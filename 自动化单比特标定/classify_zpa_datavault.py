"""S21ZPA v2: supervised value/shape classification, with uncertainty.
Model does NOT read IDs as features and does NOT look up manual labels.
Use train_zpa_model.py for held-out validation, not training predictions.
"""
import argparse,csv,json
from pathlib import Path
import numpy as np
from zpa_features import extract,VERSION

DEFAULT_MODEL=Path(__file__).parent/'zpa_v2'/'model.json'

def load_model(path=DEFAULT_MODEL):
 model=json.loads(Path(path).read_text(encoding='utf-8'))
 if model['version']!=VERSION:raise ValueError('Feature/model version mismatch')
 return model

def predict_probability(features,model):
 x=np.array([features[k] for k in model['features']],dtype=float)
 if not np.isfinite(x).all():raise ValueError('Non-finite features')
 probabilities=[]
 for tree in model['trees']:
  node=0
  while tree['left'][node]!=-1:
   node=tree['left'][node] if x[tree['feature'][node]]<=tree['threshold'][node] else tree['right'][node]
  probabilities.append(tree['positive_probability'][node])
 return float(np.mean(probabilities))

def analyze(path,model=None):
 path=Path(path);model=load_model() if model is None else model
 features,meta=extract(path);p=predict_probability(features,model)
 hard_reasons=[]
 if not meta['has_valid_amplitude']:hard_reasons.append('no_valid_positive_amplitude')
 if meta['n_freq']<2 or meta['n_zpa']<3:hard_reasons.append('insufficient_2d_sampling')
 low,high=model.get('review_band',[.35,.65]);normal=p>=model['threshold'] and not hard_reasons
 decision='normal_candidate' if normal else 'not_normal_candidate'
 if low<=p<=high and not hard_reasons:decision='review'
 return dict(id=path.name.split(' - ')[0],file=path.name,decision=decision,normal_prediction=normal,normal_score=p,review_required=decision=='review',reasons=hard_reasons or ['supervised_shape_model_not_a_physical_certification'],**meta)

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('directory',nargs='?',default='.');ap.add_argument('--out',default='zpa_v2/predictions');ap.add_argument('--model',type=Path,default=DEFAULT_MODEL);a=ap.parse_args()
 out=Path(a.out);out.mkdir(exist_ok=True,parents=True);model=load_model(a.model);rows=[]
 for p in sorted(Path(a.directory).glob('*.hdf5')):
  try:r=analyze(p,model)
  except Exception as e:r=dict(id=p.name.split(' - ')[0],file=p.name,decision='error',reasons=[type(e).__name__+': '+str(e)])
  rows.append(r)
 (out/'classification.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
 # Threshold predictions and confidence-filtered auto-accepts are distinct.
 predicted=[r['id'] for r in rows if r.get('normal_prediction',False)]
 confident=[r['id'] for r in rows if r['decision']=='normal_candidate']
 for name,ids in [('normal_ids.txt',predicted),('auto_accept_ids.txt',confident),('review_ids.txt',[r['id'] for r in rows if r.get('review_required')])]:
  (out/name).write_text('\n'.join(ids)+'\n',encoding='utf-8')
 fields=sorted(set().union(*(r.keys() for r in rows)))
 with (out/'classification.csv').open('w',encoding='utf-8-sig',newline='') as fo:
  w=csv.DictWriter(fo,fieldnames=fields);w.writeheader();w.writerows(rows)
 print(json.dumps(dict(scanned=len(rows),threshold_normal=len(predicted),auto_accept=len(confident),review=sum(r.get('review_required',False) for r in rows),errors=sum(r['decision']=='error' for r in rows)),indent=2));print('NORMAL_IDS',','.join(predicted))
if __name__=='__main__':main()
