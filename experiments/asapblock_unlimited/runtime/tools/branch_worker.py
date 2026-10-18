"""Branch deletions on the frozen shared kernel, in a fresh process.

Only window selection changes. Both account kernels, work, iteration and
candidate search are inherited byte-for-byte from the frozen source.
"""
from pathlib import Path
import hashlib
import json
import os
import resource
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT
MODES = ('last_block_only', 'progress_only', 'no_charging_gap')


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def retained(engine, k, q, w):
    for detail in engine.progress_requirements(k, q, w):
        if engine.capacity is None or detail['energy_threshold'] <= engine.capacity:
            return detail
    return None


def branch_window(engine, mode, k, q, w):
    if mode == 'last_block_only':
        return engine.last_block_window(k, q, w)
    detail = retained(engine, k, q, w)
    if detail is not None:
        return detail
    if mode == 'progress_only':
        detail = engine.charging_requirement(k, q, w)
        if detail is not None and (engine.capacity is None or detail['prefix_unit_power'] <= engine.capacity):
            return detail
        return None
    if mode == 'no_charging_gap':
        return engine.last_block_window(k, q, w)
    raise ValueError(mode)


def optimized():
    sys.path.insert(0, str(SOURCE / 'tools'))
    import unified_kernel as kernel
    original = kernel.UnifiedEngine
    kernel.MODES = kernel.MODES + MODES

    class BranchEngine(original):
        def window(self, k, q, w):
            if self.mode not in MODES:
                return super().window(k, q, w)
            self.operation_counts['window_calls'] += 1
            self.operation_counts['branch_' + self.mode] += 1
            return branch_window(self, self.mode, k, q, w)

    kernel.UnifiedEngine = BranchEngine
    original_verify = kernel.verify_compact
    kernel.verify_compact = lambda case, certificate: original_verify(case, certificate, engine_type=BranchEngine)
    return kernel


def reference_engine(case, profile, mode):
    repo = SOURCE / 'reproduction/original_verifier'
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / 'research/rta/an_ab_joint/ab'))
    import claim_audit_v7_2 as audit
    if mode == 'no_charging_gap':
        class NoChargingEngine(audit.AuditEngine):
            def charging_requirement(self, k, q, w):
                return None
        cls, reference_mode = NoChargingEngine, 'full'
    else:
        cls, reference_mode = audit.AuditEngine, mode
    m = case['model']
    tasks = [audit.sr.model.Task(t['name'], t['C'], t['D'], t['T'], t['power']) for t in m['tasks']]
    tasks, beta = audit.sr.model.normalize(tasks, m['processors'], m['beta'], m['release_floor'])
    return cls(tasks, m['processors'], beta, m['release_floor'], profile,
               mode=reference_mode, capacity=None, background_power=0, bound='analytic', legacy_v4=False)


def reference_verify(case, cert):
    assert cert['input_sha256'] == case['input_sha256']
    assert cert['mode'] in MODES
    model = case['model']
    assert len(cert['profile']) == len(cert['first_implied_by_completion']) == len(model['tasks'])
    started = time.perf_counter()
    engine = reference_engine(case, cert['profile'], cert['mode'])
    details = []
    try:
        for k, (t, (f, r), implied) in enumerate(zip(model['tasks'], cert['profile'], cert['first_implied_by_completion'])):
            assert 1 <= f <= r <= t['D'] and t['C'] <= r
            full = engine.window(k, t['C'], r)
            assert full is not None, 'original full endpoint failed'
            if implied:
                assert f == r
                first = {'kind': 'FULL_COMPLETION_IMPLIES_FIRST', 'R': r}
            else:
                first = engine.window(k, 1, f)
                assert first is not None, 'original first endpoint failed'
            details.append({'first': first, 'full': full})
        assert digest(details) == cert['evidence_sha256'], 'original endpoint evidence mismatch'
    finally:
        engine.clear()
    return {'status': 'PASS', 'mode': cert['mode'], 'seconds': time.perf_counter() - started,
            'profile': cert['profile'], 'evidence_sha256': cert['evidence_sha256'], 'proof_sha256': digest(cert)}


def reference_analyze(case, mode):
    tasks = case['model']['tasks']
    profile = tuple((1, t['C']) for t in tasks)
    natural = 1 + sum(t['D'] - t['C'] + (t['D'] - 1 if t['C'] > 1 else 0) for t in tasks)
    for iteration in range(natural):
        engine = reference_engine(case, profile, mode)
        output = []
        try:
            for k, t in enumerate(tasks):
                full = engine.full_candidate(k)
                if full is None:
                    break
                first = engine.candidate(k, 1, 1, full['R'] - 1) if t['C'] > 1 else None
                output.append(((first or full)['R'], full['R']))
        finally:
            engine.clear()
        if len(output) != len(tasks):
            return {'status': 'NO_CERTIFICATE', 'profile': None, 'iterations': iteration + 1}
        assert all(f <= a and r <= b for (f, r), (a, b) in zip(profile, output))
        if tuple(output) == profile:
            return {'status': 'CERTIFIED', 'profile': output, 'iterations': iteration + 1}
        profile = tuple(output)
    raise AssertionError('original iteration did not stabilize')


def main(payload):
    os.sched_setaffinity(0, {payload.get('host_cpu', 0)})
    limit = payload.get('memory_limit_mib', 1024) * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    job = payload.get('job', 'analysis')
    if job == 'reference_verify':
        return reference_verify(payload['case'], payload['certificate'])
    if job == 'reference_batch':
        return {'evaluations': [dict(input_sha256=case['input_sha256'], mode=mode, **reference_analyze(case, mode))
                                for case in payload['cases'] for mode in payload['modes']]}
    kernel = optimized()
    if job == 'optimized_batch':
        result = []
        for case in payload['cases']:
            for mode in payload['modes']:
                answer = kernel.analyze(case, mode, verify=True)
                result.append({'input_sha256': case['input_sha256'], 'mode': mode, 'status': answer['status'],
                               'profile': answer['certificate']['profile'] if answer['certificate'] else None,
                               'iterations': answer['iterations']})
        return {'evaluations': result}
    from experiments.ab_v7_2.data import validate_case
    validate_case(payload['case'])
    return kernel.analyze(payload['case'], payload['mode'], verify=True)


if __name__ == '__main__':
    try:
        answer = main(json.load(sys.stdin))
    except Exception:
        answer = {'status': 'ERROR', 'traceback': traceback.format_exc()}
    answer['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    Path(sys.argv[1]).write_text(canonical(answer))
