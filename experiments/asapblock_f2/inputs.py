"""Exact paired input families and result-independent F2 selections."""
from collections import Counter,defaultdict
from fractions import Fraction as F
from itertools import product
from pathlib import Path
import math
import random
import sys
import common as C
sys.path.insert(0,str(C.RUNTIME))
import generator as frozen
from generator_support import stable_seed,_load_exact_energy_model
from experiments.ab_v7_2 import data
GENERATION_DIAGNOSTICS={}

def ftext(x):return str(F(x))

def seed_for(family,m,n,uc,index):
 offset={'main':0,'scale':1000,'numeric_scale':2000,'robustness':3000}[family]
 return stable_seed(C.PROTOCOL['seed']+offset,m,n,F(uc),index)

def bone(family,m,n,uc,index,periods=None):
 gen=dict(C.PROTOCOL['generator'])
 if periods:gen.update(period_min=periods[0],period_max=periods[1])
 seed=seed_for(family,m,n,uc,index)
 wid=f'{family}:m{m}:n{n}:uc{F(uc)}:i{index}'
 from global_task_generator import UUniFastDiscard
 code=UUniFastDiscard.generate.__code__;attempts=[];previous=sys.getprofile()
 def observe(frame,event,arg):
  if frame.f_code is code and event=='return':
   attempts.append({'trials':frame.f_locals.get('trial',-1)+1,'accepted':arg is not None})
  if previous is not None:previous(frame,event,arg)
 try:
  sys.setprofile(observe)
  tasks,trials=frozen.skeleton(seed,m,n,ftext(uc),gen)
 except Exception as exc:
  GENERATION_DIAGNOSTICS[wid]={'seed':seed,'status':'FAILED','reason':str(exc),'utilization_calls':attempts}
  raise
 finally:sys.setprofile(previous)
 GENERATION_DIAGNOSTICS[wid]={'seed':seed,'status':'GENERATED','rounding_attempts':trials,
  'rounding_discards':trials-1,'utilization_attempts':sum(a['trials'] for a in attempts),
  'utilization_discards':sum(a['trials']-int(a['accepted']) for a in attempts)}
 rng=random.Random(seed^0xC0A71A)
 deadlines=[max(t['C'],rng.randint(math.ceil(F(7,10)*t['T']),t['T'])) for t in tasks]
 weights=_load_exact_energy_model(frozen.ROOT/'system_config_unified_template.yml')
 return {'cluster_id':wid,'tasks':tasks,'seed':seed,'index':index,'m':m,'n':n,
  'uc':ftext(uc),'deadlines':deadlines,'rounding_trials':trials,
  'weights':[weights[t['workload']] for t in tasks],'period_min':gen['period_min'],'period_max':gen['period_max']}

