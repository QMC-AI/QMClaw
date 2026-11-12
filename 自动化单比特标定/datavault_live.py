"""Read-only DataVault result watcher and automatic experiment metrics.
Designed for the observed LabRAD HDF5 schema in the 20260822 device directory.
It never starts or stops hardware; the caller invokes sq methods.
"""
from __future__ import annotations
import re,time
from pathlib import Path
import h5py,numpy as np
from scipy.optimize import curve_fit


def bit_from_name(name):
 m=re.search(r' - (q[^ ]+)%c ',name);return m.group(1) if m else None

def wait_for_result(root,bit,kind,since,timeout=120,poll=0.25):
 root=Path(root);deadline=time.time()+timeout
 while time.time()<deadline:
  candidates=[]
  for p in root.rglob(f'* - {bit}%c {kind}.hdf5'):
   try:
    if p.stat().st_mtime>=since-1 and p.stat().st_size>0:candidates.append(p)
   except OSError:pass
  if candidates:
   p=max(candidates,key=lambda x:x.stat().st_mtime)
   try:
    with h5py.File(p,'r') as h:a=h['DataVault'][()];attrs=dict(h['DataVault'].attrs)
    if len(a):return {'path':str(p),'kind':kind,'data':a,'attrs':attrs,'observed_at':time.time()}
   except (OSError,KeyError):pass
  time.sleep(poll)
 raise TimeoutError(f'No new {kind} result for {bit} under {root} after {timeout}s')

def _num(x):return np.asarray(x,dtype=float)

def iqraw_visibility(result):
 a=result['data'];names=a.dtype.names
 required=['f1','f2','f3','f4']
 if not all(n in names for n in required):raise ValueError('IQraw requires f1..f4: Is|I,Qs|I,Is|X,Qs|X')
 c0=np.column_stack([_num(a['f1']),_num(a['f2'])]);c1=np.column_stack([_num(a['f3']),_num(a['f4'])]);
 c0=c0[np.isfinite(c0).all(axis=1)];c1=c1[np.isfinite(c1).all(axis=1)]
 if len(c0)<10 or len(c1)<10:raise ValueError('IQraw has too few finite samples')
 mu0=c0.mean(axis=0);mu1=c1.mean(axis=0);distance=float(np.linalg.norm(mu1-mu0));s0=float(np.sqrt(np.mean(np.sum((c0-mu0)**2,axis=1))));s1=float(np.sqrt(np.mean(np.sum((c1-mu1)**2,axis=1))))
 # Bounded cluster-separation visibility. Keep components for lab comparison.
 visibility=distance/max(distance+2*(s0+s1),1e-30)
 return {'Maximum Visibility':float(visibility),'visibility_definition':'distance/(distance+2*(rms_spread0+rms_spread1))','distance01':distance,'rms_spread0':s0,'rms_spread1':s1,'n0':len(c0),'n1':len(c1)}

def pipulse_excitation(result,min_contrast=.05,min_peak=.5):
 a=result['data'];p=np.asarray(a['f6'],float);x=np.asarray(a['f0'],float);m=np.isfinite(p)&np.isfinite(x)
 if m.sum()<4:raise ValueError('PiPulse has too few finite P1 points')
 p=p[m];x=x[m];order=np.argsort(x);p=p[order];x=x[order];edge=max(1,len(p)//8);baseline=float(np.median(np.r_[p[:edge],p[-edge:]]));peak=float(np.max(p));trough=float(np.min(p));contrast=peak-baseline;ptp=peak-trough
 # A genuine response must rise above baseline and reach an appreciable P1.
 excited=bool(contrast>=min_contrast and peak>=min_peak and ptp>=min_contrast)
 return {'piamp_excited':excited,'P1_peak':peak,'P1_trough':trough,'P1_baseline':baseline,'P1_contrast':contrast,'P1_peak_to_peak':ptp,'piamp_at_max':float(x[np.argmax(p)])}

def _fit_exp(x,y):
 def fun(t,c,a,tau):return c+a*np.exp(-t/max(tau,1e-12))
 p,_=curve_fit(fun,x,y,p0=[float(y[-1]),float(y[0]-y[-1]),max(np.ptp(x)/2,1)],maxfev=30000);pred=fun(x,*p);return float(abs(p[2])),float(np.sqrt(np.mean((pred-y)**2)))

def extract_calibration_metrics(results):
 out={}
 if 't1' in results:
  a=results['t1']['data'];x=np.asarray(a['f0'],float);labels=[str(results['t1']['attrs'].get(f'Dependent{i}.label','')) for i in range(10)];pi=next((i for i,v in enumerate(labels) if 'P1' in v),None);y=np.asarray(a['f'+str(pi+2 if pi is not None else 1)],float);m=np.isfinite(x)&np.isfinite(y)&(x>=0);tau,rmse=_fit_exp(x[m],y[m]);out.update(T1_us=tau,T1_fit_rmse=rmse)
 if 'ramsey' in results:
  a=results['ramsey']['data'];x=np.asarray(a['f0'],float);labels=[str(results['ramsey']['attrs'].get(f'Dependent{i}.label','')) for i in range(10)];pi=next((i for i,v in enumerate(labels) if 'P1' in v),None);y=np.asarray(a['f'+str(pi+2 if pi is not None else 1)],float);m=np.isfinite(x)&np.isfinite(y); # envelope fit omitted unless lab confirms signal model
  out['T2_status']='requires_decay_fit_from_Ramsey';out['T2_type']='Ramsey T2*';out['T2_us']=None
 if 's21_dis' in results:
  a=results['s21_dis']['data'];labels=[str(results['s21_dis']['attrs'].get(f'Dependent{i}.label','')) for i in range(12)];j=next((i for i,v in enumerate(labels) if 'distance' in v.lower() and '01' in v.lower()),None);out['distance01_median']=float(np.median(a['f'+str(j+1)])) if j is not None else None
 return out
