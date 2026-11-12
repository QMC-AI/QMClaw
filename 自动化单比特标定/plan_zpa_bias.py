"""Offline S21ZPA usable-interval extraction. NEVER connects to hardware.
Local dip support != file-level normality != qubit spectroscopy success.
"""
import argparse,base64,csv,json,re,struct
from pathlib import Path
import h5py,numpy as np
from scipy.signal import find_peaks
from classify_zpa_datavault import analyze,load_model

def scalar_parameter(attrs,key):
 """Decode only primitive LabRAD scalar types, no arbitrary deserialization."""
 raw=attrs.get(key)
 if raw is None:return None
 if isinstance(raw,bytes):raw=raw.decode()
 if not str(raw).startswith('data:application/labrad;base64,'):return None
 try:
  b=base64.urlsafe_b64decode(raw.split(',',1)[1]);n=struct.unpack('>I',b[:4])[0];tag=b[4:4+n].decode();size=struct.unpack('>I',b[4+n:8+n])[0];data=b[8+n:]
  if len(data)!=size:return None
  if tag=='b' and size==1:return bool(data[0])
  if tag=='i' and size==4:return struct.unpack('>i',data)[0]
  if tag.startswith('v') and size==8:return struct.unpack('>d',data)[0]
 except (ValueError,struct.error,UnicodeError):return None
 return None

def runs(mask):
 start=None
 for i,v in enumerate(list(mask)+[False]):
  if v and start is None:start=i
  if not v and start is not None:yield start,i;start=None

