"""Value/shape features for LabRAD S21ZPA; no filename, ID or absolute frequency.
ZPA is the independent bias axis, not an S21 acceptance amplitude range.
"""
import h5py
import numpy as np
from scipy.ndimage import zoom
from scipy.signal import find_peaks

VERSION='zpa-shape-v2'

def extract(path):
 with h5py.File(path,'r') as h:
  ds=h['DataVault']; a=ds[()]
  frequency_unit=str(ds.attrs.get('Independent0.unit','')) or 'native_unconfirmed'
  labels=[str(ds.attrs.get('Independent%d.label'%i,'')) for i in range(2)]
  if labels!=['freq','zpa']: raise ValueError('Unsupported independent labels: '+str(labels))
  f=np.unique(a['f0']);z=np.unique(a['f1'])
  m=np.full((len(f),len(z)),np.nan)
  m[np.searchsorted(f,a['f0']),np.searchsorted(z,a['f1'])]=a['f2']
 finite=np.isfinite(m)&(m>0); missing=1-float(finite.mean())
 has_signal=bool(finite.any())
 db=np.where(finite,20*np.log10(np.maximum(m,1e-30)),np.nan)
 fill=float(np.nanmedian(db)) if has_signal else 0.
 db=np.where(np.isfinite(db),db,fill)
 q05,q50,q95=np.percentile(db,[5,50,95]);span=max(q95-q05,.01)
 norm=np.clip((db-q50)/span,-3,3)
 nfreq,nz=len(f),len(z)
 feats={}
 def put(k,x):feats[k]=float(x) if np.isfinite(x) else 0.
 def stats(prefix,x):
  x=np.asarray(x).ravel()
  for k,v in zip(['mean','std','p10','median','p90'],[np.mean(x),np.std(x),*np.percentile(x,[10,50,90])]):put(prefix+'_'+k,v)
 put('log_nfreq',np.log1p(nfreq));put('nz',nz);put('missing_fraction',missing);put('z_min',z.min());put('z_max',z.max());put('z_span',np.ptp(z));put('amp_span_db',span)
 # Frequency normalized to its own measured window, retaining sampling density.
 if nfreq>1:put('relative_frequency_step',np.median(np.diff(f))/max(np.ptp(f),1e-30))
 else:put('relative_frequency_step',1.)
 idx=np.argmin(db,axis=0); ridge=idx/max(nfreq-1,1)
 stats('ridge',ridge);put('ridge_excursion',np.ptp(ridge));put('ridge_edge_fraction',np.mean((idx==0)|(idx==nfreq-1)))
 for order in [1,2]:
  diff=np.diff(ridge,n=order)
  stats('ridge_diff%d'%order,diff if diff.size else [0])
 good=[];prom=[]
 for j,k in enumerate(idx):
  ps,pr=find_peaks(-db[:,j],prominence=0)
  p=float(pr['prominences'][np.where(ps==k)[0][0]]) if k in ps else 0.
  good.append(0<k<nfreq-1 and p>=1);prom.append(p)
 put('resolved_dip_fraction',np.mean(good));stats('dip_prominence',prom)
 # Flat, weak, truncated and periodic response descriptors, without forcing one arc.
 for axis,name in [(0,'frequency'),(1,'bias')]:
  d=np.diff(norm,axis=axis)
  stats(name+'_diff',d if d.size else [0])
  stats(name+'_variance',np.std(norm,axis=axis))
  if norm.shape[axis]>1:
   d2=np.diff(norm,n=2,axis=axis)
   stats(name+'_curvature',d2 if d2.size else [0])
  else:stats(name+'_curvature',[0])
 if nz>=3:
  zz=(z-z.mean())/max(np.ptp(z),1e-9)
  for degree in [1,2,3,4]:
   if nz>degree:
    coef=np.polyfit(zz,ridge,degree); pred=np.polyval(coef,zz)
    put('ridge_fit%d_rmse'%degree,np.sqrt(np.mean((pred-ridge)**2)))
   else:put('ridge_fit%d_rmse'%degree,1)
  put('ridge_quadratic_coefficient',np.polyfit(zz,ridge,2)[0])
 else:
  for degree in [1,2,3,4]:put('ridge_fit%d_rmse'%degree,1)
  put('ridge_quadratic_coefficient',0)
 # Normalized shape thumbnails use coordinate interpolation, not absolute frequencies.
 # Missing cells are imputed above, with missing_fraction retained as a feature.
 target_f=np.linspace(f.min(),f.max(),16);target_z=np.linspace(z.min(),z.max(),16)
 tmp=np.array([np.interp(target_f,f,norm[:,j]) for j in range(nz)]).T
 image=np.array([np.interp(target_z,z,row) for row in tmp])
 for i,val in enumerate(image.ravel()):put('shape_%03d'%i,val)
 metadata={'n_freq':nfreq,'n_zpa':nz,'grid_fraction':1-missing,'zpa_min':float(z.min()),'zpa_max':float(z.max()),'frequency_min_native':float(f.min()),'frequency_max_native':float(f.max()),'has_valid_amplitude':has_signal,'frequency_unit':frequency_unit,'resolved_dip_fraction':float(np.mean(good)),'ridge_excursion_fraction':float(np.ptp(ridge))}
 return feats,metadata
