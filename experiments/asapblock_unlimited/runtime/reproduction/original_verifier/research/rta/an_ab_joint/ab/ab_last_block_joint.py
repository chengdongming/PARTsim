"""AB joint RTA: frozen H/T V2 OR a last-block/remaining-HP-work bound.

All time and scaled energy quantities are exact integers. The model and frozen
analyzers are unchanged. See LAST_BLOCK_PROOF.md for the sufficient predicate,
first-violation argument, and the essential treatment of the virtual blocker.
"""
from functools import cache

import ab_joint
import ab_ht_joint as ht

core, ph = ht.core, ht.ph
VERSION = 'AB_ALL_TASK_LEAST_JOINT_HT_OR_LAST_BLOCK_V3'
MODEL = ab_joint.MODEL + '+HT_OR_LAST_BLOCK_RESERVED_HP'


class LastBlockEngine(ht.HTEngine):
    def __init__(self, tasks, m, beta, e0, rho):
        super().__init__(tasks, m, beta, e0, rho)
        self.work = cache(lambda i, length: core.workload_bound_v9_3(
            self.tasks[i], length, self.rho[i]))

    def clear(self):
        super().clear()
        self.work.cache_clear()

    def network(self, k, w, b, z, blocker):
        """One relaxed last-block history, or a proved impossible branch.

        b: last blocking slot in [0,w); z: target executions strictly before b.
        The tail contains at least g full-HP slots. Reserve ONLY g of them;
        ignoring the others enlarges the history family, as required for safety.
        """
        tasks, m = self.tasks, self.m
        ab_joint.require(ab_joint.integer(k) and k < len(tasks), 'invalid target')
        ab_joint.require(ab_joint.integer(w, tasks[k].wcet) and w <= tasks[k].deadline,
                         'invalid window')
        ab_joint.require(ab_joint.integer(b) and b < w and ab_joint.integer(z)
                         and z <= min(b, tasks[k].wcet-1)
                         and ab_joint.integer(blocker) and blocker <= k, 'invalid branch')
        tail = w-b-1
        g = max(0, tail-(tasks[k].wcet-1-z))
        if sum(min(g, self.work(i, tail)) for i in range(k)) < m*g:
            return None
        nodes = ['source', 'sink', 'N', 'S', 'B', 'T']
        edges = []

        def edge(u, v, lo, hi, cost=0):
            edges.append(ph._EdgeSpec(u+'->'+v, u, v, lo, hi, cost))

        for i, task in enumerate(tasks[:k]):
            all_node, prefix, history = f'a{i}', f'p{i}', f'h{i}'
            nodes.extend((all_node, prefix, history))
            # Only the augmented PREFIX counts the virtual blocked execution.
            # The whole-window bound covers actual executions; subtracting the
            # virtual unit there would double-count a job that runs in the tail.
            cap = self.work(i, b+1) - int(i == blocker)
            if cap < 0:
                return None
            edge('source', all_node, 0, self.work(i, w))
            edge(all_node, prefix, 0, cap, -int(task.power))
            edge(all_node, 'T', 0, min(g, self.work(i, tail)))
            edge(prefix, history, 0, self.work(i, b))
            edge(history, 'N', 0, b-z)
            edge(history, 'S', 0, z)
            if i < blocker:
                edge(prefix, 'B', 0, 1)
        for i in range(k+1, len(tasks)):
            node = f'l{i}'
            nodes.append(node)
            cap = min(self.work(i, b), z)
            edge('source', node, 0, cap, -int(tasks[i].power))
            edge(node, 'S', 0, cap)
        edge('N', 'sink', 0, m*(b-z))
        edge('S', 'sink', 0, (m-1)*z)
        edge('B', 'sink', 0, m-1)
        edge('T', 'sink', m*g, m*g)
        edge('sink', 'source', 0, m*(b-z)+(m-1)*(z+1)+m*g)
        return tuple(nodes), tuple(edges)

    def branch_energy(self, k, w, b, z, blocker):
        network = self.network(k, w, b, z, blocker)
        if network is None:
            return None
        nodes, edges = network
        answer = ph._solve_min_cost_circulation(
            nodes, edges, ph._Deadline(None, ph.time.monotonic))
        if answer.status is ph._FlowStatus.INFEASIBLE:
            return None
        if answer.status is not ph._FlowStatus.OPTIMAL:
            raise RuntimeError(f'last-block flow failure: {answer.status}: {answer.reason}')
        ab_joint.require(type(answer.minimum_cost) is int, 'noninteger flow cost')
        return z*int(self.tasks[k].power) + int(self.tasks[blocker].power) - answer.minimum_cost

    def last_block_window(self, k, w):
        a = self.progress(k, w)
        if a > w:
            return None
        checkpoints = []
        maximum_power = max(int(t.power) for t in self.tasks[:k+1])
        target_power = int(self.tasks[k].power)
        for b in range(w):
            maximum = None
            supply = self.e0+self.beta[b]
            for z in range(min(b, self.tasks[k].wcet-1)+1):
                # The target-blocker network contains every other blocker's
                # allocation: more eligible current HP jobs, and no reserved
                # virtual HP unit. Replace only its fixed blocker power by
                # max(p_0,...,p_k) to obtain an upper bound for ALL blockers.
                target_value = self.branch_energy(k, w, b, z, k)
                if target_value is None:
                    continue
                if target_value > supply:
                    return None
                coarse = target_value + maximum_power-target_power
                if coarse <= supply:
                    maximum = coarse if maximum is None else max(maximum, coarse)
                    continue
                maximum = target_value if maximum is None else max(maximum, target_value)
                for blocker in sorted(range(k), key=lambda i: (-int(self.tasks[i].power), -i)):
                    value = self.branch_energy(k, w, b, z, blocker)
                    # One feasible branch above supply is enough to reject this
                    # sufficient predicate. This is NOT a counterexample trace.
                    if value is not None:
                        if value > supply:
                            return None
                        maximum = value if maximum is None else max(maximum, value)
            checkpoints.append({'b': b, 'energy': maximum, 'supply': supply})
        return {'kind': 'LAST_BLOCK', 'R': w, 'A': a, 'checkpoints': checkpoints}

    def candidate(self, k, lower=None):
        lower = self.tasks[k].wcet if lower is None else lower
        ab_joint.require(ab_joint.integer(lower, self.tasks[k].wcet)
                         and lower <= self.tasks[k].deadline, 'invalid search lower bound')
        old = super().candidate(k)
        if old is not None:
            ab_joint.require(old['R'] >= lower, 'ascending search lower bound violated')
        last = self.tasks[k].deadline if old is None else old['R']-1
        for w in range(lower, last+1):
            detail = self.last_block_window(k, w)
            if detail is not None:
                return detail
        return None if old is None else dict(old, kind='HT_V2')


