"""Complete denominators, paired states, conditional bounds and censored costs."""
from collections import Counter,defaultdict
import csv
from fractions import Fraction
from pathlib import Path
import statistics
import math
import numpy as np
import common as C

def csvwrite(path,items,fields=None):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 if fields is None:fields=list(dict.fromkeys(k for x in items for k in x))
 with path.open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
  for x in items:w.writerow({k:(C.canonical(v) if isinstance(v,(dict,list,tuple)) else v) for k,v in x.items()})

def cell(c):
 md=c['metadata'];return {'m':c['model']['processors'],'n':len(c['model']['tasks']),
  'uc':md.get('target_uc'),'ue':md.get('target_ue'),'deadline':md.get('deadline_mode'),
  'gap':md.get('harvest_gap'),'h':md.get('heterogeneity_h'),'factor':md.get('time_factor',1)}

def stratum(c,suite):
 x=cell(c)
 if suite in ('mechanism','mechanism_timing','numeric_scale'):return(x['uc'],)
 if suite in ('scale',):return(x['m'],x['n'],x['uc'])
 if suite=='timing' and c['metadata'].get('family')=='scale':return(x['m'],x['n'],x['uc'])
 return(x['m'],x['uc'])

def ci(items,repetitions=None):
 """items=(cluster,stratum,numerator,denominator), including zero/missing pairs."""
 if not items:return [None,None]
 grouped=defaultdict(lambda:defaultdict(lambda:[0.,0.]))
 for cluster,s,a,b in items:
  grouped[str(s)][cluster][0]+=a;grouped[str(s)][cluster][1]+=b
 reps=C.PROTOCOL['bootstrap_repetitions'] if repetitions is None else repetitions
 totals=np.zeros((reps,2));rng=np.random.default_rng(C.PROTOCOL['bootstrap_seed'])
 for key in sorted(grouped):
  arr=np.asarray(list(grouped[key].values()),dtype=float)
  picks=rng.integers(0,len(arr),size=(reps,len(arr)))
  totals+=arr[picks].sum(axis=1)
 valid=totals[:,1]>0
 if not valid.any():return [None,None]
 result=totals[valid,0]/totals[valid,1]
 return list(map(float,np.percentile(result,[2.5,97.5])))

def stable(result,expected):
 if len(result)!=expected or {x['repeat'] for x in result}!=set(range(expected)):return None
 states={x['status'] for x in result}
 if len(states)!=1 or next(iter(states)) not in C.COMPLETED:return None
 if len({C.canonical(x.get('response_bounds')) for x in result})!=1:return None
 if len({C.canonical(x.get('first_bounds')) for x in result})!=1:return None
 return {**result[0],'request_wall_seconds':statistics.median(x['request_wall_seconds'] for x in result),
  'solve_cpu_seconds':statistics.median(x.get('solve_cpu_seconds',0) for x in result)}

def population_p95(done,total,only_completed_or_timeout=True):
 """Linear empirical percentile is identifiable if both required ranks finished."""
 if not total or not only_completed_or_timeout:return None
 position=.95*(total-1);lo=int(np.floor(position));hi=int(np.ceil(position))
 if hi>=len(done):return None
 ordered=sorted(done)
 return float(ordered[lo]+(ordered[hi]-ordered[lo])*(position-lo))

def precision(case,new,reference,ref_method):
 n=case['model']['tasks'];a=new['response_bounds'];b=reference['response_bounds']
 if len(a)!=len(n) or len(b)!=len(n):raise ValueError('Incomplete response vector')
 if not all(t['C']<=ra<=t['D'] and t['C']<=rb<=t['D'] for t,ra,rb in zip(n,a,b)):raise ValueError('Invalid response bounds')
 rr=[100*(rb-ra)/rb for ra,rb in zip(a,b)]
 out={'response_reduction_percent':statistics.mean(rr),'absolute_response_reduction':statistics.mean(rb-ra for ra,rb in zip(a,b)),
  'identical_response_vector':a==b,'any_tighter':any(ra<rb for ra,rb in zip(a,b)),
  'any_looser':any(ra>rb for ra,rb in zip(a,b)),
  'new_min_deadline_slack':min(t['D']-r for t,r in zip(n,a)),
  'reference_min_deadline_slack':min(t['D']-r for t,r in zip(n,b))}
 fa=new.get('first_bounds');fb=reference.get('first_bounds')
 if ref_method=='completion_only':fb=[r-t['C']+1 for r,t in zip(b,n)]
 if fa is not None and fb is not None:
  out['first_reduction_percent']=statistics.mean(100*(y-x)/y for x,y in zip(fa,fb))
 return out

