"""AB analytic refinement using first-execution and completion bounds.

No energy-state enumeration, flow solver, or exact schedulability oracle is
called. Integer sorting/counting bounds relax the V4 last-block constraints.
A failed check is not an unschedulability result. See MILESTONE_PROOF.md.
"""
from functools import lru_cache
import ab_joint as model

VERSION = 'AB_FIRST_EXECUTION_ANALYTIC_V6'
MODEL = model.MODEL + '+FIRST_EXECUTION_AND_COMPLETION'


def noncarry(c, period, length):
    if length <= 0:
        return 0
    jobs, rest = divmod(length, period)
    return jobs*c + min(c, rest)


def workload(c, period, first, full, length):
    """Closed form: at most two carry-age representatives, no age scan."""
    if length == 0:
        return 0
    answer = 0
    for lo, hi, demand in ((0, first-1, c), (first, full-1, c-1)):
        if lo > hi:
            continue
        cap = min(length, demand)
        age = min(hi, max(lo, full-cap))
        value = min(cap, full-age) + noncarry(c, period, length-period+age)
        answer = max(answer, value)
    return min(length, answer)


class AnalyticEngine:
    def __init__(self, tasks, m, beta, e0, profile, *, first_work=True, first_cuts=True):
        self.tasks, self.beta = model.normalize(tasks, m, beta, e0)
        model.require(type(first_work) is bool and type(first_cuts) is bool, 'invalid settings')
        model.require(len(profile) == len(tasks), 'incomplete profile')
        self.profile = tuple(tuple(pair) for pair in profile)
        for t, pair in zip(tasks, self.profile):
            model.require(len(pair) == 2, 'invalid profile pair')
            f, r = pair
            model.require(model.integer(f, 1) and model.integer(r, t.wcet)
                          and f <= r <= t.deadline and (t.wcet > 1 or f == r), 'invalid profile')
        self.m, self.e0 = m, e0
        self.first_work, self.first_cuts = first_work, first_cuts
        self.powers = tuple(int(t.power) for t in tasks)
        self.descending = tuple(sorted(range(len(tasks)), key=lambda i: (-self.powers[i], i)))
        self.ascending = tuple(reversed(self.descending))
        self.work = lru_cache(maxsize=None)(self._work)
        self.bound = lru_cache(maxsize=100000)(self._bound)
        self.bound_evaluations = 0
        self.window_evaluations = 0

    def clear(self):
        self.work.cache_clear()
        self.bound.cache_clear()

    def _work(self, i, length):
        t = self.tasks[i]
        f, r = self.profile[i]
        return workload(t.wcet, t.period, f if self.first_work else r, r, length)

    def _largest(self, caps, capacity):
        value = 0
        for i in self.descending:
            amount = min(capacity, caps.get(i, 0))
            value += amount*self.powers[i]
            capacity -= amount
            if not capacity:
                break
        return value

    def _bound(self, k, length, past, u, g, blocker):
        """Upper bound on past debit plus the augmented current prefix.

        Keep the minimum of (a) necessary tail opportunity cost and
        (b) independently relaxed N/S/B processor-capacity bounds.
        """
        self.bound_evaluations += 1
        tail = length-past-1
        hp = {}
        for i in range(k):
            whole, history = self.work(i, length), self.work(i, past)
            current = int(i < blocker)
            cap = min(whole, self.work(i, past+1)-int(i == blocker),
                      history+current, past+current)
            if cap < 0:
                return None
            tail_cap = min(g, self.work(i, tail))
            free = min(tail_cap, whole-cap)
            hp[i] = (cap, tail_cap, free, history, current)
        if sum(v[1] for v in hp.values()) < self.m*g:
            return None
        lp = {i: min(u, self.work(i, past)) for i in range(k+1, len(self.tasks))}
        needed = max(0, self.m*g-sum(v[2] for v in hp.values()))
        loss = 0
        for i in self.ascending:
            if i in hp:
                cap, tail_cap, free, _, _ = hp[i]
                take = min(needed, tail_cap-free)
                loss += take*self.powers[i]
                needed -= take
        model.require(needed == 0, 'inconsistent tail relaxation')
        coupled = sum(self.powers[i]*v[0] for i, v in hp.items())-loss
        coupled += sum(self.powers[i]*cap for i, cap in lp.items())
        region = self._largest({i: min(past-u, v[3]) for i, v in hp.items()}, self.m*(past-u))
        region += self._largest({**{i: min(u, v[3]) for i, v in hp.items()}, **lp}, (self.m-1)*u)
        region += self._largest({i: v[4] for i, v in hp.items()}, self.m-1)
        return u*self.powers[k]+self.powers[blocker]+min(coupled, region)

    def anchors(self, b):
        return [0]+[ell for ell in range(1, b+1) if ell == 1 or self.beta[ell] > self.beta[ell-1]]

    def witness(self, k, q, w, b, z, blocker, ell, all_blockers=False):
        first = self.profile[k][0] if q > 1 and self.first_cuts else w+1
        g = max(0, w-b-1-(q-1-z))
        if ell == 0:
            length, past, counts, supply = w, b, range(z, z+1), self.e0+self.beta[b]
        else:
            length, past = w-b+ell-1, ell-1
            lower = max(0, z-(b-ell+1))
            upper = min(z, ell-1, z-int(b-ell+1 >= first))
            counts, supply = range(lower, upper+1), self.beta[ell]
        extra = max(self.powers[:k+1])-self.powers[k] if all_blockers else 0
        maximum = None
        for u in counts:
            value = self.bound(k, length, past, u, g, blocker)
            if value is not None:
                value += extra
                if value > supply:
                    return None
                maximum = value if maximum is None else max(maximum, value)
        return dict(blocker=blocker, all_blockers=all_blockers, ell=ell,
                    upper_energy=maximum, supply=supply)

    def find_witness(self, k, q, w, b, z, blocker, all_blockers=False):
        for ell in self.anchors(b):
            result = self.witness(k, q, w, b, z, blocker, ell, all_blockers)
            if result is not None:
                return result
        return None

    def window(self, k, q, w):
        model.require(q in (1, self.tasks[k].wcet) and q <= w <= self.tasks[k].deadline,
                      'invalid milestone/window')
        self.window_evaluations += 1
        necessary = w-q+1
        capacity = sum(min(self.work(i, w), necessary) for i in range(k))
        if capacity >= self.m*necessary:
            return None
        groups = []
        first = self.profile[k][0] if q > 1 and self.first_cuts else w+1
        for b in range(w):
            for z in range(int(b >= first), min(b, q-1)+1):
                common = self.find_witness(k, q, w, b, z, k, True)
                if common is not None:
                    witnesses = [common]
                else:
                    witnesses = []
                    for j in (k, *sorted(range(k), key=lambda i: -self.powers[i])):
                        point = self.find_witness(k, q, w, b, z, j)
                        if point is None:
                            return None
                        witnesses.append(point)
                groups.append(dict(b=b, z=z, witnesses=witnesses))
        return dict(kind='ANALYTIC_WINDOW', q=q, R=w, required_full_hp_slots=necessary,
                    hp_capacity=capacity, groups=groups)

    def candidate(self, k, q, lower, upper):
        for w in range(lower, upper+1):
            result = self.window(k, q, w)
            if result is not None:
                return result
        return None


