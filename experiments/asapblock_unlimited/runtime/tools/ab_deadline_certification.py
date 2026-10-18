"""Optional deadline-only certification; the V7.2 core stays unchanged.

Import after adding frozen/optimized/research/rta/an_ab_joint/ab to sys.path.
Successful certificates use the existing, verifiable V7.2 format. Failure
means no sufficient certificate, never a proof of deadline violation.
"""
import time
import ab_structural_rta as sr


def deadline_certificate(tasks, m, beta, e0=0, *, verify=True):
    tasks, beta = sr.model.normalize(tasks, m, beta, e0)
    sr.model.require(type(verify) is bool, 'invalid verification setting')
    opts = sr.settings(None, 0, 'analytic', False)
    profile = [[t.deadline, t.deadline] for t in tasks]
    engine = sr.StructuralEngine(tasks, m, beta, e0, profile, **opts)
    details = []
    try:
        for k, task in enumerate(tasks):
            full = engine.window(k, task.wcet, task.deadline)
            if full is None:
                return dict(status='NO_CERTIFICATE_IN_DEADLINE_FAMILY',
                            proven=False, certificate=None,
                            first_unproven_task=k+1,
                            operation_counts=dict(window_evaluations=engine.window_evaluations,
                                                  bound_evaluations=engine.bound_evaluations))
            details.append(dict(first=dict(kind='FULL_COMPLETION_IMPLIES_FIRST',
                                           R=task.deadline), full=full))
        cert = dict(version=sr.VERSION, model=sr.MODEL, settings=opts,
                    scope='ALL_TASKS', input_sha256=sr.fingerprint(tasks, m, beta, e0, opts),
                    profile=profile, response_bounds=[t.deadline for t in tasks],
                    details=details)
        result = dict(status='CERTIFIED', proven=True, certificate=cert,
                      operation_counts=dict(window_evaluations=engine.window_evaluations,
                                            bound_evaluations=engine.bound_evaluations))
    finally:
        engine.clear()
    if verify:
        result['verification'] = sr.verify_certificate(tasks, m, beta, e0, cert)
    return result


def deadline_then_full(tasks, m, beta, e0=0, *, verify=True):
    """Fresh full search on failure. A public time budget can still censor it."""
    start = time.perf_counter()
    fast = deadline_certificate(tasks, m, beta, e0, verify=verify)
    quick_seconds = time.perf_counter()-start
    if fast['proven']:
        fast.update(route='DEADLINE', quick_status=fast['status'],
                    quick_wall_seconds=quick_seconds, fallback_wall_seconds=0.0)
        return fast
    start = time.perf_counter()
    full = sr.least_certificate(tasks, m, beta, e0, capacity=None,
                               background_power=0, bound='analytic',
                               legacy_v4=False, verify=verify)
    full.update(route='FULL_FALLBACK', quick_status=fast['status'],
                quick_wall_seconds=quick_seconds,
                fallback_wall_seconds=time.perf_counter()-start)
    return full