def plan(path,model,min_prominence_db=1.,min_points=3,bias_map=None):
 classification=analyze(path,model)
 with h5py.File(path,'r') as h:
  ds=h['DataVault'];a=ds[()];at=dict(ds.attrs)
 bit=re.search(r'q\d+_\d+',path.name).group();f=np.unique(a['f0']);z=np.unique(a['f1'])
 m=np.full((len(f),len(z)),np.nan);m[np.searchsorted(f,a['f0']),np.searchsorted(z,a['f1'])]=a['f2']
 db=20*np.log10(np.where(m>0,m,np.nan));points=[]
 for j,bias in enumerate(z):
  valid=np.isfinite(db[:,j]);k=None;prom=0.;reason='incomplete_frequency_column'
  if valid.all() and len(f)>=3:
   k=int(np.argmin(db[:,j]));ps,props=find_peaks(-db[:,j],prominence=0)
   if k in ps:prom=float(props['prominences'][np.where(ps==k)[0][0]])
   reason='supported_interior_dip' if 0<k<len(f)-1 and prom>=min_prominence_db else ('frequency_window_edge' if k in [0,len(f)-1] else 'weak_or_unresolved_dip')
  points.append(dict(zpa=float(bias),supported=reason=='supported_interior_dip',reason=reason,resonance_frequency_native=float(f[k]) if k is not None else None,prominence_db=prom,frequency_index=k))
 # Avoid bridging unresolved gaps or branches jumping more than 30% of window.
 intervals=[]
 for start,end in runs([p['supported'] for p in points]):
  boundaries=[start]
  for j in range(start+1,end):
   if abs(points[j]['frequency_index']-points[j-1]['frequency_index'])>max(2,.3*(len(f)-1)):boundaries.append(j)
  boundaries.append(end)
  for s,e in zip(boundaries,boundaries[1:]):
   if e-s<min_points:continue
   zs=z[s:e];freqs=np.array([p['resonance_frequency_native'] for p in points[s:e]])
   # Operational stable point: low local slope with margins on both axes.
   slopes=np.abs(np.gradient(freqs,zs));scaled=slopes/max(np.ptp(f),1e-12)
   interior=np.arange(s+1,e-1);scores=[]
   for j in interior:
    edge=min(points[j]['frequency_index'],len(f)-1-points[j]['frequency_index'])/max(len(f)-1,1)
    scores.append(points[j]['prominence_db']/(1+points[j]['prominence_db'])+edge-2*scaled[j-s])
   best=int(interior[int(np.argmax(scores))]);p=points[best]
   intervals.append(dict(sampled_zpa_interval=[float(z[s]),float(z[e-1])],point_count=e-s,candidate_zpa=p['zpa'],readout_resonance_frequency_native=p['resonance_frequency_native'],selection='interior_low_slope_with_dip_contrast_not_certified_sweetpoint',boundary_resolution=float(np.median(np.diff(zs))),median_prominence_db=float(np.median([q['prominence_db'] for q in points[s:e]]))))
 normal=classification.get('normal_prediction',False)
 r=dict(id=classification['id'],file=path.name,bit=bit,file_normal_prediction=normal,file_normal_score=classification['normal_score'],file_review_required=classification.get('review_required',False),stored_bias_z=scalar_parameter(at,'Param.'+bit+'.bias_z'),stored_bias=scalar_parameter(at,'Param.bias'),isDbias=scalar_parameter(at,'Param.isDbias'),fixed_readout_fc=scalar_parameter(at,'Param.fixed_readout_fc'),stored_f10_native=scalar_parameter(at,'Param.'+bit+'.f10'),stored_fread_native=scalar_parameter(at,'Param.'+bit+'.fread'),frequency_unit=str(at.get('Independent0.unit','')) or 'unconfirmed_native',zpa_unit=str(at.get('Independent1.unit','')) or 'unconfirmed_native',intervals=intervals,points=points,bias_z_proposal=None,execution_enabled=False,status='needs_frequency_rescan' if not intervals else 'mapping_confirmation_required',warnings=['HDF5 metadata does not prove scanned zpa equals absolute bias_z.','Supported sampled intervals are not calibrated continuous safe bias ranges.','Candidate is not a confirmed qubit sweet point; validate locally before spectroscopy.'])
 if not normal:r['status']='file_not_normal_candidate';r['warnings'].append('Do not execute based on a non-normal file.')
 if intervals and normal and not classification.get('review_required',False):
  chosen=max(intervals,key=lambda q:(q['point_count'],q['median_prominence_db']));r['selected_candidate']=chosen
  if bias_map:
   if not bias_map.get('mapping_confirmed') or not bias_map.get('source'):raise ValueError('Mapping requires mapping_confirmed=true and source evidence')
   scale=float(bias_map['scale']);offset=float(bias_map['offset']);limits=bias_map['bias_z_limits'];b=scale*chosen['candidate_zpa']+offset
   if not np.isfinite([scale,offset,b,*limits]).all() or len(limits)!=2 or limits[0]>=limits[1]:raise ValueError('Invalid bias mapping or limits')
   if limits[0]<=b<=limits[1]:r['bias_z_proposal']=b;r['status']='offline_proposal_requires_preflight'
   else:r['status']='bias_z_outside_configured_limits'
 return r

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('directory',nargs='?',default='.');ap.add_argument('--out',default='zpa_bias_plan');ap.add_argument('--mapping',type=Path);ap.add_argument('--min-prominence-db',type=float,default=1.);ap.add_argument('--min-points',type=int,default=3);a=ap.parse_args()
 if a.min_prominence_db<=0 or a.min_points<3:ap.error('Positive prominence and min-points >=3 required')
 mappings=json.loads(a.mapping.read_text()) if a.mapping else {};out=Path(a.out);out.mkdir(parents=True,exist_ok=True);model=load_model();rows=[]
 for p in sorted(Path(a.directory).glob('*.hdf5')):
  bit=re.search(r'q\d+_\d+',p.name).group();rows.append(plan(p,model,a.min_prominence_db,a.min_points,mappings.get(bit)))
 (out/'plan.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
 fields=['id','bit','file_normal_prediction','file_normal_score','stored_bias_z','isDbias','status','intervals','candidate_zpa','bias_z_proposal','frequency_unit']
 with (out/'intervals.csv').open('w',encoding='utf-8-sig',newline='') as fo:
  w=csv.DictWriter(fo,fieldnames=fields,extrasaction='ignore');w.writeheader()
  for r in rows:
   w.writerow(dict(r,intervals=json.dumps([q['sampled_zpa_interval'] for q in r['intervals']]),candidate_zpa=r.get('selected_candidate',{}).get('candidate_zpa')))
 print('FILES',len(rows),'NORMAL',sum(r['file_normal_prediction'] for r in rows),'NORMAL_WITH_SUPPORTED_INTERVAL',sum(r['file_normal_prediction'] and bool(r['intervals']) for r in rows),'BIAS_PROPOSALS',sum(r['bias_z_proposal'] is not None for r in rows));print('Execution disabled. No hardware calls.')
 for r in [r for r in rows if r['file_normal_prediction']][:12]:print(r['id'],r['bit'],[q['sampled_zpa_interval'] for q in r['intervals']],'candidate',r.get('selected_candidate',{}).get('candidate_zpa'),'stored_bias_z',r['stored_bias_z'])
if __name__=='__main__':main()
