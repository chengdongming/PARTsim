"""AB V4: interval supply restarts with reserved post-block HP work.

V2 and V3 remain frozen. Every admitted window has a checkable witness covering
every last-block position, prior target count, and first unaffordable task.
"""
from functools import lru_cache
import ab_joint
import ab_last_block_joint as v3

ph = v3.ph
VERSION = 'AB_INTERVAL_LAST_BLOCK_JOINT_V4'
MODEL = ab_joint.MODEL + '+INTERVAL_RESTART_RESERVED_HP'


class IntervalEngine(v3.LastBlockEngine):
    def __init__(self, tasks, m, beta, e0, rho, *, intervals=True, reserve=True):
        super().__init__(tasks, m, beta, e0, rho)
        ab_joint.require(type(intervals) is bool and type(reserve) is bool, 'invalid analysis options')
        self.intervals, self.reserve = intervals, reserve
        self.energy = lru_cache(maxsize=200000)(self._interval_energy)

    def clear(self):
        super().clear()
        self.energy.cache_clear()

    def interval_network(self, k, length, past, z, g, blocker):
        ab_joint.require(ab_joint.integer(k) and k < len(self.tasks), 'invalid target')
        ab_joint.require(ab_joint.integer(length, 1) and ab_joint.integer(past) and past < length,
                         'invalid interval geometry')
        ab_joint.require(ab_joint.integer(z) and z <= min(past, self.tasks[k].wcet-1)
                         and ab_joint.integer(g) and g <= length-past-1
                         and ab_joint.integer(blocker) and blocker <= k, 'invalid interval branch')
        tasks, m = self.tasks, self.m
        tail = length-past-1
        if sum(min(g, self.work(i, tail)) for i in range(k)) < m*g:
            return None
        nodes = ['source', 'sink', 'N', 'S', 'B', 'T']
        edges = []
        def edge(u, v, lo, hi, cost=0):
            edges.append(ph._EdgeSpec(u+'->'+v, u, v, lo, hi, cost))
        for i, task in enumerate(tasks[:k]):
            all_node, prefix, history = f'a{i}', f'p{i}', f'h{i}'
            nodes.extend((all_node, prefix, history))
            cap = self.work(i, past+1)-int(i == blocker)
            if cap < 0:
                return None
            edge('source', all_node, 0, self.work(i, length))
            edge(all_node, prefix, 0, cap, -int(task.power))
            edge(all_node, 'T', 0, min(g, self.work(i, tail)))
            edge(prefix, history, 0, self.work(i, past))
            edge(history, 'N', 0, past-z)
            edge(history, 'S', 0, z)
            if i < blocker:
                edge(prefix, 'B', 0, 1)
        for i in range(k+1, len(tasks)):
            node = f'l{i}'
            nodes.append(node)
            cap = min(self.work(i, past), z)
            edge('source', node, 0, cap, -int(tasks[i].power))
            edge(node, 'S', 0, cap)
        edge('N', 'sink', 0, m*(past-z))
        edge('S', 'sink', 0, (m-1)*z)
        edge('B', 'sink', 0, m-1)
        edge('T', 'sink', m*g, m*g)
        edge('sink', 'source', 0, m*(past-z)+(m-1)*(z+1)+m*g)
        return tuple(nodes), tuple(edges)

    def _interval_energy(self, k, length, past, z, g, blocker):
        network = self.interval_network(k, length, past, z, g, blocker)
        if network is None:
            return None
        result = ph._solve_min_cost_circulation(*network, ph._Deadline(None, ph.time.monotonic))
        if result.status is ph._FlowStatus.INFEASIBLE:
            return None
        if result.status is not ph._FlowStatus.OPTIMAL:
            raise RuntimeError(f'interval flow failure: {result.status}: {result.reason}')
        ab_joint.require(type(result.minimum_cost) is int, 'noninteger optimum')
        return z*int(self.tasks[k].power)+int(self.tasks[blocker].power)-result.minimum_cost

    def anchors(self, b):
        # 0 denotes the original release-origin account. Positive ell denotes
        # harvest from b-ell through b-1, and debit only from b-ell+1 onwards.
        # This explicit finite anchor family includes supply jumps and ell=1.
        return [0]+([ell for ell in range(1, b+1)
                     if ell == 1 or self.beta[ell] > self.beta[ell-1]] if self.intervals else [])

    def capacity_upper(self,k,length,past,u,g,blocker):
        """Two counting relaxations, used only when they already prove safety.

        The first keeps a common whole-window HP budget and charges the minimum
        energy opportunity cost of reserving M*g tail units. The second keeps
        independent N/S/B processor capacities. Neither is a feasible schedule.
        """
        tail=length-past-1
        hp=[]
        for i,t in enumerate(self.tasks[:k]):
            whole=self.work(i,length)
            history=self.work(i,past)
            current=int(i<blocker)
            cap=min(whole,self.work(i,past+1)-int(i==blocker),history+current,past+current)
            if cap<0:return None
            tail_cap=min(g,self.work(i,tail))
            free=min(tail_cap,whole-cap)
            hp.append((int(t.power),cap,tail_cap,free,history,current))
        if sum(v[2] for v in hp)<self.m*g:return None
        lp=[(int(t.power),min(u,self.work(i,past))) for i,t in enumerate(self.tasks) if i>k]
        needed=max(0,self.m*g-sum(v[3] for v in hp))
        loss=0
        for power,cap,tail_cap,free,history,current in sorted(hp):
            take=min(needed,tail_cap-free)
            loss+=take*power;needed-=take
        ab_joint.require(needed==0,'counting tail allocation inconsistent')
        coupled=sum(power*cap for power,cap,*_ in hp)-loss+sum(power*cap for power,cap in lp)
        def largest(items,capacity):
            total=0
            for power,cap in sorted(items,reverse=True):
                take=min(capacity,cap);total+=take*power;capacity-=take
                if capacity==0:break
            return total
        regions=largest([(power,min(past-u,history)) for power,_,_,_,history,_ in hp],self.m*(past-u))
        regions+=largest([(power,min(u,history)) for power,_,_,_,history,_ in hp]+lp,(self.m-1)*u)
        regions+=largest([(power,current) for power,_,_,_,_,current in hp],self.m-1)
        return u*int(self.tasks[k].power)+int(self.tasks[blocker].power)+min(coupled,regions)

    def witness(self, k, w, b, z, blocker, ell, *, all_blockers=False, force_exact=False):
        ab_joint.require(ell in self.anchors(b), 'anchor outside the declared family')
        g = max(0, w-b-1-(self.tasks[k].wcet-1-z)) if self.reserve else 0
        if ell == 0:
            length, past, counts, supply = w, b, range(z, z+1), self.e0+self.beta[b]
        else:
            length, past = w-b+ell-1, ell-1
            counts = range(max(0, z-(b-ell+1)), min(z, ell-1)+1)
            supply = self.beta[ell]
        extra = (max(int(t.power) for t in self.tasks[:k+1])-int(self.tasks[k].power)
                 if all_blockers else 0)
        ab_joint.require(not all_blockers or blocker == k, 'invalid blocker domination witness')
        maximum = None
        for u in counts:
            value = None if force_exact else self.capacity_upper(k,length,past,u,g,blocker)
            if force_exact or (value is not None and value+extra>supply):
                value = self.energy(k, length, past, u, g, blocker)
            if value is not None:
                value += extra
                if value > supply:
                    return None
                maximum = value if maximum is None else max(maximum, value)
        point = {'blocker': blocker, 'all_blockers': all_blockers, 'ell': ell,
                 'energy': maximum, 'supply': supply}
        if not force_exact:
            point['bound_method']='CAPACITY_THEN_EXACT_FLOW'
        return point

    def find_witness(self, k, w, b, z, blocker):
        for ell in self.anchors(b):
            result = self.witness(k, w, b, z, blocker, ell)
            if result is not None:
                return result
        return None

    def last_block_window(self, k, w):
        a = self.progress(k, w)
        if a > w:
            return None
        groups = []
        extra = max(int(t.power) for t in self.tasks[:k+1])-int(self.tasks[k].power)
        for b in range(w):
            for z in range(min(b, self.tasks[k].wcet-1)+1):
                target = self.find_witness(k, w, b, z, k)
                if target is None:
                    return None
                if target['energy'] is None or target['energy']+extra <= target['supply']:
                    witnesses = [dict(target, all_blockers=True,
                                      energy=None if target['energy'] is None else target['energy']+extra)]
                else:
                    witnesses = [target]
                    for blocker in sorted(range(k), key=lambda i: (-int(self.tasks[i].power), -i)):
                        witness = self.find_witness(k, w, b, z, blocker)
                        if witness is None:
                            return None
                        witnesses.append(witness)
                groups.append({'b': b, 'z': z, 'witnesses': witnesses})
        return {'kind': 'INTERVAL_LAST_BLOCK', 'R': w, 'A': a, 'groups': groups}


