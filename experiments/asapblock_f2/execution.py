"""Host allocation only; mathematical inputs, budgets and methods stay frozen."""
import common as C

def allocate(env,requested):
 if requested<1:raise ValueError('Worker counts must be positive')
 cpus=list(env['physical_cpus'])
 quota=env.get('cpu_quota')
 if quota and quota.split()[0]!='max':
  amount,period=map(int,quota.split())
  if amount<period:raise ValueError('At least one full CPU of quota is required')
  cpus=cpus[:amount//period]
 if not cpus:raise ValueError('No available physical CPU within the quota')
 return {'requested_workers':requested,'effective_workers':min(requested,len(cpus)),
  'available_physical_workers':len(cpus),'cpus':cpus[:requested],
  'per_request_address_limit_mib':C.PROTOCOL['memory_limit_mib'],
  'memory_note':'The per-request virtual address limit is not a RAM reservation; check measured peak RSS and available server RAM.'}

def mode(suite,cpus):
 timing=C.SUITES[suite][4]
 parallel=timing and len(cpus)>1
 return {'exclusive_host':timing,'serial':len(cpus)==1,
  'timing_suite':timing,'parallel_timing':parallel,
  'execution_profile':'F2_PARALLEL_TIMING_EXTENSION' if parallel else 'F2',
  'time_scope':'single-core request under parallel host load' if parallel else
   ('isolated serial single-core request' if timing else 'single-core coverage request under parallel host load')}

def add_arguments(parser):
 parser.add_argument('--timing-workers',type=int,default=1,
  help='1 preserves frozen F2 serial timing; >1 explicitly enables the separately labeled parallel-timing extension')
 parser.add_argument('--verification-workers',type=int,
  help='Parallel independent replays, outside primary clocks; defaults to the analysis CPU lanes')

def validate_args(args,parser):
 if args.timing_workers<1 or (args.verification_workers is not None and args.verification_workers<1):
  parser.error('Worker counts must be positive')

def describe(workers,timing_workers,verification_workers=None):
 env=C.environment()
 return {'environment':env,'coverage':allocate(env,workers),'timing':allocate(env,timing_workers),
  'verification':allocate(env,verification_workers or workers),
  'parallel_timing_requested':timing_workers>1,
  'note':'One suite at a time, with concurrent single-core requests inside it. Three repeats of the same input remain sequential on the same CPU.'}
