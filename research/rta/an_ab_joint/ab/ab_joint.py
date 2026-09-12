"""Research AB-SEQ with simultaneous residence bounds for ALL other tasks.

The frozen AB processor term and phase-energy envelope are unchanged. In the
envelope only, each LP task's deadline field is a view of its proposed residence
bound. Original task deadlines still bound the candidate scan and certificate.
No AN scheduling, bypass, suffix, energy supply, or real deadline is imported.
"""
from dataclasses import replace
from functools import cache
from pathlib import Path
import hashlib
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'frozen_ab'))
import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_ph as ph

Task = core.V93Task
VERSION = 'AB_SEQ_ALL_TASK_LEAST_JOINT_V1'
MODEL = 'NO_OVERFLOW_EVERY_RELEASE_FLOOR_DEBIT_BEFORE_HARVEST'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def integer(x, minimum=0):
    return type(x) is int and x >= minimum


def normalize(tasks, m, beta, e0):
    tasks, beta = tuple(tasks), tuple(beta)
    require(tasks and all(isinstance(t, Task) for t in tasks), 'invalid tasks')
    require(len({t.name for t in tasks}) == len(tasks), 'duplicate task names')
    require(integer(m, 1) and integer(e0), 'invalid cores/release floor')
    require(all(t.power.denominator == 1 for t in tasks), 'use exactly scaled integer energy')
    require(len(beta) > max(t.deadline for t in tasks), 'insufficient beta horizon')
    require(beta[0] == 0 and all(integer(v) for v in beta), 'invalid integer beta')
    require(all(a <= b for a, b in zip(beta, beta[1:])), 'nonmonotone beta')
    return tasks, beta


def fingerprint(tasks, m, beta, e0):
    payload = {'tasks': [[t.name, t.wcet, t.period, t.deadline, int(t.power)] for t in tasks],
               'cores': m, 'beta': list(beta), 'release_floor': e0, 'model': MODEL}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class Engine:
    def __init__(self, tasks, m, beta, e0, rho):
        self.tasks, self.beta = normalize(tasks, m, beta, e0)
        self.m, self.e0, self.rho = m, e0, tuple(rho)
        require(len(self.rho) == len(tasks), 'residence vector length')
        require(all(integer(r, t.wcet) and r <= t.deadline for t, r in zip(tasks, rho)),
                'residence outside original C..D')
        # These are ONLY workload views. The actual model and scan use self.tasks.
        self.lp_views = tuple(replace(t, deadline=r) for t, r in zip(tasks, rho))
        self.theta = {t.name: r for t, r in zip(tasks, rho)}
        self.phase = cache(self._phase)
        self.progress = cache(self._progress)

    def clear(self):
        self.phase.cache_clear()
        self.progress.cache_clear()

    def _progress(self, k, w):
        t = self.tasks[k]
        delay = core.processor_delay_definition_scan_v9_3(t, self.tasks[:k], w, self.m, self.theta)
        return t.wcet + delay

    def _phase(self, k, q, h):
        r = ph.phase_energy_envelope_v9_3(
            target=self.tasks[k], hp_tasks=self.tasks[:k], lp_tasks=self.lp_views[k+1:],
            w=self.tasks[k].deadline, q=q, h=h, processors=self.m, theta_by_name=self.theta)
        if r.status is ph.PHEnvelopeStatus.IMPOSSIBLE_PREFIX:
            return None
        if r.status is ph.PHEnvelopeStatus.UNPROVEN_TIMEOUT:
            raise TimeoutError(r.failure_reason)
        require(r.status is ph.PHEnvelopeStatus.OPTIMAL, f'phase solver failure: {r}')
        require(r.energy.denominator == 1, 'noninteger exact phase result')
        return int(r.energy)

    def candidate(self, k):
        t = self.tasks[k]
        for w in range(t.wcet, t.deadline+1):
            a = self.progress(k, w)
            if a > w:
                continue
            previous, budgets, checkpoints = 0, [], []
            for q in range(1, a+1):
                for h in range(previous, w-a+1):
                    g = self.phase(k, q, h)
                    supply = self.e0+self.beta[q+h-1]
                    if g is None or g <= supply:
                        previous = h
                        budgets.append(h)
                        checkpoints.append({'q': q, 'h': h, 'energy': g, 'supply': supply})
                        break
                else:
                    break
            if len(budgets) == a:
                return {'R': w, 'A': a, 'h': budgets, 'checkpoints': checkpoints}
        return None


def evaluate(tasks, m, beta, e0, rho):
    engine = Engine(tasks, m, beta, e0, rho)
    try:
        return [engine.candidate(k) for k in range(len(tasks))]
    finally:
        engine.clear()