def precision_summary(values):
 n=len(values)
 def mean(key):return statistics.mean(x[key] for x in values) if n else None
 return {'common_certified':n,'response_mean_percent':mean('response_reduction_percent'),
  'response_median_percent':statistics.median(x['response_reduction_percent'] for x in values) if n else None,
  'absolute_response_mean_slots':mean('absolute_response_reduction'),
  'identical_vectors':sum(x['identical_response_vector'] for x in values),
  'identical_vector_percent':100*sum(x['identical_response_vector'] for x in values)/n if n else None,
  'any_tighter_sets':sum(x['any_tighter'] for x in values),
  'any_looser_sets':sum(x['any_looser'] for x in values),
  'first_mean_percent':statistics.mean(x['first_reduction_percent'] for x in values if 'first_reduction_percent' in x)
   if any('first_reduction_percent' in x for x in values) else None}

def paired_speed_summary(cases,by,target,reference,spec,suite):
 """Three stable repeats per method; bootstrap whole bones, including missing pairs."""
 if spec['repetitions']!=3:return []
 groups=defaultdict(list)
 for cid,c in cases.items():
  family=c['metadata'].get('family',suite)
  groups[('family',family,None)].append(cid)
  groups[('cell',family,C.canonical(cell(c)))].append(cid)
 result=[]
 for (scope,family,key),ids in sorted(groups.items(),key=str):
  items=[];ratios=[];states=Counter()
  for cid in ids:
   c=cases[cid];a=stable(by[cid,target],3);b=stable(by[cid,reference],3)
   eligible=a is not None and b is not None
   value=0.
   if eligible:
    if a['request_wall_seconds']<=0 or b['request_wall_seconds']<=0:raise ValueError('Nonpositive request time')
    ratio=b['request_wall_seconds']/a['request_wall_seconds'];ratios.append(ratio);value=math.log(ratio)
    states[a['status'],b['status']]+=1
   items.append((c['metadata'].get('cluster_id',cid),stratum(c,suite),value,int(eligible)))
  interval=ci(items)
  result.append({'new':target,'reference':reference,'scope':scope,'family':family,
   **(cell(cases[ids[0]]) if scope=='cell' else {}),'preselected_inputs':len(ids),'eligible_inputs':len(ratios),
   'repetitions_per_method':3,'geometric_mean_reference_over_new':math.exp(statistics.mean(map(math.log,ratios))) if ratios else None,
   'geometric_mean_ci95':[math.exp(x) if x is not None else None for x in interval],
   'median_reference_over_new':statistics.median(ratios) if ratios else None,
   'eligible_state_pairs':[{'new_state':a,'reference_state':b,'inputs':n} for (a,b),n in sorted(states.items())],
   'scope_note':'Conditional on both methods completing stably in all three repeats; repeats are not independent inputs.'})
 return result

