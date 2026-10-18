"""One isolated analysis or reference replay per Python interpreter."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
RUNTIME = HERE/'runtime'
CODE = RUNTIME/'reproduction/optimized'
AB = CODE/'research/rta/an_ab_joint/ab'
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(RUNTIME/'tools'))


class AnalysisLimit(BaseException):
    def __init__(self, kind):
        self.kind = kind


def stop(signum, _frame):
    raise AnalysisLimit('CPU' if signum == signal.SIGPROF else 'WALL')


def prepare_call(payload):
    from experiments.ab_v7_2.data import validate_case
    case, method = payload['case'], payload['method']
    validate_case(case)
    model = case['model']
    if model['capacity'] is not None or model['background_power'] != 0:
        raise ValueError('This experiment suite requires unlimited capacity and zero background power')
    if payload.get('job') == 'reference':
        certificate = payload['answer']
        if method in ('seq', 'seq_audit'):
            import research_worker
            return lambda: research_worker.verify_seq(case, certificate, reference=True)
        if method in ('production_analytic', 'production_flow', 'deadline', 'fallback'):
            reference = RUNTIME/'reproduction/original_verifier'
            sys.path.insert(0, str(reference))
            sys.path.insert(0, str(reference/'research/rta/an_ab_joint/ab'))
            import ab_structural_rta as sr
            tasks = [sr.model.Task(t['name'], t['C'], t['D'], t['T'], t['power']) for t in model['tasks']]
            return lambda: {'status':'PASS', 'verification':sr.verify_certificate(
                tasks, model['processors'], model['beta'], model['release_floor'], certificate)}
        if method in ('last_block_only', 'progress_only', 'no_charging_gap'):
            import branch_worker
            return lambda: branch_worker.reference_verify(case, certificate)
        import kernel_worker
        return lambda: kernel_worker.reference_verify({'case':case, 'certificate':certificate})
    if method in ('production_analytic', 'production_flow', 'seq'):
        from experiments.ab_v7_2.adapters import prepare
        return prepare(case, {'production_analytic':'v72_analytic', 'production_flow':'v72_flow', 'seq':'seq'}[method])
    if method in ('legacy_audit_full', 'legacy_audit_no_shared_budget'):
        from experiments.ab_v7_2.adapters import prepare
        return prepare(case, 'ablation_'+method.removeprefix('legacy_audit_'))
    if method in ('deadline', 'fallback'):
        sys.path.insert(0, str(AB))
        import ab_structural_rta as sr
        from ab_deadline_certification import deadline_certificate, deadline_then_full
        tasks = [sr.model.Task(t['name'], t['C'], t['D'], t['T'], t['power']) for t in model['tasks']]
        fn = deadline_certificate if method == 'deadline' else deadline_then_full

        def quick():
            result = fn(tasks, model['processors'], model['beta'], model['release_floor'], verify=True)
            certificate = result.get('certificate')
            return {**result, 'raw_status':result['status'],
                'status':'CERTIFIED' if result['proven'] else 'NO_CERTIFICATE',
                'response_bounds':certificate['response_bounds'] if certificate else None,
                'first_bounds':[p[0] for p in certificate['profile']] if certificate else None}
        return quick
    if method == 'affine_completion':
        import affine_completion
        return lambda: affine_completion.analyze(case)
    if method == 'seq_audit':
        import research_worker
        return lambda: research_worker.main({**payload, 'mode':'seq', 'job':'analysis', 'host_cpu':payload['cpu']})
    if method in ('last_block_only', 'progress_only', 'no_charging_gap'):
        import branch_worker
        kernel = branch_worker.optimized()
        return lambda: kernel.analyze(case, method, verify=True)
    import unified_kernel
    if method not in unified_kernel.MODES:
        raise ValueError(f'Unknown method: {method}')
    return lambda: unified_kernel.analyze(case, method, verify=True)


def run(payload):
    os.sched_setaffinity(0, {payload['cpu']})
    limit = payload.get('memory_limit_mib', 1024)*1024*1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    call = prepare_call(payload)
    budget = payload['budget_seconds']
    signal.signal(signal.SIGPROF, stop)
    signal.signal(signal.SIGALRM, stop)
    if payload['clock'] == 'cpu':
        signal.setitimer(signal.ITIMER_PROF, budget)
        signal.setitimer(signal.ITIMER_REAL, budget*2+15)
    wall, cpu = time.perf_counter(), time.process_time()
    try:
        answer = call()
    except AnalysisLimit as exc:
        answer = {'status':'TIMEOUT', 'limit_kind':exc.kind, 'censored':True}
    except MemoryError:
        answer = {'status':'RESOURCE_LIMIT', 'limit_kind':'MEMORY'}
    finally:
        signal.setitimer(signal.ITIMER_PROF, 0)
        signal.setitimer(signal.ITIMER_REAL, 0)
    answer.update(analysis_wall_seconds=time.perf_counter()-wall,
                  analysis_cpu_seconds=time.process_time()-cpu,
                  peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024)
    if payload['method'].startswith('legacy_audit_') and answer['status']=='CERTIFIED' and payload.get('job')!='reference':
        # Export the original audit's already computed endpoint evidence after
        # the analysis timer, so it can be replayed in the reference process.
        details = answer['details']
        encoded = json.dumps(details,sort_keys=True,separators=(',', ':'),ensure_ascii=False)
        answer['certificate'] = {
            'schema':'AB_UNIFIED_ABLATION_POST_FIXED_POINT_1',
            'mode':payload['method'].removeprefix('legacy_audit_'),
            'input_sha256':payload['case']['input_sha256'],
            'profile':list(map(list,zip(answer['first_bounds'],answer['response_bounds']))),
            'first_implied_by_completion':[d['first']['kind']=='FULL_COMPLETION_IMPLIES_FIRST' for d in details],
            'evidence_sha256':hashlib.sha256(encoded.encode()).hexdigest(),
        }
    return answer


if __name__ == '__main__':
    try:
        payload = json.load(sys.stdin)
        result = run(payload)
    except Exception:
        result = {'status':'ERROR', 'traceback':traceback.format_exc()}
    Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