def verify_certificate(tasks, m, beta, e0, cert):
    tasks, beta = normalize(tasks, m, beta, e0)
    require(isinstance(cert, dict) and cert.get('version') == VERSION, 'certificate version')
    require(cert.get('input_sha256') == fingerprint(tasks, m, beta, e0), 'certificate/input mismatch')
    rho, responses, details = (cert.get(k) for k in ('residence_bounds', 'response_bounds', 'details'))
    require(all(isinstance(v, list) and len(v) == len(tasks) for v in (rho, responses, details)),
            'incomplete certificate')
    require(all(integer(w, t.wcet) and integer(r, w) and r <= t.deadline
                for t, w, r in zip(tasks, responses, rho)), 'not a joint post-fixed certificate')
    engine = Engine(tasks, m, beta, e0, rho)
    checks = 0
    try:
        for k, (w, detail) in enumerate(zip(responses, details)):
            require(isinstance(detail, dict) and detail.get('R') == w, 'candidate mismatch')
            a = engine.progress(k, w)
            require(a <= w and detail.get('A') == a, 'incorrect processor progress')
            hs, points = detail.get('h'), detail.get('checkpoints')
            require(isinstance(hs, list) and isinstance(points, list) and len(hs) == len(points) == a,
                    'missing progress checkpoint')
            previous = 0
            for q, (h, pt) in enumerate(zip(hs, points), 1):
                require(integer(h, previous) and h <= w-a, 'invalid blocking sequence')
                g = engine.phase(k, q, h)
                supply = e0+beta[q+h-1]
                require(g is None or g <= supply, 'unsafe energy checkpoint')
                require(pt == {'q': q, 'h': h, 'energy': g, 'supply': supply}, 'checkpoint changed')
                previous = h
                checks += 1
    finally:
        engine.clear()
    return {'tasks': len(tasks), 'checkpoints': checks, 'status': 'PASS'}


def certificate(tasks, m, beta, e0, rho, answers):
    cert = {'version': VERSION, 'model': MODEL, 'input_sha256': fingerprint(tasks, m, beta, e0),
            'residence_bounds': list(rho), 'response_bounds': [a['R'] for a in answers], 'details': answers}
    check = verify_certificate(tasks, m, beta, e0, cert)
    return cert, check


def least_certificate(tasks, m, beta, e0, max_iterations=None):
    tasks, beta = normalize(tasks, m, beta, e0)
    natural_limit = 1+sum(t.deadline-t.wcet for t in tasks)
    if max_iterations is not None:
        require(integer(max_iterations, 1), 'invalid iteration limit')
    limit = natural_limit if max_iterations is None else min(natural_limit, max_iterations)
    rho, history = tuple(t.wcet for t in tasks), []
    out = {'version': VERSION, 'taskset_proven': False, 'certificate': None, 'history': history}
    for _ in range(limit):
        answers = evaluate(tasks, m, beta, e0, rho)
        vector = tuple(a['R'] if a is not None else None for a in answers)
        history.append({'rho': list(rho), 'outputs': list(vector)})
        if any(w is None for w in vector):
            out['status'] = 'NO_CERTIFICATE_IN_THIS_FAMILY'
            return out
        require(all(r <= w for r, w in zip(rho, vector)), 'ascending monotonicity violated')
        if vector == rho:
            cert, check = certificate(tasks, m, beta, e0, rho, answers)
            out.update(status='CERTIFIED', taskset_proven=True, certificate=cert, verification=check)
            return out
        rho = vector
    require(limit < natural_limit, 'finite integer search did not terminate')
    out['status'] = 'ITERATION_LIMIT'
    return out


def legacy_seq(tasks, m, beta, e0):
    """Same frozen AB-SEQ math: previous HP responses, all LP residence D."""
    tasks, beta = normalize(tasks, m, beta, e0)
    rho, answers = [t.deadline for t in tasks], []
    for k in range(len(tasks)):
        engine = Engine(tasks, m, beta, e0, rho)
        try:
            a = engine.candidate(k)
        finally:
            engine.clear()
        answers.append(a)
        if a is None:
            return {'status': 'NO_CANDIDATE', 'taskset_proven': False, 'response_bounds': None,
                    'first_unproven_task': k+1}
        rho[k] = a['R']
    return {'status': 'CERTIFIED', 'taskset_proven': True, 'response_bounds': rho}


def materialize(row, cfg):
    from fractions import Fraction
    require(cfg['shared_energy_model'] == 'NO_OVERFLOW_CONSERVATIVE_ACCOUNT', 'wrong account')
    require(cfg['e0_semantics'] == 'LOWER_BOUND_AT_EVERY_ANALYZED_JOB_RELEASE', 'wrong E0 meaning')
    scale = cfg['energy_scale']
    require(integer(scale, 1), 'invalid energy scale')
    def exact(value):
        x = Fraction(value)*scale
        require(x.denominator == 1, 'energy conversion would round')
        return int(x)
    tasks = tuple(Task(t['name'], t['C'], t['D'], t['T'], exact(t['power'])) for t in row['tasks'])
    require(all(a.period <= b.period for a, b in zip(tasks, tasks[1:])), 'not RM ordered')
    beta = tuple(exact(max(Fraction(0), Fraction(cfg['beta_rate'])*(ell-Fraction(cfg['beta_latency']))))
                 for ell in range(max(t.deadline for t in tasks)+1))
    return tasks, beta, exact(cfg['e0'])
