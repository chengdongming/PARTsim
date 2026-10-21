"""One fresh interpreter for a frozen analysis, replay, or diagnostics."""
import importlib.util
import json
import os
from pathlib import Path
import resource
import sys
import time
import traceback
from collections import Counter
import common as C

def run(payload):
 os.sched_setaffinity(0,{payload['cpu']})
 cap=payload['memory_limit_mib']*1024**2
 resource.setrlimit(resource.RLIMIT_AS,(cap,cap))
 spec=importlib.util.spec_from_file_location('frozen_f2_adapter',C.LEGACY/'worker.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 start=time.perf_counter();cpu=time.process_time()
 counts=Counter()
 if payload['job'] in ('diagnose','derived_first'):
  import diagnostic_worker
  if payload['job']=='diagnose':call=lambda:diagnostic_worker.diagnose(payload['case'],payload['answer'])
  else:call=lambda:diagnostic_worker.main({**payload,'job':'co_embed','certificate':payload['answer'],'host_cpu':payload['cpu']})
 else:
  call=module.prepare_call(payload)
  if payload['job']=='analysis' and payload['method'] in C.MECHANISMS:
   import unified_kernel
   original=unified_kernel.UnifiedEngine.window
   def counted(self,*args):
    detail=original(self,*args)
    if detail is not None:counts[detail['kind']]+=1
    return detail
   unified_kernel.UnifiedEngine.window=counted
 setup_wall=time.perf_counter()-start;setup_cpu=time.process_time()-cpu
 wall=time.perf_counter();cpu=time.process_time()
 result=call()
 result['public_call_wall_seconds']=time.perf_counter()-wall
 result['public_call_cpu_seconds']=time.process_time()-cpu
 result['solve_clock_scope']='native_core' if 'solve_wall_seconds' in result else 'public_method_including_builtin_checks'
 result.setdefault('solve_wall_seconds',result['public_call_wall_seconds'])
 result.setdefault('solve_cpu_seconds',result['public_call_cpu_seconds'])
 result['setup_wall_seconds']=setup_wall;result['setup_cpu_seconds']=setup_cpu
 if counts:result['passing_window_evidence_counts']=dict(counts)
 result['peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
 return result

if __name__=='__main__':
 try:
  result=run(json.load(sys.stdin))
 except MemoryError:
  result={'status':'RESOURCE_LIMIT','limit_kind':'MEMORY'}
 except Exception:
  result={'status':'ERROR','traceback':traceback.format_exc()}
 Path(sys.argv[1]).write_text(C.canonical(result),encoding='utf-8')