def verify_certificate(tasks, m, beta, e0, certificate):
    tasks, beta = model.normalize(tasks, m, beta, e0)
    cert = certificate
    model.require(cert.get('version') == VERSION and cert.get('model') == MODEL, 'certificate version/model')
    model.require(cert.get('input_sha256') == model.fingerprint(tasks, m, beta, e0), 'input fingerprint')
    settings = cert.get('settings')
    model.require(isinstance(settings, dict) and set(settings) == {'first_work', 'first_cuts'}
                  and all(type(v) is bool for v in settings.values()), 'invalid settings')
    profile, details = cert.get('profile'), cert.get('details')
    responses = cert.get('response_bounds')
    model.require(isinstance(details, list) and len(details) == len(tasks), 'incomplete details')
    engine = AnalyticEngine(tasks, m, beta, e0, profile, **settings)
    model.require(responses == [d['full']['R'] for d in details], 'response vector')
    try:
        for k, (t, (f, r), d) in enumerate(zip(tasks, profile, details)):
            full = d.get('full')
            model.require(isinstance(full, dict) and model.integer(full.get('R'), t.wcet)
                          and full['R'] <= r, 'not a full post-fixed point')
            rebuilt = engine.window(k, t.wcet, full['R'])
            model.require(rebuilt is not None and rebuilt == full, 'changed full-window evidence')
            first = d.get('first')
            model.require(isinstance(first, dict) and model.integer(first.get('R'), 1)
                          and first['R'] <= f, 'not a first post-fixed point')
            if first.get('kind') == 'FULL_COMPLETION_IMPLIES_FIRST':
                model.require(first == dict(kind='FULL_COMPLETION_IMPLIES_FIRST', R=full['R']), 'invalid implication')
            else:
                rebuilt = engine.window(k, 1, first['R'])
                model.require(rebuilt is not None and rebuilt == first, 'changed first-window evidence')
    finally:
        engine.clear()
    return dict(status='PASS', tasks=len(tasks))