def verify_certificate(tasks, m, beta, e0, cert):
    tasks, beta = ab_joint.normalize(tasks, m, beta, e0)
    ab_joint.require(isinstance(cert, dict) and cert.get('version') == VERSION
                     and cert.get('model') == MODEL, 'certificate version/model')
    ab_joint.require(cert.get('input_sha256') == ab_joint.fingerprint(tasks, m, beta, e0),
                     'certificate/input mismatch')
    settings = cert.get('settings')
    ab_joint.require(isinstance(settings, dict) and set(settings) == {'intervals', 'reserve', 'joint'}
                     and all(type(v) is bool for v in settings.values()), 'invalid settings')
    rho, responses, details = (cert.get(key) for key in ('residence_bounds', 'response_bounds', 'details'))
    ab_joint.require(all(isinstance(v, list) and len(v) == len(tasks)
                         for v in (rho, responses, details)), 'incomplete certificate')
    ab_joint.require(all(ab_joint.integer(w, t.wcet) and ab_joint.integer(r, w) and r <= t.deadline
                         for t, w, r in zip(tasks, responses, rho)), 'not a joint post-fixed certificate')
    engine = IntervalEngine(tasks, m, beta, e0, rho, intervals=settings['intervals'], reserve=settings['reserve'])
    kinds = {'HT_V2': 0, 'INTERVAL_LAST_BLOCK': 0}
    checks = 0
    try:
        for k, (w, detail) in enumerate(zip(responses, details)):
            ab_joint.require(isinstance(detail, dict) and detail.get('R') == w, 'candidate mismatch')
            kind = detail.get('kind')
            ab_joint.require(kind in kinds, 'unknown candidate type')
            if kind == 'HT_V2':
                v3._verify_ht_detail(engine, k, w, detail)
            else:
                a = engine.progress(k, w)
                ab_joint.require(a <= w and detail.get('A') == a, 'processor condition')
                expected = [(b,z) for b in range(w) for z in range(min(b,tasks[k].wcet-1)+1)]
                groups = detail.get('groups')
                ab_joint.require(isinstance(groups,list) and len(groups) == len(expected), 'missing branch group')
                for group, (b,z) in zip(groups,expected):
                    ab_joint.require(isinstance(group,dict) and group.get('b') == b and group.get('z') == z,
                                     'changed branch group')
                    witnesses = group.get('witnesses')
                    ab_joint.require(isinstance(witnesses,list) and witnesses, 'missing witness')
                    covered = set()
                    for point in witnesses:
                        ab_joint.require(isinstance(point,dict) and type(point.get('all_blockers')) is bool
                                         and ab_joint.integer(point.get('blocker')) and point['blocker'] <= k
                                         and ab_joint.integer(point.get('ell')), 'invalid witness')
                        j = point['blocker']
                        ab_joint.require(point.get('bound_method') in (None,'CAPACITY_THEN_EXACT_FLOW'),
                                         'unknown energy-bound method')
                        rebuilt = engine.witness(k,w,b,z,j,point['ell'],all_blockers=point['all_blockers'],
                                                 force_exact='bound_method' not in point)
                        ab_joint.require(rebuilt is not None and point == rebuilt, 'unsafe/changed interval witness')
                        covered.update(range(k+1) if point['all_blockers'] else [j])
                        checks += 1
                    ab_joint.require(covered == set(range(k+1)), 'uncovered blocking task')
            kinds[kind] += 1
    finally:
        engine.clear()
    return {'status': 'PASS', 'tasks': len(tasks), 'kinds': kinds, 'witnesses': checks}


