"""Thin adapters. Each invocation MUST run in a fresh interpreter.

V7.2's frozen core uses the same module names as the root SEQ core; process
isolation prevents sys.modules/import-order leakage and cross-method caches.
Performance timing covers the public method, including its built-in checks.
Ablation timing covers audit_run, with the extra production rerun disabled
uniformly; its seven modes are compared with each other, not production time.
"""
from collections import Counter
from pathlib import Path
import sys

from .data import ROOT

MODES = ('full','completion_only','no_first_work','no_first_cuts',
         'no_shared_budget','progress_only','last_block_only')
PERFORMANCE = ('seq','v72_analytic','v72_flow')
ABLATIONS = tuple('ablation_'+mode for mode in MODES)
METHODS = PERFORMANCE + ABLATIONS


def method_config(method):
    if method not in METHODS:
        raise ValueError(f'unknown method {method}')
    return dict(method=method, bound=('flow' if method == 'v72_flow' else
                                    'analytic' if method != 'seq' else 'PH_FLOW'),
                legacy_v4=False, priority='INPUT_ORDER',
                mode=method.removeprefix('ablation_') if method in ABLATIONS else None,
                timing_family='ablation_audit' if method in ABLATIONS else 'production',
                production_rerun=False,
                builtin_certificate_verification=method.startswith('v72_'))


def applicable(case, method):
    model = case['model']
    return not (method == 'seq' and
                (model['capacity'] is not None or model['background_power'] != 0))


def prepare(case, method):
    """Import and construct one callable before starting the analysis timer."""
    model = case['model']
    rows = [[t[k] for k in ('C','D','T','power')] for t in model['tasks']]
    if method == 'seq':
        import asap_block_rta_v9_3 as core
        import asap_block_rta_v9_3_seq as seq
        if Path(core.__file__).resolve() != ROOT / 'asap_block_rta_v9_3.py':
            raise RuntimeError('SEQ imported a frozen V7 core; use a fresh interpreter')
        tasks = [core.V93Task(t['name'], *row) for t,row in zip(model['tasks'],rows)]

        def run_seq():
            theta, details, counts = {}, [], Counter()
            for k, task in enumerate(tasks):
                result = seq.seq_response_time_v9_3(target=task,
                    hp_tasks=tasks[:k], lp_tasks=tasks[k+1:],
                    processors=model['processors'], theta_by_name=theta,
                    e0=model['release_floor'], beta=model['beta'])
                status = result.solver_status.value
                details.append(dict(task=task.name, status=status,
                    response=result.candidate_response_time,
                    witness_sequence=list(result.witness_sequence)))
                for field in ('checked_w_count','checked_h_count','checked_q_count','envelope_call_count'):
                    counts[field] += getattr(result, field)
                if status != 'CANDIDATE':
                    return dict(status={'NO_CANDIDATE':'NO_CERTIFICATE',
                        'UNPROVEN_TIMEOUT':'TIMEOUT','UNPROVEN_NUMERIC':'ERROR',
                        'UNPROVEN_INTERNAL':'ERROR'}[status], raw_status=status,
                        reason=result.failure_reason, response_bounds=None,
                        first_bounds=None, details=details, operation_counts=dict(counts))
                theta[task.name] = result.candidate_response_time
            return dict(status='CERTIFIED', raw_status='ALL_RECURSIVE_CANDIDATES',
                response_bounds=[theta[t.name] for t in tasks], first_bounds=None,
                details=details, operation_counts=dict(counts))
        return run_seq

    sys.path.insert(0, str(ROOT / 'research/rta/an_ab_joint/ab'))
    import ab_structural_rta as sr
    expected = ROOT / 'research/rta/an_ab_joint/ab/frozen_ab/asap_block_rta_v9_3.py'
    if Path(sr.model.core.__file__).resolve() != expected:
        raise RuntimeError('V7.2 imported the root SEQ core; use a fresh interpreter')
    opts = dict(capacity=model['capacity'],background_power=model['background_power'])
    if method in ABLATIONS:
        from claim_audit_v7_2 import audit_run

        def run_ablation():
            result = audit_run(rows,model['processors'],model['beta'],
                mode=method.removeprefix('ablation_'),e0=model['release_floor'],
                check_production=False,return_details=True,**opts)
            profile = result['profile']
            return dict(status='CERTIFIED' if profile else 'NO_CERTIFICATE',
                raw_status=result['status'], response_bounds=[p[1] for p in profile] if profile else None,
                first_bounds=[p[0] for p in profile] if profile else None,
                iterations=result['iterations'], operation_counts=result['operation_counts'],
                details=result['details'], evidence_kind='ABLATION_POST_FIXED_POINT')
        return run_ablation
    tasks = [sr.model.Task(t['name'],*row) for t,row in zip(model['tasks'],rows)]

    def run_v72():
        result = sr.least_certificate(tasks,model['processors'],model['beta'],
            model['release_floor'],bound=method_config(method)['bound'],
            legacy_v4=False,verify=True,**opts)
        cert = result.get('certificate')
        return dict(status='CERTIFIED' if result['proven'] else 'NO_CERTIFICATE',
            raw_status=result['status'], response_bounds=cert['response_bounds'] if cert else None,
            first_bounds=[p[0] for p in cert['profile']] if cert else None,
            iterations=len(result['history']), certificate=cert,
            verification=result.get('verification'), evidence_kind='PRODUCTION_CERTIFICATE')
    return run_v72
