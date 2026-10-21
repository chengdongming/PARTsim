#!/usr/bin/env python3
"""Resume the complete frozen F2 campaign, with durable status and data snapshots."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile
import common as C
import run

ORDER=['main','mechanism','scale','robustness','timing','numeric_scale','mechanism_timing']

def snapshot(output):
 output=Path(output);target=output/'F2_formal_checkpoint_20261021.zip'
 with run.lock(output/'snapshot.lock'),run.lock(Path(tempfile.gettempdir())/'asapblock-f2-host.lock',shared=True):
  temp=target.with_suffix('.zip.tmp');counts={};included=set()
  with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
   def add(path):
    path=Path(path);name=str(path.relative_to(output))
    if path.is_file() and name not in included:archive.write(path,name);included.add(name)
   for path in output.glob('*.json'):add(path)
   for path in output.glob('*.txt'):add(path)
   for path in (output/'inputs').glob('*.json.gz'):add(path)
   for suite in ORDER:
    folder=output/suite
    for name in ['plan.json','sources.json','environment.json','execution.json','diagnostic_plan.json']:
     add(folder/name)
    rows=[]
    for kind in ['primary','verification','diagnostics']:
     # The mutable JSONL can be mid-write. Snapshot only atomically saved copies.
     for path in sorted((folder/(kind+'.records')).glob('*.json')):
      row=C.read(path);add(path)
      if kind=='primary':
       rows.append(row)
       if 'certificate_file' in row:
        cert=output/row['certificate_file']
        if not cert.exists() or C.digest(C.read(cert))!=row['certificate_sha256']:
         raise ValueError('Cannot snapshot a missing or changed certificate')
        add(cert)
       add(folder/'histories'/(row['request_id']+'.json.gz'))
    counts[suite]={'requests':len(rows),'states':dict(Counter(r['status'] for r in rows))}
    # Reports are copied only when this suite's final summary exists.
    if (folder/'summary.json').exists():
     add(folder/'summary.json')
     for sub in ['tables','figures']:
      for path in (folder/sub).glob('*'):add(path)
   info={'schema':'F2_DATA_SNAPSHOT_1','source_sha256':C.source_identity()['sha256'],
    'created_utc':datetime.now(timezone.utc).isoformat(),'suites':counts,
    'requests':sum(x['requests'] for x in counts.values()),
    'note':'Immutable request copies are authoritative. Rebuild missing JSONL indexes with sync_journal; unfinished suites are not final results.'}
   archive.writestr('snapshot_manifest.json',C.canonical(info)+'\n')
  with zipfile.ZipFile(temp) as archive:
   if archive.testzip() is not None:raise ValueError('Snapshot CRC check failed')
  os.replace(temp,target)
  return {'path':str(target.resolve()),'bytes':target.stat().st_size,**info}

def campaign(args):
 import inputs
 output=args.output;output.mkdir(parents=True,exist_ok=True)
 identity=C.source_identity();env=C.environment()
 plan={'schema':'F2_FULL_CAMPAIGN_1','source_sha256':identity['sha256'],
  'commit':env['git_head'],'protocol_sha256':C.digest(C.PROTOCOL),
  'environment':env,'coverage_workers':args.workers,'suites':ORDER,
  'core_requests':C.PROTOCOL['expected']['core_requests'],
  'core_cap_seconds':C.PROTOCOL['expected']['core_cap_seconds']}
 with run.lock(output/'campaign.lock'):
  C.write(output/'campaign_plan.json',plan,immutable=True)
  def status(**values):
   C.write(output/'campaign_status.json',{'updated_utc':datetime.now(timezone.utc).isoformat(),
    'source_sha256':identity['sha256'],'commit':env['git_head'],**values})
  def command(action,suite=None):
   argv=[sys.executable,str(C.HERE/'run.py'),action,'--output',str(output)]
   if suite:argv+=['--suite',suite]
   if action=='run':argv+=['--workers',str(args.workers)]
   return argv
  def save_checkpoint():
   # Separate low-priority process; coverage workers keep their dedicated CPUs.
   result=subprocess.run([sys.executable,str(C.HERE/'campaign.py'),'--output',str(output),'--snapshot-only'],
    text=True,capture_output=True)
   if result.returncode:raise RuntimeError('Snapshot failed: '+result.stderr[-2000:])
   saved=json.loads(result.stdout)
   if args.checkpoint_hook:
    uploaded=subprocess.run([sys.executable,str(args.checkpoint_hook),saved['path']],text=True,capture_output=True)
    C.write(output/'checkpoint_transfer.json',{'success':uploaded.returncode==0,
     'completed_utc':datetime.now(timezone.utc).isoformat(),'stdout':uploaded.stdout[-4000:],
     'stderr':uploaded.stderr[-2000:]})
    if uploaded.returncode:print(C.canonical({'checkpoint_transfer_failed':True,'local_snapshot':saved['path']}),flush=True)
   return saved
  status(state='PREPARING')
  if not (output/'input_manifest.json').exists():inputs.prepare(output)
  elif C.read(output/'input_manifest.json')!=C.read(C.HERE/'expected_inputs.json'):
   raise ValueError('Prepared input manifest differs from the frozen list')
  gate=output/'preflight_checks.json'
  if not gate.exists() or C.read(gate).get('source_sha256')!=identity['sha256'] or C.read(gate).get('status')!='PASS':
   subprocess.run(command('check'),check=True)
  if args.initialize_only:
   status(state='READY',completed_requests=0,planned_requests=plan['core_requests'])
   print(C.canonical(save_checkpoint()),flush=True);return
  completed=0;last_checkpoint=0;started=time.monotonic()
  try:
   for suite in ORDER:
    dataset,methods,budget,reps,serial=C.SUITES[suite]
    expected=C.PROTOCOL['expected'][dataset]*len(methods)*reps
    for action in ['run','verify','summarize','plot']+(['diagnose'] if suite=='mechanism' else []):
     status(state='RUNNING',suite=suite,action=action,completed_previous_suites=completed,
      planned_requests=plan['core_requests'],elapsed_seconds=time.monotonic()-started)
     print(C.canonical({'suite':suite,'action':action,'planned_requests':expected,'serial':serial}),flush=True)
     with subprocess.Popen(command(action,suite),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=1) as process:
      for line in process.stdout:
       print(line,end='',flush=True)
       try:progress=json.loads(line)
       except json.JSONDecodeError:continue
       if action=='run' and 'done' in progress:
        total=completed+progress['done']
        status(state='RUNNING',suite=suite,action=action,completed_requests=total,
         planned_requests=plan['core_requests'],suite_progress=progress,elapsed_seconds=time.monotonic()-started)
        if not serial and total-last_checkpoint>=args.checkpoint_every:
         saved=save_checkpoint();last_checkpoint=total
         print(C.canonical({'checkpoint_requests':saved['requests']}),flush=True)
      if process.wait():raise RuntimeError(f'{suite}/{action} failed; records are preserved')
    completed+=expected
    saved=save_checkpoint();last_checkpoint=completed
   status(state='COMPLETE',completed_requests=completed,planned_requests=plan['core_requests'],elapsed_seconds=time.monotonic()-started)
   save_checkpoint()
  except BaseException as exc:
   status(state='PAUSED',completed_previous_suites=completed,error=str(exc),elapsed_seconds=time.monotonic()-started)
   raise

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--output',type=Path,default=C.HERE/'runs/formal_f2_20261021')
 ap.add_argument('--workers',type=int,default=2)
 ap.add_argument('--checkpoint-every',type=int,default=500)
 ap.add_argument('--checkpoint-hook',type=Path,help='Optional trusted Python script accepting the completed archive path')
 ap.add_argument('--initialize-only',action='store_true')
 ap.add_argument('--snapshot-only',action='store_true')
 args=ap.parse_args()
 if args.workers<1 or args.checkpoint_every<1:ap.error('Counts must be positive')
 args.output=args.output.resolve()
 if args.snapshot_only:
  os.nice(10);os.sched_setaffinity(0,{C.environment()['physical_cpus'][-1]})
  print(C.canonical(snapshot(args.output)));return
 campaign(args)

if __name__=='__main__':main()