def case(b,ue,gap,deadline='implicit',family=None,h=None,factor=1,original_scale=False):
 tasks=b['tasks'];n=b['n'];ue=F(ue)
 weights=b['weights']
 if h is not None:
  rng=random.Random(int(C.digest(['heterogeneity',b['cluster_id']])[:16],16))
  raw=[rng.randint(C.PROTOCOL['mechanism']['weight_min'],C.PROTOCOL['mechanism']['weight_max']) for _ in tasks]
  weights=[1-F(h)+F(h)*w for w in raw]
 demand=sum(F(t['C'],t['T'])*p for t,p in zip(tasks,weights))
 powers=[p*ue/demand for p in weights]
 ds=[t['T'] for t in tasks] if deadline=='implicit' else b['deadlines']
 order=sorted(range(n),key=lambda i:(tasks[i]['T'],i))
 rs=[(tasks[i]['C']*factor,ds[i]*factor,tasks[i]['T']*factor,powers[i]) for i in order]
 horizon=max(r[1] for r in rs);gap*=factor
 beta=[gap*(l//gap) for l in range(horizon+1)]
 mean=sum(powers)/n
 cv=math.sqrt(float(sum((p-mean)**2 for p in powers)/n))/float(mean)
 meta={'family':family or b['cluster_id'].split(':')[0],'cluster_id':b['cluster_id'],
  'sample':b['index'],'seed':b['seed'],'target_uc':b['uc'],
  'actual_uc':str(sum(F(t['C'],t['T']) for t in tasks)/b['m']),
  'target_ue':str(ue),'actual_ue':str(ue),'power_multiplier':str(ue/demand),
  'original_task_ids':order,'workloads':[tasks[i]['workload'] for i in order],
  'deadline_mode':deadline,'harvest_gap':gap,'harvest_rate':'1','time_factor':factor,
  'heterogeneity_h':None if h is None else str(F(h)),'power_cv':cv,
  'period_min':b['period_min']*factor,'period_max':b['period_max']*factor,
  'rounding_trials':b['rounding_trials'],'scale_original_grid':original_scale,
  'priority':'RM','generator':'bounded_UUniFast_Discard_compensated'}
 name=b['cluster_id']+':'+C.digest([str(ue),gap,deadline,h,factor,family])[:16]
 c=data.make_case(name,rs,b['m'],beta,metadata=meta,cohort=meta['family'])
 validate(c)
 return c

def validate(c):
 data.validate_case(c);m=c['model'];meta=c['metadata'];ts=m['tasks']
 if m['capacity'] is not None or m['release_floor']!=0 or m['background_power']!=0:raise ValueError('F2 model mismatch')
 if [t['T'] for t in ts]!=sorted(t['T'] for t in ts):raise ValueError('Not RM ordered')
 actual=sum(F(t['C'],t['T']) for t in ts)/m['processors']
 if abs((actual-F(meta['target_uc']))*m['processors'])>F(C.PROTOCOL['generator']['tolerance_total']):raise ValueError('CPU tolerance')
 energy=sum(F(t['power'],m['energy_scale'])*F(t['C'],t['T']) for t in ts)
 if energy!=F(meta['target_ue']):raise ValueError('Energy utilization changed')
 g=meta['harvest_gap'];scale=m['energy_scale']
 if m['beta']!=[scale*g*(l//g) for l in range(max(t['D'] for t in ts)+1)]:raise ValueError('Supply mismatch')
 if not all(meta['period_min']<=t['T']<=meta['period_max'] for t in ts):raise ValueError('Period range')

def main_variants(uc):
 p=C.PROTOCOL['main'];out=set()
 for ue,d in product(p['base_ue'],p['deadlines']):out.add((ftext(ue),d,1))
 if F(uc) in map(F,p['energy_uc']):
  for ue,d in product(p['energy_ue'],p['deadlines']):out.add((ftext(ue),d,1))
  for ue,d,g in product(p['base_ue'],p['deadlines'],p['gaps']):out.add((ftext(ue),d,g))
 return sorted(out,key=lambda v:(F(v[0]),v[1],v[2]))

def choose(cases,key,count,salt,used=None):
 groups=defaultdict(list)
 for c in cases:groups[key(c)].append(c)
 out=[]
 for cell in sorted(groups,key=str):
  found=0
  for c in sorted(groups[cell],key=lambda x:C.digest([salt,x['case_id']])):
   cluster=c['metadata']['cluster_id']
   if used is not None and cluster in used:continue
   out.append(c);found+=1
   if used is not None:used.add(cluster)
   if found==count:break
  if found!=count:raise ValueError(f'Insufficient selection in {cell}')
 return out

def build():
 p=C.PROTOCOL;sets={k:[] for k in ['main','mechanism','scale','numeric_scale','robustness']};bones={}
 for m,uc,index in product(p['main']['processors'],p['main']['uc'],range(p['main']['samples'])):
  b=bone('main',m,2*m,uc,index);bones[b['cluster_id']]=b
  sets['main'] += [case(b,ue,g,d) for ue,d,g in main_variants(uc)]
 mp=p['mechanism']
 for uc in mp['uc']:
  bs=[b for b in bones.values() if b['m']==4 and F(b['uc'])==F(uc)]
  bs=sorted(bs,key=lambda b:C.digest(['F2-mechanism-bones',b['cluster_id']]))[:mp['samples_per_uc']]
  for b,ue,g,h in product(bs,mp['ue'],mp['gaps'],mp['h']):
   sets['mechanism'].append(case(b,ue,g,family='mechanism',h=h))
 sp=p['scale'];cells={(m,m*q,uc) for m,q,uc in product(sp['processors'],sp['multipliers'],sp['uc'])}
 cells|={(sp['extra_m'],n,uc) for n,uc in product(sp['extra_n'],sp['uc'])}
 for m,n,uc in sorted(cells):
  for i in range(sp['samples']):
   sets['scale'].append(case(bone('scale',m,n,uc,i),'0.40',1,original_scale=n in (2*m,4*m)))
 np=p['numeric_scale']
 for uc,i in product(np['uc'],range(np['samples'])):
  b=bone('numeric_scale',4,8,uc,i)
  for factor in np['factors']:sets['numeric_scale'].append(case(b,'0.40',np['gap'],factor=factor))
 rp=p['robustness']
 for m,uc,i in product(rp['processors'],rp['uc'],range(rp['samples'])):
  b=bone('robustness',m,2*m,uc,i,(rp['period_min'],rp['period_max']))
  for d in p['main']['deadlines']:sets['robustness'].append(case(b,rp['ue'],rp['gap'],d))
 timing=p['timing'];used=set()
 def main_cell(c):
  md=c['metadata'];return(c['model']['processors'],md['target_uc'],md['target_ue'],md['deadline_mode'])
 base=[c for c in sets['main'] if F(c['metadata']['target_uc']) in map(F,timing['base_uc']) and F(c['metadata']['target_ue']) in map(F,p['main']['base_ue']) and c['metadata']['harvest_gap']==1]
 extra=[c for c in sets['main'] if F(c['metadata']['target_uc']) in map(F,timing['gap_uc']) and F(c['metadata']['target_ue']) in map(F,p['main']['base_ue']) and c['metadata']['harvest_gap']==timing['gap']]
 tm=choose(base,main_cell,timing['base_samples'],'F2-timing-main-1',used)
 tm+=choose(extra,main_cell,timing['gap_samples'],'F2-timing-main-2',used)
 assert len(tm)==len(used)==192
 def scale_cell(c):return(c['model']['processors'],len(c['model']['tasks']),c['metadata']['target_uc'])
 ts=choose([c for c in sets['scale'] if c['metadata']['scale_original_grid']],scale_cell,3,'F2-timing-scale')
 sets['timing']=tm+ts
 def mechanism_cell(c):
  md=c['metadata'];return(md['target_uc'],md['target_ue'],md['harvest_gap'],md['heterogeneity_h'])
 sets['mechanism_timing']=choose(sets['mechanism'],mechanism_cell,3,'F2-timing-mechanism')
 sets['pilot']=[c for c in sets['main'] if c['metadata']['sample']<p['main']['pilot_samples_per_cell']]
 for key,expected in p['expected'].items():
  if key in sets and len(sets[key])!=expected:raise AssertionError((key,len(sets[key]),expected))
 assert len({c['case_id'] for k in ['main','mechanism','scale','numeric_scale','robustness'] for c in sets[k]})==11860
 return sets

def prepare(output):
 output=Path(output)
 try:sets=build()
 except Exception:
  C.write(output/'generation_diagnostics.json',GENERATION_DIAGNOSTICS)
  raise
 C.write(output/'generation_diagnostics.json',GENERATION_DIAGNOSTICS)
 manifest={}
 for name,cases in sets.items():
  identity=C.digest(sorted((c['case_id'],c['input_sha256']) for c in cases))
  C.write(output/'inputs'/(name+'.json.gz'),{'cases':cases,'identity':identity},immutable=True)
  manifest[name]={'inputs':len(cases),'clusters':len({c['metadata']['cluster_id'] for c in cases}),
    'unique_models':len({c['input_sha256'] for c in cases}),'identity':identity}
 C.write(output/'input_manifest.json',manifest,immutable=True)
 expected=C.HERE/'expected_inputs.json'
 if expected.exists() and C.read(expected)!=manifest:raise ValueError('F2 preregistered input identities changed')
 return manifest

def load(output,name):
 if name in ('legacy_ablation','constructed'):
  ensure_legacy(output)
 value=C.read(Path(output)/'inputs'/(name+'.json.gz'));cases=value['cases']
 if C.digest(sorted((c['case_id'],c['input_sha256']) for c in cases))!=value['identity']:raise ValueError('Dataset identity mismatch')
 for c in cases:
  if name in ('legacy_ablation','constructed'):data.validate_case(c)
  else:validate(c)
 return cases

def ensure_legacy(output):
 """Original 720 inputs and 180 constructs stay outside the F2 random pool."""
 import importlib.util
 spec=importlib.util.spec_from_file_location('old_f2_prepare',C.LEGACY/'prepare.py')
 old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
 config=C.read(C.LEGACY/'configs/campaigns.json')['latest']
 cases=old.generate_campaign(config)
 expected=C.read(C.LEGACY/'configs/expected_inputs.json')['latest']
 if {'inputs':len(cases),'identity_sha256':old.identity(cases)}!=expected:raise ValueError('Legacy identities changed')
 for name,items in [('legacy_ablation',cases),('constructed',old.constructed())]:
  ident=C.digest(sorted((c['case_id'],c['input_sha256']) for c in items))
  C.write(Path(output)/'inputs'/(name+'.json.gz'),{'cases':items,'identity':ident},immutable=True)
