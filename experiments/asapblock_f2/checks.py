"""Independent finite AB traces and exact-model preflight gates."""
import random
from pathlib import Path
import common as C

def trace_check(case,responses,first_bounds=None,seed=0,release_stop=300):
 """Finite sporadic releases, arbitrary pulse phase, debit-before-harvest.

 This is a falsification check, not exhaustive state exploration or a WCRT oracle.
 """
 m=case['model'];ts=m['tasks'];rng=random.Random(seed)
 gap=case['metadata']['harvest_gap'];scale=m['energy_scale'];phase=rng.randrange(gap)
 next_release=[rng.randrange(t['T']) for t in ts];queues=[[] for t in ts]
 energy=0;released=completed=first_checks=0
 end=release_stop+max(t['D'] for t in ts)+gap+2
 for now in range(end):
  for i,t in enumerate(ts):
   if now==next_release[i] and now<release_stop:
    queues[i].append({'release':now,'left':t['C'],'first':False});released+=1
    next_release[i]=now+t['T']+rng.randrange(max(1,t['T']//2))
  selected=[];available=energy
  for i,t in enumerate(ts):
   if len(selected)==m['processors']:break
   if not queues[i]:continue
   if available<t['power']:break
   selected.append(i);available-=t['power']
  energy=available
  for i in selected:
   job=queues[i][0];age=now+1-job['release'];job['left']-=1
   if not job['first']:
    if first_bounds is not None:assert age<=first_bounds[i],('F violated',i,age,first_bounds[i])
    job['first']=True;first_checks+=1
   if job['left']==0:
    assert age<=responses[i],('R violated',i,age,responses[i])
    queues[i].pop(0);completed+=1
  # Slot-end harvest; any L consecutive slots contain at least floor(L/G) pulses.
  if (now+1+phase)%gap==0:energy+=gap*scale
  assert energy>=0
  for i,q in enumerate(queues):
   for job in q:
    age=now+1-job['release']
    assert age<responses[i],('Unfinished beyond R',i,age,responses[i])
    if first_bounds is not None and not job['first']:assert age<first_bounds[i],('No first unit by F',i,age,first_bounds[i])
 assert released==completed,('Undrained jobs',released,completed)
 return {'released':released,'completed':completed,'first_checks':first_checks,'slots':end,'pulse_phase':phase}

def preflight(output):
 import inputs
 from run import call
 output=Path(output);cpu=C.environment()['physical_cpus'][0]
 simple=inputs.data.make_case('preflight-single',[(2,6,10,1)],1,list(range(7)),
  metadata={'cluster_id':'preflight-single','harvest_gap':1})
 constructed=inputs.load(output,'constructed')[0]
 result={'source_sha256':C.source_identity()['sha256'],'parity_budget_seconds':120,
  'methods':[],'parity':[],'traces':[],'diagnostics':None}
 for method in C.LEGACY_METHODS:
  ans=call(simple,method,30,cpu)
  assert ans['status']=='CERTIFIED',(method,ans)
  cert=ans.get('certificate',ans)
  check=call(simple,method,120,cpu,'reference',cert)
  assert check['status']=='PASS',(method,check)
  result['methods'].append({'method':method,'status':'CERTIFIED','reference':'PASS',
   'R':ans['response_bounds'],'F':ans.get('first_bounds')})
  if method=='completion_only':
   shifted=call(simple,method,120,cpu,'derived_first',cert)
   assert shifted['status']=='PASS',shifted
   result['completion_only_derived_first']='PASS'
 # A real structural witness, not just an easy single-task adapter check.
 ca={m:call(constructed,m,30,cpu) for m in ['production_analytic','full','no_shared_budget','completion_only']}
 for method in ['production_analytic','full']:
  assert ca[method]['status']=='CERTIFIED' and ca[method]['response_bounds']==[3,5,7],ca[method]
 for method in ['no_shared_budget','completion_only']:assert ca[method]['status']=='NO_CERTIFICATE'
 diag=call(constructed,'full',120,cpu,'diagnose',ca['full']['certificate'])
 assert diag['status']=='PASS',diag
 result['diagnostics']=diag
 # Fixed new models selected without reference to their analysis outcomes.
 pilots=inputs.load(output,'pilot')
 selected=[c for c in pilots if c['model']['processors']==2 and c['metadata']['target_uc']=='1/5'
   and c['metadata']['deadline_mode']=='implicit' and c['metadata']['harvest_gap']==1]
 for c in selected:
  # Mathematical parity is outside the fixed 30-second coverage experiment.
  a,b=(call(c,method,120,cpu) for method in ['production_analytic','full'])
  assert a['status'] in C.COMPLETED and b['status'] in C.COMPLETED,(a['status'],b['status'])
  assert (a['status'],a.get('response_bounds'),a.get('first_bounds'))==(b['status'],b.get('response_bounds'),b.get('first_bounds'))
  result['parity'].append({'case_id':c['case_id'],'status':a['status'],'R':a.get('response_bounds'),'F':a.get('first_bounds')})
  if a['status']=='CERTIFIED':
   for seed in range(5):result['traces'].append({'case_id':c['case_id'],'seed':seed,
    **trace_check(c,a['response_bounds'],a['first_bounds'],seed)})
 # The manuscript supply has rate 2, so trace_check is deliberately restricted
 # to F2's rate-1 generated inputs rather than silently applying another supply.
 result['status']='PASS'
 C.write(output/'preflight_checks.json',result)
 return {'methods_replayed':len(result['methods']),'parity_inputs':len(result['parity']),
  'finite_traces':len(result['traces']),'jobs_checked':sum(t['completed'] for t in result['traces']),
  'diagnostics':'PASS','status':'PASS'}
