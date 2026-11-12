"""Integrated S21ZPA recognition -> interval search -> qubit calibration.
Default CLI is OFFLINE. Hardware execution requires an explicit trusted adapter
and --execute. No LabRAD auto-connect and no inference of experiment return APIs.
"""
from __future__ import annotations
import argparse,csv,importlib,json,math,time,traceback
from dataclasses import dataclass,asdict
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
import numpy as np
from classify_zpa_datavault import load_model
from plan_zpa_bias import plan

@dataclass
class Settings:
    bias_step: float = .2
    visibility_threshold: float = .05
    spec_start: float = 5.5
    spec_stop: float = 4.0
    spec_step: float = -.001
    spec_amp: float = .1
    piamp_amp: float = 2.
    min_prominence_db: float = 1.
    min_interval_points: int = 3

    def validate(self):
        vals=list(asdict(self).values())
        if not all(math.isfinite(float(x)) for x in vals): raise ValueError('Non-finite settings')
        if self.bias_step<=0 or self.spec_step>=0 or self.spec_start<=self.spec_stop: raise ValueError('Invalid bias/frequency steps')
        if self.spec_amp<=0 or self.piamp_amp<=0 or self.min_prominence_db<=0 or self.min_interval_points<3: raise ValueError('Invalid amplitude/interval settings')
        if not 0<=self.visibility_threshold<=1: raise ValueError('Visibility threshold must be in [0,1]')
        if len(np.arange(self.spec_start,self.spec_stop,self.spec_step))>100000:raise ValueError('Too many spectroscopy points')


def utcnow():return datetime.now(timezone.utc).isoformat()

