"""Fresh-process wrapper: shared AB kernel or unchanged SEQ analysis."""
from pathlib import Path
import json, os, resource, sys, time, traceback

ROOT=Path(__file__).resolve().parents[1]

def verify_seq(case, answer, reference=False):
    repo=ROOT/'reproduction'/('original_verifier' if reference else 'optimized')
    sys.path.insert(0,str(repo))
    import asap_block_rta_v9_3 as core
    import asap_block_rta_v9_3_seq as seq
    import asap_block_rta_v9_3_ph as ph
    model=case['model']
    tasks=[core.V93Task(t['name'],t['C'],t['D'],t['T'],t['power']) for t in model['tasks']]
    assert answer['status']=='CERTIFIED' and len(answer['details'])==len(tasks)
    theta={}; checks=0
    for k,(t,d,r) in enumerate(zip(tasks,answer['details'],answer['response_bounds'])):
        assert d['task']==t.name and d['response']==r and t.wcet<=r<=t.deadline
        a=core.processor_progress_v9_3(t,tasks[:k],r,model['processors'],theta)
        sequence=d['witness_sequence']
        seq._validate_seq_certificate(w=r,processor_progress_a=a,maximum_blocking_h=r-a,
            witness_sequence=sequence,witness_h=sequence[-1])
        for q,h in enumerate(sequence,1):
            result=ph.phase_safe_v9_3(target=t,hp_tasks=tasks[:k],lp_tasks=tasks[k+1:],
                w=r,q=q,h=h,processors=model['processors'],theta_by_name=theta,
                e0=model['release_floor'],beta=model['beta'])
            assert result.status is ph.PHSafetyStatus.SAFE
            checks+=1
        theta[t.name]=r
    return {'status':'PASS','phase_checks':checks,'tasks':len(tasks)}

def main(payload):
    os.sched_setaffinity(0,{payload.get('host_cpu',0)})
    limit=payload.get('memory_limit_mib',1024)*1024*1024
    resource.setrlimit(resource.RLIMIT_AS,(limit,limit))
    job=payload.get('job','analysis')
    if job=='seq_verify':
        return verify_seq(payload['case'],payload['answer'],True)
    if payload.get('mode')=='seq':
        repo=ROOT/'reproduction'/'optimized'; sys.path.insert(0,str(repo))
        from experiments.ab_v7_2.data import validate_case
        from experiments.ab_v7_2.adapters import prepare
        validate_case(payload['case'])
        start,cpu=time.perf_counter(),time.process_time()
        call=prepare(payload['case'],'seq')
        answer=call()
        solve_wall,solve_cpu=time.perf_counter()-start,time.process_time()-cpu
        vw,vc=time.perf_counter(),time.process_time()
        verification=verify_seq(payload['case'],answer) if answer['status']=='CERTIFIED' else None
        answer.update(solve_wall_seconds=solve_wall,solve_cpu_seconds=solve_cpu,
            verification_wall_seconds=time.perf_counter()-vw if verification else 0.,
            verification_cpu_seconds=time.process_time()-vc if verification else 0.,
            verification_status='PASS' if verification else None,
            analysis_wall_seconds=time.perf_counter()-start,
            analysis_cpu_seconds=time.process_time()-cpu)
        return answer
    import kernel_worker
    return kernel_worker.main(payload)

if __name__=='__main__':
    try: answer=main(json.load(sys.stdin))
    except Exception: answer={'status':'ERROR','traceback':traceback.format_exc()}
    answer['peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
    Path(sys.argv[1]).write_text(json.dumps(answer,ensure_ascii=False,allow_nan=False))