def summarize(output,suite):
 import inputs
 import run
 output=Path(output);folder=output/suite;spec=C.read(folder/'plan.json')
 if C.source_identity()['sha256']!=spec['source_sha256']:raise ValueError('Source changed since analysis')
 cases={c['case_id']:c for c in inputs.load(output,C.SUITES[suite][0])}
 cases={cid:cases[cid] for cid,_ in spec['selected']}
 logs=C.record_rows(folder/'primary.jsonl');checks={r['request_id']:r for r in C.record_rows(folder/'verification.jsonl')}
 run.validate_records(spec,list(cases.values()),logs)
 request_status_counts=Counter(r['status'] for r in logs)
 by=defaultdict(list);invalid=0;pending=0
 for r in logs:
  if r['case_id'] not in cases or r['run_id']!=spec['run_id']:raise ValueError('Foreign result in summary')
  rr=dict(r)
  if r['status']=='CERTIFIED':
   check=checks.get(r['request_id'])
   if check is None:rr['status']='UNVERIFIED';pending+=1
   elif check['status']!='PASS' or check['certificate_sha256']!=r['certificate_sha256']:
    rr['status']='INVALID_CERTIFICATE';invalid+=1
  by[r['case_id'],r['method']].append(rr)
 first={(cid,m):next((r for r in by[cid,m] if r['repeat']==0),{'status':'NOT_RUN'})
  for cid in cases for m in spec['methods']}
 states=[];allstats=[];timings=[];stability=[]
 for cid,c in cases.items():
  for method in spec['methods']:
   rr=by[cid,method];ss=stable(rr,spec['repetitions'])
   stability.append({'case_id':cid,'cluster_id':c['metadata'].get('cluster_id',cid),**cell(c),
    'method':method,'expected_repetitions':spec['repetitions'],'observed_repetitions':len(rr),
    'states':[r['status'] for r in sorted(rr,key=lambda x:x['repeat'])],
    'stable_completed':ss is not None,'stable_status':ss['status'] if ss else None,
    'median_seconds':ss['request_wall_seconds'] if ss else None,
    'median_solve_cpu_seconds':ss['solve_cpu_seconds'] if ss else None})
 grouped=defaultdict(list)
 for cid,c in cases.items():grouped[C.canonical(cell(c))].append(cid)
 for key,ids in sorted(grouped.items()):
  for method in spec['methods']:
   counts=Counter(first[cid,method]['status'] for cid in ids)
   st={'suite':suite,**cell(cases[ids[0]]),'method':method,'inputs':len(ids),
    **{s:counts[s] for s in sorted(C.STATUSES|{'NOT_RUN','UNVERIFIED'})},
    'certification_percent':100*counts['CERTIFIED']/len(ids)}
   states.append(st)
   done=[first[cid,method]['request_wall_seconds'] for cid in ids if first[cid,method]['status'] in C.COMPLETED]
   overall=population_p95(done,len(ids),sum(counts[s] for s in C.COMPLETED|{'TIMEOUT'})==len(ids))
   timings.append({**cell(cases[ids[0]]),'method':method,'inputs':len(ids),'completed':len(done),
    'timeouts':counts['TIMEOUT'],'conditional_median_seconds':float(np.median(done)) if done else None,
    'conditional_P95_seconds':float(np.percentile(done,95)) if done else None,
    'population_P95_seconds':overall,
    'population_P95_scope':'identifiable_from_completed_ranks' if overall is not None else 'not_established_within_budget'})
 for method in spec['methods']:
  counts=Counter(first[cid,method]['status'] for cid in cases)
  allstats.append({'method':method,'inputs':len(cases),'states':dict(counts),
   'certification_percent':100*counts['CERTIFIED']/len(cases)})
 target='production_analytic' if 'production_analytic' in spec['methods'] else 'full'
 contrasts=[m for m in spec['methods'] if m!=target];pairs=[];prec=[];diffs=[];bounds=[];speeds=[]
 cell_pairs=[];cell_diffs=[];cell_bounds=[];speed_summaries=[]
 for ref in contrasts:
  pcount=Counter();delta_items=[];r_items=[];f_items=[];common=[]
  for cid,c in cases.items():
   a,b=first[cid,target],first[cid,ref];sa,sb=a['status'],b['status']
   pcount[sa,sb]+=1;cluster=c['metadata'].get('cluster_id',cid);strat=stratum(c,suite)
   delta_items.append((cluster,strat,100*(int(sa=='CERTIFIED')-int(sb=='CERTIFIED')),1))
   if sa==sb=='CERTIFIED':
    value=precision(c,a,b,ref);common.append(value)
    prec.append({'case_id':cid,'cluster_id':cluster,**cell(c),'new':target,'reference':ref,**value})
    r_items.append((cluster,strat,value['response_reduction_percent'],1))
    if 'first_reduction_percent' in value:f_items.append((cluster,strat,value['first_reduction_percent'],1))
   else:
    r_items.append((cluster,strat,0,0));f_items.append((cluster,strat,0,0))
   aa=stable(by[cid,target],spec['repetitions']);bb=stable(by[cid,ref],spec['repetitions'])
   if aa is not None and bb is not None:
    speeds.append({'case_id':cid,'new':target,'reference':ref,'new_state':aa['status'],'reference_state':bb['status'],
     'repetitions_per_method':spec['repetitions'],
     'speed_scope':'three_stable_repeats' if spec['repetitions']==3 else 'single_request_diagnostic',
     'new_seconds':aa['request_wall_seconds'],'reference_seconds':bb['request_wall_seconds'],
     'reference_over_new':bb['request_wall_seconds']/aa['request_wall_seconds'],**cell(c)})
  pairs.extend({'new':target,'reference':ref,'new_state':a,'reference_state':b,'inputs':n} for (a,b),n in sorted(pcount.items()))
  values=[x[2] for x in delta_items]
  diffs.append({'new':target,'reference':ref,'inputs':len(cases),'difference_pp':statistics.mean(values),
   'ci95':ci(delta_items),'new_confirmed_gain':pcount['CERTIFIED','NO_CERTIFICATE'],
   'reference_confirmed_gain':pcount['NO_CERTIFICATE','CERTIFIED'],
   'new_cert_reference_timeout':pcount['CERTIFIED','TIMEOUT'],
   'reference_cert_new_timeout':pcount['TIMEOUT','CERTIFIED']})
  bounds.append({'new':target,'reference':ref,**precision_summary(common),
   'response_ci95':ci(r_items),'first_ci95':ci(f_items)})
  speed_summaries.extend(paired_speed_summary(cases,by,target,ref,spec,suite))
  pm={x['case_id']:x for x in prec if x['reference']==ref}
  for key,ids in sorted(grouped.items()):
   base={'new':target,'reference':ref,**cell(cases[ids[0]]),'inputs':len(ids)}
   pc=Counter((first[cid,target]['status'],first[cid,ref]['status']) for cid in ids)
   cell_pairs.extend({**base,'new_state':a,'reference_state':b,'count':count} for (a,b),count in sorted(pc.items()))
   ds=[(cases[cid]['metadata'].get('cluster_id',cid),stratum(cases[cid],suite),
    100*(int(first[cid,target]['status']=='CERTIFIED')-int(first[cid,ref]['status']=='CERTIFIED')),1) for cid in ids]
   cell_diffs.append({**base,'difference_pp':statistics.mean(x[2] for x in ds),'ci95':ci(ds),
    'new_confirmed_gain':pc['CERTIFIED','NO_CERTIFICATE'],'reference_confirmed_gain':pc['NO_CERTIFICATE','CERTIFIED'],
    'new_cert_reference_timeout':pc['CERTIFIED','TIMEOUT'],'reference_cert_new_timeout':pc['TIMEOUT','CERTIFIED']})
   pp=[pm[cid] for cid in ids if cid in pm]
   cell_bounds.append({**base,**precision_summary(pp)})
 budgets=[]
 if suite=='timing':
  for family in ['main','scale']:
   ids=[cid for cid,c in cases.items() if c['metadata']['family']==family]
   for method in spec['methods']:
    for budget in C.PROTOCOL['timing']['budgets']:
     count=sum(first[cid,method]['status']=='CERTIFIED' and first[cid,method]['request_wall_seconds']<=budget for cid in ids)
     budgets.append({'family':family,'method':method,'budget_seconds':budget,'inputs':len(ids),
      'certified':count,'certification_percent':100*count/len(ids) if ids else None})
 result={'suite':suite,'run_id':spec['run_id'],'custom_run':spec['custom_run'],'pilot':spec['pilot'],
  'execution_profile':spec['execution_profile'],'parallel_timing':spec['parallel_timing'],
  'analysis_concurrency':len(spec['cpus']),'time_scope':spec['time_scope'],
  'inputs':len(cases),'planned_requests':len(cases)*len(spec['methods'])*spec['repetitions'],
  'observed_requests':len(logs),'reference_checks_pending':pending,'invalid_certificates':invalid,
  'request_status_counts':dict(request_status_counts),
  'request_errors':sum(request_status_counts[s] for s in ['ERROR','RESOURCE_LIMIT','INVALID_CERTIFICATE']),
  'methods':allstats,'differences':diffs,'bounds':bounds,'cells':states,
  'timings':timings,'precision':prec,'speed_pairs':speeds,'budget_coverage':budgets,'repeat_stability':stability,
  'paired_states_by_cell':cell_pairs,'certification_differences_by_cell':cell_diffs,'precision_by_cell':cell_bounds,
  'paired_speed_summary':speed_summaries,
  'bootstrap_repetitions':C.PROTOCOL['bootstrap_repetitions']}
 tables=folder/'tables'
 for name,items in [('state_counts',states),('paired_states',pairs),('precision_by_input',prec),
  ('certification_differences',diffs),('precision_summary',bounds),('timing_summary',timings),
  ('speed_pairs',speeds),('budget_coverage',budgets),('repeat_stability',stability),
  ('paired_states_by_cell',cell_pairs),('certification_differences_by_cell',cell_diffs),
  ('precision_by_cell',cell_bounds),('paired_speed_summary',speed_summaries)]:
  csvwrite(tables/(name+'.csv'),[{**x,'execution_profile':spec['execution_profile'],
   'analysis_concurrency':len(spec['cpus']),'time_scope':spec['time_scope']} for x in items])
 C.write(folder/'summary.json',result)
 return {k:result[k] for k in ['suite','inputs','planned_requests','observed_requests','reference_checks_pending','invalid_certificates','methods']}