def serializable(x):
    if isinstance(x,np.ndarray):return serializable(x.tolist())
    if isinstance(x,np.generic):return serializable(x.item())
    if isinstance(x,float) and not math.isfinite(x):return None
    if isinstance(x,Path):return str(x)
    if isinstance(x,dict):return {str(k):serializable(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [serializable(v) for v in x]
    if x is None or isinstance(x,(str,int,float,bool)):return x
    return {'unserialized_type':type(x).__name__}

def save_json(path,data):
    path=Path(path);path.parent.mkdir(exist_ok=True,parents=True)
    # Single runner only. Atomic replace to retain last valid checkpoint.
    tmp=path.with_name(path.name+'.tmp');tmp.write_text(json.dumps(serializable(data),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');tmp.replace(path)

def bias_grid(intervals,step=.2):
    """Each interval starts at its own minimum; never steps outside or across gaps."""
    if not math.isfinite(step) or step<=0:raise ValueError('Invalid step')
    result=set();d=Decimal(str(step))
    for lo,hi in intervals:
        if not all(math.isfinite(float(v)) for v in [lo,hi]) or lo>hi:raise ValueError('Invalid interval')
        # Remove binary noise from HDF5 np.arange coordinates.
        low=Decimal(str(round(float(lo),10)));high=Decimal(str(round(float(hi),10)))
        if (high-low)/d>10000:raise ValueError('Too many bias points')
        n=int((high-low)//d)
        for i in range(n+1):result.add(float(low+i*d))
    return sorted(result)

def inspect_file(path,settings=None,model=None):
    settings=settings or Settings();settings.validate()
    r=plan(Path(path),model or load_model(),settings.min_prominence_db,settings.min_interval_points)
    allowed=r['file_normal_prediction'] and not r['file_review_required']
    intervals=[i['sampled_zpa_interval'] for i in r['intervals']] if allowed else []
    r['scan_bias_values']=bias_grid(intervals,settings.bias_step)
    r['pipeline_eligible']=bool(r['scan_bias_values'])
    r['bias_mapping']='qobj.regs.bias_z = scanned_zpa (user-confirmed)'
    r['execution_enabled']=False
    r['status']='ready_for_preflight' if r['pipeline_eligible'] else 'skipped_no_reliable_interval'
    return r


def metric_number(value,name,lo=None,hi=None):
    if isinstance(value,(bool,np.bool_)) or not isinstance(value,(float,int,np.number)):raise ValueError(name+' must be an explicit numeric metric')
    x=float(value)
    if not math.isfinite(x) or lo is not None and x<lo or hi is not None and x>hi:raise ValueError('Invalid '+name)
    return x


def preflight(adapter,bit,qobj,sq,biases,settings):
    for name in ['preflight','snapshot','restore','safe_shutdown','wait_for_bias','maximum_visibility','piamp_excited','latest_datavault_path','final_metrics']:
        if not callable(getattr(adapter,name,None)):raise TypeError('Adapter missing '+name)
    for name in ['spectroscopy','iqraw','piamp','pidf','ramsey_df','s21_dis','set_pi','t1']:
        if not callable(getattr(sq,name,None)):raise TypeError('sq missing '+name)
    permission=adapter.preflight(bit,qobj,settings,biases)
    # Adapter must check wiring, units, amplitudes, frequency limits, bias ramp,
    # settling time, readback, and rollback. Never default unspecified limits.
    if not isinstance(permission,dict) or permission.get('approved') is not True:raise ValueError('Preflight not approved')
    lo,hi=permission['bias_limits'];lo=metric_number(lo,'lower bias limit');hi=metric_number(hi,'upper bias limit')
    if lo>=hi or any(not lo<=b<=hi for b in biases):raise ValueError('Planned bias outside approved bounds')
    if permission.get('frequency_unit')!='GHz':raise ValueError('Spectroscopy frequency unit must be confirmed GHz')
    if permission.get('bias_mapping_confirmed') is not True:raise ValueError('bias_z mapping not confirmed')
    if permission.get('t1_zpa_is_selected_bias') is not True:raise ValueError('Confirm t1 zpa parameter means selected absolute bias')
    return permission


def run_bit(bit,qobj,sq,adapter,inspection,outdir,settings=None):
    """Hardware-capable API. Call only with authorized, validated live adapter.
    On success, keep the selected bias and calibration. On failure, shut down
    drive and restore adapter snapshot. No automatic retry or process timeout.
    """
    settings=settings or Settings();settings.validate();outdir=Path(outdir);outdir.mkdir(exist_ok=True,parents=True)
    result=dict(bit=bit,source_file=inspection['file'],source_id=inspection['id'],started_at=utcnow(),status='pending',attempts=[],calibration=[],metrics=None,selected_bias_z=None,settings=asdict(settings))
    checkpoint=outdir/(bit+'_result.json')
    def save():save_json(checkpoint,result)
    def event(name,state,**extra):
        with (outdir/(bit+'_events.jsonl')).open('a',encoding='utf-8') as f:
            f.write(json.dumps(serializable(dict(time=utcnow(),operation=name,state=state,**extra)),ensure_ascii=False)+'\n')
    def call(name,func,*args,**kwargs):
        event(name,'started');ret=func(*args,**kwargs);event(name,'completed');return ret
    biases=inspection.get('scan_bias_values',[])
    if not inspection.get('pipeline_eligible') or not biases:
        result['status']='skipped_no_reliable_interval';save();return result
    if inspection.get('bit')!=bit:raise ValueError('qobj bit does not match selected S21ZPA file')
    try:
        permission=preflight(adapter,bit,qobj,sq,biases,settings)
        result['preflight']=permission;snapshot=adapter.snapshot(qobj);save()
    except Exception as exc:
        result['status']='error';result['error']=dict(type=type(exc).__name__,message=str(exc));save();return result
    success=False
    try:
        freq=np.arange(settings.spec_start,settings.spec_stop,settings.spec_step)
        for bias in biases:
            attempt=dict(bias_z=bias,status='running');result['attempts'].append(attempt);save()
            event('set_bias','started',bias_z=bias)
            # Required assignment. wait_for_bias must apply/ramp actual hardware,
            # wait until stable and verify actual readback before RF experiments.
            qobj.regs.bias_z=bias
            adapter.wait_for_bias(qobj,bias)
            event('set_bias','completed',bias_z=bias)
            spec=call('spectroscopy',sq.spectroscopy,qobj,freq=freq.copy(),spec_amp=settings.spec_amp,update=True)
            iq=call('search_iqraw',sq.iqraw,qobj)
            # The real IQraw schema has stats, Is|I, Qs|I, Is|X, Qs|X;
            # visibility must be calculated from this current result handle.
            if callable(getattr(adapter,'latest_datavault_path',None)):
                attempt['iqraw_datavault_path']=adapter.latest_datavault_path()
                if not attempt['iqraw_datavault_path']:raise ValueError('Current IQraw DataVault result handle missing')
            # Always perform piamp as requested, even if visibility is weak.
            pi=call('search_piamp',sq.piamp,qobj,amp=settings.piamp_amp)
            visibility=metric_number(adapter.maximum_visibility(iq,qobj),'Maximum Visibility',0,1)
            excited=adapter.piamp_excited(pi,qobj)
            if not isinstance(excited,(bool,np.bool_)):raise ValueError('piamp_excited must return explicit bool')
            attempt.update(visibility=visibility,piamp_excited=bool(excited),status='accepted' if visibility>settings.visibility_threshold and excited else 'rejected')
            save()
            if attempt['status']=='accepted':result['selected_bias_z']=bias;break
        if result['selected_bias_z'] is None:
            result['status']='no_working_bias';return result
        b=result['selected_bias_z'];returns={}
        sequence=[
            ('pidf_df008',lambda:sq.pidf(qobj,df=.08)),
            ('pidf',lambda:sq.pidf(qobj)),
            ('ramsey_df',lambda:sq.ramsey_df(qobj)),
            ('s21_dis',lambda:sq.s21_dis(qobj)),
            ('iqraw_before_pi',lambda:sq.iqraw(qobj)),
            ('set_pi',lambda:sq.set_pi(qobj, gate='X')),
            ('set_pi_half',lambda:sq.set_pi(qobj, gate='X/2')),
            ('t1',lambda:sq.t1(qobj,zpa=b)),
            ('iqraw_final',lambda:sq.iqraw(qobj)),
        ]
        for name,func in sequence:
            if not math.isclose(float(qobj.regs.bias_z),b,abs_tol=1e-9,rel_tol=0):raise ValueError('Calibration unexpectedly changed bias_z; abort before next experiment')
            returns[name]=call(name,func)
            result['calibration'].append(dict(step=name,status='completed'));save()
        # Adapter maps actual API outputs/fit parameters and explicitly converts
        # T1/T2 to us and f01 to GHz. Missing metrics are errors, never made up.
        if not math.isclose(float(qobj.regs.bias_z),b,abs_tol=1e-9,rel_tol=0):raise ValueError('Final experiment changed bias_z')
        metrics=adapter.final_metrics(qobj,returns)
        for key in ['readout_fidelity','T1_us','T2_us','qubit_frequency_GHz']:
            if key not in metrics:raise ValueError('Final metric missing '+key)
            metrics[key]=metric_number(metrics[key],key,0,1 if key=='readout_fidelity' else None)
            if key!='readout_fidelity' and metrics[key]<=0:raise ValueError('Nonpositive '+key)
        if not isinstance(metrics.get('T2_type'),str) or not metrics['T2_type']:raise ValueError('Must specify T2_type (e.g. Ramsey T2*)')
        if not math.isclose(float(qobj.regs.bias_z),b,abs_tol=1e-9,rel_tol=0):raise ValueError('Metric extraction unexpectedly changed bias_z')
        adapter.wait_for_bias(qobj,b)
        result['metrics']=metrics;result['status']='success';success=True
        return result
    except BaseException as exc:
        result['status']='interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'error'
        result['error']=dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc());save()
        if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
        return result
    finally:
        # Always attempt RF shutdown, then restore on failure. Log cleanup errors.
        cleanup=[]
        try:adapter.safe_shutdown(qobj)
        except BaseException as e:cleanup.append('safe_shutdown: '+repr(e))
        if not success or cleanup:
            try:adapter.restore(qobj,snapshot)
            except BaseException as e:cleanup.append('restore: '+repr(e))
        if cleanup:result['cleanup_errors']=cleanup;result['status']='cleanup_failed'
        result['finished_at']=utcnow();save()


def write_summary(path,results):
    fields=['bit','source_id','status','selected_bias_z','readout_fidelity','T1_us','T2_us','T2_type','qubit_frequency_GHz']
    with Path(path).open('w',encoding='utf-8-sig',newline='') as fo:
        w=csv.DictWriter(fo,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for r in results:w.writerow(dict(r,**(r.get('metrics') or {})))


def run_manifest(manifest,adapter=None,execute=False):
    """One explicit current file per bit; no historical/latest-file guessing."""
    manifest_path=Path(manifest).resolve();cfg=json.loads(manifest_path.read_text(encoding='utf-8'));settings=Settings(**cfg.get('settings',{}));settings.validate()
    out=Path(cfg.get('out','zpa_pipeline_output'));out=out if out.is_absolute() else manifest_path.parent/out
    if out.exists() and any(out.iterdir()):raise ValueError('Output directory must be empty to prevent accidental rerun or overwriting a prior run')
    out.mkdir(exist_ok=True,parents=True);entries=cfg['qubits'];bits=[e['bit'] for e in entries]
    if len(bits)!=len(set(bits)):raise ValueError('One current source file per bit is required')
    model_path=Path(cfg.get('model','zpa_v2/model.json'));model_path=model_path if model_path.is_absolute() else manifest_path.parent/model_path
    model=load_model(model_path);inspections=[]
    # Prepare everything before any hardware call.
    for e in entries:
        source=Path(e['s21zpa_file']);source=source if source.is_absolute() else manifest_path.parent/source
        r=inspect_file(source,settings,model)
        if r['bit']!=e['bit']:raise ValueError('File/bit mismatch')
        inspections.append(r)
    save_json(out/'offline_plan.json',dict(created_at=utcnow(),settings=asdict(settings),entries=inspections,execution_enabled=execute))
    if not execute:return inspections
    if not cfg.get('datavault_root'):raise ValueError('Manifest requires datavault_root for live result discovery')
    if adapter is None:raise ValueError('Execution requires explicit adapter')
    results=[]
    for entry,inspection in zip(entries,inspections):
        if not inspection['pipeline_eligible']:
            r=dict(bit=entry['bit'],source_id=inspection['id'],status='skipped_no_reliable_interval',metrics=None)
        else:
            try:
                qobj,sq=adapter.get_context(entry['bit'])
                r=run_bit(entry['bit'],qobj,sq,adapter,inspection,out,settings)
            except BaseException as exc:
                checkpoint=out/(entry['bit']+'_result.json')
                r=json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else dict(bit=entry['bit'],source_id=inspection['id'],status='interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'error',metrics=None,error=str(exc))
                results.append(r);save_json(out/'results.json',results);write_summary(out/'summary.csv',results)
                if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
                break
        results.append(r);save_json(out/'results.json',results);write_summary(out/'summary.csv',results)
        # Never continue manipulating further bits after a failed cleanup.
        if r['status'] in ['cleanup_failed','error']:break
    return results


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('manifest',type=Path);ap.add_argument('--execute',action='store_true');ap.add_argument('--adapter',help='Trusted Python module exporting create_adapter(config). Imports execute code.');a=ap.parse_args()
    if a.execute and not a.adapter:ap.error('--execute needs --adapter; use offline mode first')
    adapter=None
    if a.execute:
        cfg=json.loads(a.manifest.read_text(encoding='utf-8'));adapter=importlib.import_module(a.adapter).create_adapter(cfg.get('adapter_config',{}))
    results=run_manifest(a.manifest,adapter,a.execute)
    print(json.dumps(serializable([dict(bit=r['bit'],status=r.get('status','offline_planned'),bias_values=r.get('scan_bias_values'),metrics=r.get('metrics')) for r in results]),ensure_ascii=False,indent=2))
    if a.execute and any(r.get('status')!='success' for r in results):raise SystemExit(2)
if __name__=='__main__':main()