def analytic_certificate(tasks, m, beta, e0, *, first_work=True, first_cuts=True,
                         max_iterations=None, verify=True):
    """Finite inflationary search; no claim of a globally least response bound."""
    tasks, beta = model.normalize(tasks, m, beta, e0)
    model.require(type(first_work) is bool and type(first_cuts) is bool and type(verify) is bool, 'invalid settings')
    natural = 1+sum(t.deadline-t.wcet+(t.deadline-1 if t.wcet > 1 else 0) for t in tasks)
    if max_iterations is not None:
        model.require(model.integer(max_iterations, 1), 'invalid iteration limit')
    limit = natural if max_iterations is None else min(natural, max_iterations)
    settings = dict(first_work=first_work, first_cuts=first_cuts)
    profile = tuple((1 if first_work or first_cuts else t.wcet, t.wcet) for t in tasks)
    history = []
    stats = dict(window_evaluations=0, bound_evaluations=0)
    out = dict(version=VERSION, status=None, taskset_proven=False, certificate=None,
               history=history, statistics=stats)
    for _ in range(limit):
        engine = AnalyticEngine(tasks, m, beta, e0, profile, **settings)
        details, result = [], []
        try:
            for k, t in enumerate(tasks):
                full = engine.candidate(k, t.wcet, profile[k][1], t.deadline)
                if full is None:
                    break
                first = None
                if t.wcet > 1 and (first_work or first_cuts):
                    first = engine.candidate(k, 1, profile[k][0], full['R']-1)
                if first is None:
                    first = dict(kind='FULL_COMPLETION_IMPLIES_FIRST', R=full['R'])
                details.append(dict(first=first, full=full))
                result.append((first['R'], full['R']))
        finally:
            for key in stats:
                stats[key] += getattr(engine, key)
            engine.clear()
        history.append(dict(profile=[list(p) for p in profile], outputs=[list(p) for p in result]))
        if len(result) != len(tasks):
            out.update(status='NO_ANALYTIC_CERTIFICATE', first_unproven_task=len(result)+1)
            return out
        result = tuple(result)
        model.require(all(a <= x and b <= y for (a, b), (x, y) in zip(profile, result)), 'inflationary iteration')
        if result == profile:
            cert = dict(version=VERSION, model=MODEL, input_sha256=model.fingerprint(tasks, m, beta, e0),
                        settings=settings, profile=[list(p) for p in profile],
                        response_bounds=[d['full']['R'] for d in details], details=details)
            out.update(status='CERTIFIED', taskset_proven=True, certificate=cert)
            if verify:
                out['verification'] = verify_certificate(tasks, m, beta, e0, cert)
            return out
        profile = result
    out['status'] = 'ITERATION_LIMIT'
    return out


def hybrid_certificate(tasks, m, beta, e0):
    """Try the analytic certificate first, then the frozen V4 family.

    This union preserves V4 with sufficient resources. It does not subsume V5's
    exponential refinement, and a timeout does not prove unschedulability.
    """
    result = analytic_certificate(tasks, m, beta, e0)
    if result['taskset_proven']:
        return dict(result, route='ANALYTIC')
    import ab_interval_joint as v4
    return dict(v4.least_certificate(tasks, m, beta, e0), route='V4_FALLBACK',
                analytic_status=result['status'])
