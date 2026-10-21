#!/usr/bin/env python3
"""Prepare, run, replay, summarize and plot the exact F2 experiment plan."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime,timezone
import fcntl
import os
from pathlib import Path
from queue import Queue,Empty
import subprocess
import sys
import tempfile
from threading import Event
import time
import common as C

def call(case,method,budget,cpu,job='analysis',answer=None):
 payload={'case':case,'method':method,'budget_seconds':budget,'clock':'wall','cpu':cpu,
  'memory_limit_mib':C.PROTOCOL['memory_limit_mib'],'job':job}
 if answer is not None:payload['answer']=answer
 start=time.perf_counter()
 text=C.canonical(payload)
 with tempfile.TemporaryDirectory(prefix='ab-f2-') as tmp:
  target=Path(tmp)/'result.json'
  try:
   remaining=budget-(time.perf_counter()-start)
   if remaining<=0:raise subprocess.TimeoutExpired('payload',budget)
   proc=subprocess.run([sys.executable,'-B',str(C.HERE/'worker.py'),str(target)],input=text,
    text=True,capture_output=True,timeout=remaining,
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'})
   if proc.returncode or not target.exists():
    value={'status':'ERROR','reason':f'Worker exit {proc.returncode}','stderr':proc.stderr[-3000:]}
   else:value=C.read(target)
  except subprocess.TimeoutExpired:
   value={'status':'TIMEOUT','censored':True,'limit_kind':'REQUEST_WALL'}
  elapsed=time.perf_counter()-start
  if elapsed>budget and value['status'] not in ('ERROR','RESOURCE_LIMIT'):
   value={'status':'TIMEOUT','censored':True,'limit_kind':'REQUEST_WALL','late_output_discarded':True}
 value['request_wall_seconds']=elapsed
 if value['status']=='TIMEOUT':value.update(censored=True,response_bounds=None,first_bounds=None)
 if value['status'] not in C.STATUSES|{'PASS'}:raise ValueError('Unknown worker outcome: '+str(value))
 return value

@contextmanager
def lock(path,shared=False):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('a') as stream:
  try:fcntl.flock(stream,fcntl.LOCK_NB|(fcntl.LOCK_SH if shared else fcntl.LOCK_EX))
  except BlockingIOError:raise RuntimeError(f'Another experiment holds {path}')
  try:yield
  finally:fcntl.flock(stream,fcntl.LOCK_UN)

def suite_spec(output,suite,args):
 import inputs
 dataset,methods,budget,repeats,serial=C.SUITES[suite]
 cases=inputs.load(output,dataset)
 selection=getattr(args,'limit',None)
 if selection is not None:
  cases=sorted(cases,key=lambda c:C.digest(['F2-smoke',c['case_id']]))[:selection]
 if not cases:raise ValueError('No inputs selected')
 requested=getattr(args,'workers',2)
 env=C.environment();cpus=env['physical_cpus']
 quota=env.get('cpu_quota')
 if quota and quota.split()[0]!='max':
  amount,period=map(int,quota.split());cpus=cpus[:max(1,amount//period)]
 if serial:cpus=cpus[:1]
 else:cpus=cpus[:min(requested,len(cpus))]
 if not cpus:raise ValueError('No available physical CPUs')
 identity=C.source_identity()
 params={'schema':C.PROTOCOL['schema'],'suite':suite,'methods':methods,'budget_seconds':budget,
  'repetitions':repeats,'memory_limit_mib':C.PROTOCOL['memory_limit_mib'],
  'reference_budget_seconds':C.PROTOCOL['reference_budget_seconds'],
  'serial':serial,'cpus':cpus,'source_sha256':identity['sha256'],
  'protocol_sha256':C.digest(C.PROTOCOL),'selected':[(c['case_id'],c['input_sha256']) for c in cases],
  'environment':{k:v for k,v in env.items() if k!='git_head'},
  'custom_run':selection is not None,'pilot':suite=='pilot'}
 params['run_id']=C.digest(params)
 return params,cases,identity

def planned_tasks(spec,cases):
 tasks=[]
 for case in sorted(cases,key=lambda c:C.digest(['F2-request-order',c['case_id']])):
  for repeat in range(spec['repetitions']):
   methods=list(spec['methods'])
   # Alternate the first method across inputs AND repeats, independent of results.
   if (int(C.digest(case['case_id'])[:8],16)+repeat)%2:methods.reverse()
   for method in methods:
    ident={'run_id':spec['run_id'],'case_id':case['case_id'],'method':method,'repeat':repeat,
     'budget_seconds':spec['budget_seconds']}
    tasks.append((C.digest(ident),ident,case))
 return tasks

def validate_records(spec,cases,records,complete=False):
 selected=dict(spec['selected']);cases=[c for c in cases if c['case_id'] in selected]
 if len(cases)!=len(selected) or any(c['input_sha256']!=selected[c['case_id']] for c in cases):
  raise ValueError('Selected input identities differ from the run plan')
 expected={rid:(ident,c) for rid,ident,c in planned_tasks(spec,cases)};seen=set()
 for row in records:
  rid=row['request_id']
  if rid in seen or rid not in expected:raise ValueError('Duplicate or foreign request in run records')
  seen.add(rid);ident,c=expected[rid]
  if any(row.get(k)!=v for k,v in ident.items()) or row.get('input_sha256')!=c['input_sha256']:
   raise ValueError('Saved request identity differs from the run plan')
 if complete and seen!=set(expected):raise ValueError('Run records do not cover every planned request')
 return expected

def run_suite(output,suite,args):
 output=Path(output);folder=output/suite;folder.mkdir(parents=True,exist_ok=True)
 spec,cases,identity=suite_spec(output,suite,args)
 if suite in C.CORE_SUITES or suite=='pilot':
  gate=output/'preflight_checks.json'
  if not gate.exists():raise ValueError('Run the check action before this experiment')
  checked=C.read(gate)
  if checked.get('status')!='PASS' or checked['source_sha256']!=identity['sha256']:raise ValueError('Preflight is missing, failed or belongs to different source code')
 with lock(folder/'run.lock'),lock(Path(tempfile.gettempdir())/'asapblock-f2-host.lock',shared=not spec['serial']):
  C.write(folder/'plan.json',spec,immutable=True)
  C.write(folder/'sources.json',identity,immutable=True)
  C.write(folder/'environment.json',C.environment(),immutable=False)
  tasks=planned_tasks(spec,cases);lookup={rid:(ident,c) for rid,ident,c in tasks}
  old=C.sync_journal(folder/'primary.jsonl')
  validate_records(spec,cases,old)
  if any(r['status'] in {'ERROR','RESOURCE_LIMIT','INVALID_CERTIFICATE'} for r in old):
   raise RuntimeError('Resume refused: resolve the recorded request error before running a new batch')
  for row in old:
   if row['request_id'] not in lookup:raise ValueError('Resume refused: request not in plan')
   ident,case=lookup[row['request_id']]
   if row['input_sha256']!=case['input_sha256'] or any(row[k]!=v for k,v in ident.items()):raise ValueError('Resume identity mismatch')
  done={r['request_id'] for r in old};pending=[t for t in tasks if t[0] not in done]
  cpus=spec['cpus'];queues=[[] for _ in cpus]
  assignment={cid:i%len(cpus) for i,cid in enumerate(dict.fromkeys(c['case_id'] for _,_,c in tasks))}
  for t in pending:queues[assignment[t[2]['case_id']]].append(t)
  queue=Queue();stop=Event()
  def consume(cpu,jobs):
   try:
    for rid,ident,case in jobs:
     if stop.is_set():break
     value=call(case,ident['method'],ident['budget_seconds'],cpu)
     certificate=value.pop('certificate',None)
     if ident['method']=='seq' and value['status']=='CERTIFIED':
      certificate={k:value[k] for k in ['status','response_bounds','details']}
     history=value.pop('history',None)
     value.pop('details',None)
     if value['status']=='CERTIFIED' and certificate is None:
      value={'status':'ERROR','reason':'Certified result did not export a replayable certificate'}
     if certificate is not None:
      path=folder/'certificates'/(rid+'.json.gz');C.write(path,certificate)
      value['certificate_file']=str(path.relative_to(output))
      value['certificate_sha256']=C.digest(certificate)
     if history is not None:
      C.write(folder/'histories'/(rid+'.json.gz'),history)
     row={**ident,**value,'request_id':rid,'input_sha256':case['input_sha256'],
      'cluster_id':case['metadata'].get('cluster_id',case['case_id']),'host_cpu':cpu,
      'completed_utc':datetime.now(timezone.utc).isoformat()}
     queue.put(row)
     if row['status'] in {'ERROR','RESOURCE_LIMIT','INVALID_CERTIFICATE'}:stop.set()
   except BaseException as exc:stop.set();queue.put(exc)
   finally:queue.put(None)
  finished=0;last=time.monotonic();allrows=list(old)
  with ThreadPoolExecutor(max_workers=len(cpus)) as pool:
   futures=[pool.submit(consume,cpu,jobs) for cpu,jobs in zip(cpus,queues)]
   while finished<len(cpus):
    try:item=queue.get(timeout=1)
    except Empty:continue
    if item is None:finished+=1;continue
    if isinstance(item,BaseException):raise item
    C.journaled_append(folder/'primary.jsonl',item);allrows.append(item)
    if time.monotonic()-last>=15 or len(allrows)==len(tasks):
     print(C.canonical({'suite':suite,'done':len(allrows),'planned':len(tasks),
      'states':dict(Counter(r['status'] for r in allrows))}),flush=True);last=time.monotonic()
   for f in futures:f.result()
  allrows=C.sync_journal(folder/'primary.jsonl',allrows)
  errors=sum(r['status'] in {'ERROR','RESOURCE_LIMIT','INVALID_CERTIFICATE'} for r in allrows)
  complete={r['request_id'] for r in allrows}==set(lookup)
  C.write(folder/'execution.json',{'requests':len(allrows),'complete':complete,
   'run_id':spec['run_id'],'journal_checked':True,
   'errors':errors})
  if errors:raise RuntimeError('Analysis paused after a request error; saved records are preserved')
  validate_records(spec,cases,allrows,complete=True)
 return allrows

def verify(output,suite):
 import inputs
 output=Path(output);folder=output/suite;spec=C.read(folder/'plan.json')
 if C.source_identity()['sha256']!=spec['source_sha256']:raise ValueError('Source changed since analysis')
 cases={c['case_id']:c for c in inputs.load(output,C.SUITES[suite][0])}
 with lock(folder/'run.lock'),lock(folder/'verify.lock'),lock(Path(tempfile.gettempdir())/'asapblock-f2-host.lock',shared=True):
  checks=C.sync_journal(folder/'verification.jsonl');done={x['request_id']:x for x in checks}
  records=C.sync_journal(folder/'primary.jsonl');validate_records(spec,list(cases.values()),records,complete=True)
  certified={r['request_id']:r for r in records if r['status']=='CERTIFIED'}
  for check in checks:
   if check['request_id'] not in certified:raise ValueError('Foreign reference check in run records')
   if check['status']!='PASS':raise RuntimeError('A previous independent replay did not pass; it cannot be skipped')
  for row in records:
   if row['status']!='CERTIFIED':continue
   cert=C.read(output/row['certificate_file'])
   if C.digest(cert)!=row['certificate_sha256']:raise ValueError('Certificate hash mismatch')
   if row['request_id'] in done:
    if done[row['request_id']]['certificate_sha256']!=row['certificate_sha256']:raise ValueError('Replay hash mismatch')
    continue
   budget=spec['reference_budget_seconds']
   value=call(cases[row['case_id']],row['method'],budget,spec['cpus'][0],'reference',cert)
   check={'request_id':row['request_id'],'case_id':row['case_id'],'method':row['method'],
    'certificate_sha256':row['certificate_sha256'],'reference_budget_seconds':budget,**value}
   C.journaled_append(folder/'verification.jsonl',check);checks.append(check)
   if value['status']!='PASS':raise RuntimeError('Independent replay did not pass: '+C.canonical(check))
  checks=C.sync_journal(folder/'verification.jsonl',checks)
 return {'replayed':len(checks),'pass':sum(x['status']=='PASS' for x in checks)}

def diagnose(output,suite):
 """Final-profile counterfactuals on preselected inputs, outside performance clocks."""
 import inputs
 if suite!='mechanism':raise ValueError('Same-X diagnostics use the mechanism suite; constructs are checked by check')
 output=Path(output);folder=output/suite;spec=C.read(folder/'plan.json')
 if C.source_identity()['sha256']!=spec['source_sha256']:raise ValueError('Source changed since analysis')
 selected={cid for cid,_ in spec['selected']}
 cases=[c for c in inputs.load(output,'mechanism') if c['case_id'] in selected]
 if not spec['custom_run']:
  def key(c):
   md=c['metadata'];return(md['target_uc'],md['target_ue'],md['harvest_gap'],md['heterogeneity_h'])
  cases=inputs.choose(cases,key,1,'F2-same-X-diagnostic')
 with lock(folder/'run.lock'),lock(folder/'diagnose.lock'),lock(Path(tempfile.gettempdir())/'asapblock-f2-host.lock',shared=True):
  C.write(folder/'diagnostic_plan.json',{'run_id':spec['run_id'],'inputs':[c['case_id'] for c in cases],
   'budget_seconds':120,'selection':'one result-independent hash choice per cell; all selected inputs for a custom smoke',
   'outside_analysis_budget':True,'custom_run':spec['custom_run']},immutable=True)
  primary={(r['case_id'],r['method']):r for r in C.sync_journal(folder/'primary.jsonl') if r['repeat']==0}
  checks={r['request_id']:r for r in C.sync_journal(folder/'verification.jsonl')}
  records=C.sync_journal(folder/'diagnostics.jsonl');done={r['request_id'] for r in records}
  for case in cases:
   cid=case['case_id'];full=primary[cid,'full'];rid=C.digest([spec['run_id'],'same-X-diagnostic',cid])
   if rid in done:continue
   if full['status']!='CERTIFIED':value={'status':'SKIPPED_NOT_CERTIFIED','analysis_status':full['status']}
   else:
    if checks.get(full['request_id'],{}).get('status')!='PASS':raise ValueError('Replay the full certificate before diagnostics')
    cert=C.read(output/full['certificate_file'])
    if C.digest(cert)!=full['certificate_sha256']:raise ValueError('Diagnostic certificate hash mismatch')
    value=call(case,'full',120,spec['cpus'][0],'diagnose',cert)
   row={'request_id':rid,'run_id':spec['run_id'],'case_id':cid,'input_sha256':case['input_sha256'],
    'paired_analysis_states':{m:primary[cid,m]['status'] for m in spec['methods']},**value}
   C.journaled_append(folder/'diagnostics.jsonl',row);records.append(row)
   if value['status'] in ('ERROR','RESOURCE_LIMIT'):raise RuntimeError('Same-X diagnostic failed: '+C.canonical(row))
  records=C.sync_journal(folder/'diagnostics.jsonl',records)
 return {'selected':len(cases),'states':dict(Counter(r['status'] for r in records)),
  'outside_performance_timing':True}

def plan():
 from inputs import build
 sets=build();out={};total=0;cap=0
 for name in C.CORE_SUITES:
  dataset,methods,budget,reps,_=C.SUITES[name]
  n=len(sets[dataset]);requests=n*len(methods)*reps
  out[name]={'inputs':n,'requests':requests,'budget_seconds':budget,'serial':C.SUITES[name][-1]}
  total+=requests;cap+=requests*budget
 assert total==C.PROTOCOL['expected']['core_requests']
 assert cap==C.PROTOCOL['expected']['core_cap_seconds']
 return {'suites':out,'requests':total,'cap_hours':cap/3600,'document_sha256':C.PROTOCOL['document_sha256']}

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('action',choices=['prepare','plan','check','run','verify','diagnose','summarize','plot','all'])
 ap.add_argument('--output',type=Path,default=C.HERE/'runs/f2')
 ap.add_argument('--suite',choices=list(C.SUITES),default='pilot')
 ap.add_argument('--workers',type=int,default=2)
 ap.add_argument('--limit',type=int,help='Result-independent smoke subset; always marked custom_run')
 args=ap.parse_args()
 if args.workers<1 or (args.limit is not None and args.limit<1):ap.error('workers/limit must be positive')
 if args.action=='plan':print(C.canonical(plan()));return
 import inputs
 if args.action in ('prepare','all'):
  print(C.canonical({'prepared':inputs.prepare(args.output)}),flush=True)
 if args.action in ('check','all'):
  import checks
  with lock(Path(tempfile.gettempdir())/'asapblock-f2-host.lock',shared=True):
   print(C.canonical(checks.preflight(args.output)),flush=True)
 if args.action in ('run','all'):run_suite(args.output,args.suite,args)
 if args.action in ('verify','all'):print(C.canonical(verify(args.output,args.suite)),flush=True)
 if args.action=='diagnose':print(C.canonical(diagnose(args.output,args.suite)),flush=True)
 if args.action in ('summarize','all'):
  import summarize
  with lock(Path(tempfile.gettempdir())/'asapblock-f2-host.lock',shared=True):
   print(C.canonical(summarize.summarize(args.output,args.suite)),flush=True)
 if args.action in ('plot','all'):
  import plots
  with lock(Path(tempfile.gettempdir())/'asapblock-f2-host.lock',shared=True):
   print(C.canonical(plots.plot(args.output,args.suite)),flush=True)

if __name__=='__main__':main()
