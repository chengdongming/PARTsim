"""Exploratory R-only iteration using F_i=r_i-C_i+1; primary methods unchanged."""
from collections import Counter
import time
import unified_kernel as kernel

def analyze(case):
    tasks,beta,m,e0,opts=kernel.model_inputs(case)
    responses=tuple(t.wcet for t in tasks)
    limit=1+sum(t.deadline-t.wcet for t in tasks)
    history=[];counts=Counter();certificate=None
    wall,cpu=time.perf_counter(),time.process_time()
    for iteration in range(limit):
        profile=tuple((r-t.wcet+1,r) for t,r in zip(tasks,responses))
        engine=kernel.UnifiedEngine(tasks,m,beta,e0,profile,mode='full',**opts)
        output=[];full_details=[]
        try:
            for k,t in enumerate(tasks):
                full=engine.full_candidate(k)
                if full is None:break
                output.append(full['R']);full_details.append(full)
            history.append({'input':profile,'output_R':output})
            if len(output)!=len(tasks):
                status='NO_CERTIFICATE'
                break
            assert all(a<=b for a,b in zip(responses,output)),'R-only bottom monotonicity'
            if tuple(output)==responses:
                details=[];implied=[]
                for k,(t,(f,r),full) in enumerate(zip(tasks,profile,full_details)):
                    infer=t.wcet==1
                    first=({'kind':'FULL_COMPLETION_IMPLIES_FIRST','R':r} if infer
                           else engine.window(k,1,f))
                    assert first is not None,'completion-window shift failed'
                    details.append({'first':first,'full':full});implied.append(infer)
                certificate={'schema':'AB_UNIFIED_ABLATION_POST_FIXED_POINT_1',
                    'mode':'full','input_sha256':case['input_sha256'],
                    'profile':[list(p) for p in profile],
                    'first_implied_by_completion':implied,'evidence_sha256':kernel.digest(details)}
                status='CERTIFIED'
                break
            responses=tuple(output)
        finally:
            counts.update(engine.operation_counts);engine.clear()
    else:raise AssertionError('R-only finite lattice failed to stabilize')
    verification=kernel.verify_compact(case,certificate) if certificate else None
    return {'status':status,'response_bounds':[p[1] for p in certificate['profile']] if certificate else None,
        'first_bounds':[p[0] for p in certificate['profile']] if certificate else None,
        'certificate':certificate,'iterations':iteration+1,'history':history,
        'operation_counts':dict(counts),'verification_status':'PASS' if verification else None,
        'analysis_wall_seconds':time.perf_counter()-wall,'analysis_cpu_seconds':time.process_time()-cpu,
        'scope':'Exploratory R-only derived-first iteration; not a frozen primary method or timing benchmark.'}