def least_certificate(tasks, m, beta, e0, max_iterations=None, *, intervals=True, reserve=True, joint=True):
    tasks, beta = ab_joint.normalize(tasks,m,beta,e0)
    settings = {'intervals': intervals, 'reserve': reserve, 'joint': joint}
    ab_joint.require(all(type(v) is bool for v in settings.values()), 'invalid settings')
    natural = 1+sum(t.deadline-t.wcet for t in tasks)
    if max_iterations is not None:
        ab_joint.require(ab_joint.integer(max_iterations,1), 'invalid iteration limit')
    limit = min(natural,max_iterations) if max_iterations is not None else natural
    rho = tuple(t.wcet if joint else t.deadline for t in tasks)
    history = []
    out = {'version': VERSION, 'settings': settings, 'status': None, 'taskset_proven': False,
           'certificate': None, 'history': history}
    for _ in range(limit if joint else 1):
        engine = IntervalEngine(tasks,m,beta,e0,rho,intervals=intervals,reserve=reserve)
        answers = []
        try:
            for k,t in enumerate(tasks):
                result = engine.candidate(k,lower=rho[k] if joint else t.wcet)
                answers.append(result)
                if result is None:
                    break
        finally:
            engine.clear()
        vector = [a['R'] if a is not None else None for a in answers]
        history.append({'rho': list(rho),'outputs': vector,'evaluated_task_count': len(answers)})
        if answers[-1] is None:
            out.update(status='NO_CERTIFICATE_IN_THIS_FAMILY',first_unproven_task=len(answers))
            return out
        if joint:
            ab_joint.require(all(r <= w for r,w in zip(rho,vector)), 'ascending monotonicity violated')
        if not joint or tuple(vector) == rho:
            cert = {'version': VERSION,'model': MODEL,'settings': settings,
                    'input_sha256': ab_joint.fingerprint(tasks,m,beta,e0),
                    'residence_bounds': list(rho),'response_bounds': vector,'details': answers}
            out.update(status='CERTIFIED',taskset_proven=True,certificate=cert,
                       verification=verify_certificate(tasks,m,beta,e0,cert))
            return out
        rho = tuple(vector)
    ab_joint.require(limit < natural, 'finite integer iteration did not terminate')
    out['status'] = 'ITERATION_LIMIT'
    return out
