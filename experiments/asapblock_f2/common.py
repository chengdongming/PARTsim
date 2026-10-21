"""Identity, atomic persistence and the frozen F2 protocol; no RTA changes."""
from pathlib import Path
import gzip
import hashlib
import json
import os
import platform
import subprocess
import importlib.metadata

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LEGACY = HERE.parent / 'asapblock_unlimited'
RUNTIME = LEGACY / 'runtime'
PROTOCOL = json.loads((HERE / 'protocol.json').read_text())
PRODUCTION = ['production_analytic', 'seq']
MECHANISMS = ['full','no_first_cuts','no_first_work','no_shared_budget','completion_only']
LEGACY_METHODS = MECHANISMS + ['affine_completion','last_block_only','progress_only','no_charging_gap','seq']
SUITES = {
 'main': ('main',PRODUCTION,30,1,False),
 'pilot': ('pilot',PRODUCTION,30,1,False),
 'mechanism': ('mechanism',MECHANISMS,60,1,False),
 'scale': ('scale',PRODUCTION,30,1,False),
 'numeric_scale': ('numeric_scale',PRODUCTION,120,3,True),
 'robustness': ('robustness',PRODUCTION,30,1,False),
 'timing': ('timing',PRODUCTION,120,3,True),
 'mechanism_timing': ('mechanism_timing',['full','no_shared_budget','completion_only'],60,3,True),
 'legacy_ablation': ('legacy_ablation',LEGACY_METHODS,60,1,False),
 'constructed': ('constructed',MECHANISMS+['affine_completion','seq'],30,1,False),
}
CORE_SUITES = ['main','mechanism','scale','numeric_scale','robustness','timing','mechanism_timing']
COMPLETED = {'CERTIFIED','NO_CERTIFICATE'}
STATUSES = COMPLETED | {'TIMEOUT','RESOURCE_LIMIT','ERROR','INVALID_CERTIFICATE'}

def canonical(value):
 return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)

def digest(value):
 return hashlib.sha256(canonical(value).encode()).hexdigest()

def read(path):
 path=Path(path)
 if path.suffix=='.gz':
  with gzip.open(path,'rt',encoding='utf-8') as f:return json.load(f)
 return json.loads(path.read_text(encoding='utf-8'))

def write(path,value,immutable=False):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 if path.exists() and immutable:
  if canonical(read(path))!=canonical(value):raise ValueError(f'Refusing to overwrite changed identity: {path}')
  return
 temp=path.with_name(path.name+'.tmp')
 if path.suffix=='.gz':
  with gzip.open(temp,'wt',encoding='utf-8') as f:f.write(canonical(value)+'\n')
 else:temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
 os.replace(temp,path)

def rows(path):
 p=Path(path)
 result=[json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
 if len({x['request_id'] for x in result})!=len(result):raise ValueError('Duplicate request IDs')
 return result

def append(path,value):
 with Path(path).open('a',encoding='utf-8') as f:
  f.write(canonical(value)+'\n');f.flush();os.fsync(f.fileno())

def checkpoint(path,value):
 """Immutable per-request copy; JSONL is an append-only index of these copies."""
 path=Path(path)
 rid=value['request_id']
 if not isinstance(rid,str) or len(rid)!=64 or any(c not in '0123456789abcdef' for c in rid):
  raise ValueError('Invalid request ID for checkpoint')
 target=path.with_suffix('.records')/(rid+'.json')
 write(target,value,immutable=True)
 with target.open('rb') as f:os.fsync(f.fileno())
 fd=os.open(target.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)

def journaled_append(path,value):
 checkpoint(path,value)
 append(path,value)

def record_rows(path):
 """Read both durable copies, rejecting disagreement rather than picking a result."""
 path=Path(path);result=rows(path);by={r['request_id']:r for r in result}
 for saved in sorted(path.with_suffix('.records').glob('*.json')):
  row=read(saved);rid=row['request_id']
  if saved.stem!=rid:raise ValueError('Checkpoint filename does not match request ID')
  if rid in by:
   if canonical(by[rid])!=canonical(row):raise ValueError('Checkpoint and JSONL disagree: '+rid)
  else:result.append(row);by[rid]=row
 return result

def sync_journal(path,snapshots=()):
 """Recover missing index entries without rerunning an already saved request."""
 path=Path(path);result=record_rows(path);by={r['request_id']:r for r in result}
 for row in snapshots:
  rid=row['request_id']
  if rid in by:
   if canonical(by[rid])!=canonical(row):raise ValueError('In-memory and durable results disagree: '+rid)
  else:result.append(row);by[rid]=row
 indexed={r['request_id'] for r in rows(path)}
 for row in result:
  checkpoint(path,row)
  if row['request_id'] not in indexed:
   append(path,row);indexed.add(row['request_id'])
 saved=rows(path)
 if {r['request_id'] for r in saved}!=set(by):raise ValueError('Durable request count did not match journal')
 if any(canonical(r)!=canonical(by[r['request_id']]) for r in saved):raise ValueError('Durable request value changed')
 return saved

def source_identity():
 frozen=read(RUNTIME/'frozen_sources.json')
 for rel,item in frozen.items():
  if hashlib.sha256((LEGACY/rel).read_bytes()).hexdigest()!=item['sha256']:
   raise ValueError('Frozen source changed: '+rel)
 paths=list(HERE.glob('*.py'))+list(HERE.glob('*.json'))+[HERE/'requirements.txt']
 paths+=list(RUNTIME.rglob('*.py'))+list(RUNTIME.rglob('*.json'))+list(RUNTIME.rglob('*.yml'))
 paths+=[LEGACY/'worker.py',LEGACY/'prepare.py',LEGACY/'configs/campaigns.json',LEGACY/'configs/expected_inputs.json']
 files={str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
 return {'sha256':digest(files),'files':files}

def environment():
 affinity=sorted(os.sched_getaffinity(0));physical={}
 for cpu in affinity:
  p=Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
  key=tuple((p/k).read_text().strip() for k in ['physical_package_id','core_id']) if p.exists() else (cpu,)
  physical.setdefault(key,cpu)
 def sysfile(p):return Path(p).read_text().strip() if Path(p).exists() else None
 return {'python':platform.python_version(),'platform':platform.platform(),'affinity':affinity,
  'packages':{name:importlib.metadata.version(name) for name in ['PyYAML','numpy','matplotlib']},
  'physical_cpus':list(physical.values()),'cpu_quota':sysfile('/sys/fs/cgroup/cpu.max'),
  'memory_max':sysfile('/sys/fs/cgroup/memory.max'),
  'system_memory_bytes':int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemTotal:')))*1024,
  'cpu_model':next((s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines() if s.startswith('model name')),None),
  'git_head':subprocess.run(['git','rev-parse','HEAD'],cwd=REPO,capture_output=True,text=True).stdout.strip()}