def _verify_ht_detail(engine, k, w, detail):
    a = engine.progress(k, w)
    ab_joint.require(a <= w and detail.get('A') == a, 'incorrect processor progress')
    hs, points = detail.get('h'), detail.get('checkpoints')
    ab_joint.require(isinstance(hs, list) and isinstance(points, list)
                     and len(hs) == len(points) == a, 'missing HT checkpoint')
    previous = 0
    for q, (h, point) in enumerate(zip(hs, points), 1):
        ab_joint.require(ab_joint.integer(h, previous) and h <= w-a, 'invalid blocking sequence')
        energy = engine.phase(k, q, h)
        supply = engine.e0+engine.beta[q+h-1]
        ab_joint.require(energy is None or energy <= supply, 'unsafe HT checkpoint')
        ab_joint.require(point == {'q': q, 'h': h, 'energy': energy, 'supply': supply},
                         'changed HT checkpoint')
        previous = h


def verify_certificate(tasks, m, beta, e0, cert):
    tasks, beta = ab_joint.normalize(tasks, m, beta, e0)
    ab_joint.require(isinstance(cert, dict) and cert.get('version') == VERSION, 'certificate version')
    ab_joint.require(cert.get('model') == MODEL, 'certificate model')
    ab_joint.require(cert.get('input_sha256') == ab_joint.fingerprint(tasks, m, beta, e0),
                     'certificate/input mismatch')
    rho, responses, details = (cert.get(key) for key in ('residence_bounds', 'response_bounds', 'details'))
    ab_joint.require(all(isinstance(v, list) and len(v) == len(tasks)
                         for v in (rho, responses, details)), 'incomplete certificate')
    ab_joint.require(all(ab_joint.integer(w, t.wcet) and ab_joint.integer(r, w) and r <= t.deadline
                         for t, w, r in zip(tasks, responses, rho)), 'not a joint post-fixed certificate')
    engine = LastBlockEngine(tasks, m, beta, e0, rho)
    kinds = {'HT_V2': 0, 'LAST_BLOCK': 0}
    try:
        for k, (w, detail) in enumerate(zip(responses, details)):
            ab_joint.require(isinstance(detail, dict) and detail.get('R') == w, 'candidate mismatch')
            kind = detail.get('kind')
            ab_joint.require(kind in kinds, 'unknown certificate branch')
            if kind == 'HT_V2':
                _verify_ht_detail(engine, k, w, detail)
            else:
                rebuilt = engine.last_block_window(k, w)
                ab_joint.require(rebuilt is not None and detail == rebuilt, 'unsafe/changed last-block certificate')
            kinds[kind] += 1
    finally:
        engine.clear()
    return {'status': 'PASS', 'tasks': len(tasks), 'branches': kinds}


def least_certificate(tasks, m, beta, e0, max_iterations=None):
    tasks, beta = ab_joint.normalize(tasks, m, beta, e0)
    natural = 1+sum(t.deadline-t.wcet for t in tasks)
    if max_iterations is not None:
        ab_joint.require(ab_joint.integer(max_iterations, 1), 'invalid iteration limit')
    limit = natural if max_iterations is None else min(natural, max_iterations)
    rho = tuple(t.wcet for t in tasks)
    history = []
    output = {'version': VERSION, 'status': None, 'taskset_proven': False,
              'certificate': None, 'history': history}
    for _ in range(limit):
        engine = LastBlockEngine(tasks, m, beta, e0, rho)
        answers = []
        try:
            for k in range(len(tasks)):
                answer = engine.candidate(k, lower=rho[k])
                answers.append(answer)
                if answer is None:
                    break
        finally:
            engine.clear()
        vector = [a['R'] if a is not None else None for a in answers]
        history.append({'rho': list(rho), 'outputs': vector,
                        'evaluated_task_count': len(answers)})
        if answers[-1] is None:
            output.update(status='NO_CERTIFICATE_IN_THIS_FAMILY', first_unproven_task=len(answers))
            return output
        ab_joint.require(all(r <= w for r, w in zip(rho, vector)), 'ascending monotonicity violated')
        if tuple(vector) == rho:
            cert = {'version': VERSION, 'model': MODEL,
                    'input_sha256': ab_joint.fingerprint(tasks, m, beta, e0),
                    'residence_bounds': list(rho), 'response_bounds': vector, 'details': answers}
            check = verify_certificate(tasks, m, beta, e0, cert)
            output.update(status='CERTIFIED', taskset_proven=True, certificate=cert, verification=check)
            return output
        rho = tuple(vector)
    ab_joint.require(limit < natural, 'finite integer iteration did not terminate')
    output['status'] = 'ITERATION_LIMIT'
    return output
