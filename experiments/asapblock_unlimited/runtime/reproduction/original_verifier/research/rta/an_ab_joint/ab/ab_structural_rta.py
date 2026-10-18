"""AB conditional certificates with finite storage and optional background load.

Theory-oriented reference implementation. No empirical scalability claim.
See STRUCTURAL_THEORY_V7.md for the model, quantifiers, and proofs.
All inherited analyzers remain frozen.
"""
from functools import lru_cache
from bisect import bisect_left
import hashlib
import json
import ab_joint as model
import ab_milestone_rta as ap
import ab_interval_joint as v4

VERSION = 'AB_STRUCTURAL_JOINT_V7_2'
MODEL = 'SLOTTED_AB_DEBIT_BEFORE_HARVEST_ALL_ORIGIN_RAW_SUPPLY'


def settings(capacity, background_power, bound, legacy_v4):
    model.require(capacity is None or model.integer(capacity), 'invalid capacity')
    model.require(model.integer(background_power), 'invalid background power')
    model.require(bound in ('flow', 'analytic'), 'invalid bound')
    if legacy_v4 is None:
        legacy_v4 = capacity is None and background_power == 0
    model.require(type(legacy_v4) is bool, 'invalid legacy setting')
    model.require(not legacy_v4 or (capacity is None and background_power == 0),
                  'legacy V4 is not a finite-battery/background theorem')
    return dict(capacity=capacity, background_power=background_power,
                bound=bound, legacy_v4=legacy_v4)


def fingerprint(tasks, m, beta, e0, options):
    payload = dict(tasks=[[t.name, t.wcet, t.deadline, t.period, int(t.power)] for t in tasks],
                   m=m, beta=list(beta), release_floor=e0, settings=options, model=MODEL)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class StructuralEngine(ap.AnalyticEngine):
    interval_network = v4.IntervalEngine.interval_network

    def __init__(self, tasks, m, beta, e0, profile, **options):
        tasks, beta = model.normalize(tasks, m, beta, e0)
        self.options = settings(**options)
        self.critical_tasks = tasks
        self.critical_profile = tuple(tuple(p) for p in profile)
        self.capacity = self.options['capacity']
        model.require(self.capacity is None or e0 <= self.capacity, 'release floor exceeds capacity')
        augmented = list(tasks)
        augmented_profile = list(self.critical_profile)
        if self.options['background_power']:
            occupied = {t.name for t in tasks}
            for i in range(m-1):
                name = f'__background_channel_{i}'
                while name in occupied:
                    name += '_'
                occupied.add(name)
                augmented.append(model.Task(name, 1, 1, 1, self.options['background_power']))
                augmented_profile.append((1, 1))
        # The extra entries are envelope channels, never certified real tasks.
        super().__init__(tuple(augmented), m, beta, e0, augmented_profile)
        # Every past slot and the augmented current prefix use at most m units.
        # This physical cap is also valid when a common-blocker relaxation
        # substitutes a different power for the virtual unit.
        self.slot_energy_cap = (m*max((*self.powers[:len(tasks)],
                                      self.options['background_power']))
                                if self.options['background_power'] else
                                sum(sorted(self.powers, reverse=True)[:m]))
        self.legacy = (v4.IntervalEngine(tasks, m, beta, e0, [r for f, r in profile])
                       if self.options['legacy_v4'] else None)
        self.legacy_answer = lru_cache(maxsize=None)(self._legacy_answer)
        self.account = lru_cache(maxsize=100000)(self._account)
        self.threshold = lru_cache(maxsize=None)(
            lambda k, units: sum(sorted(self.powers[:k+1], reverse=True)[:units]))
        # A charging gap depends only on the supply contract and prefix powers,
        # not on the candidate first/completion bounds. None means no further
        # progress is guaranteed within the supplied finite length domain.
        self.charging_gaps = []
        prefix_power = 0
        for power in self.powers[:len(tasks)]:
            prefix_power = max(prefix_power, power)
            gap = bisect_left(self.beta, prefix_power)
            initial = bisect_left(self.beta, max(0, prefix_power-self.e0))
            self.charging_gaps.append((prefix_power,
                gap if gap < len(self.beta) else None,
                initial if initial < len(self.beta) else None))

    def _work(self, i, length):
        if i >= len(self.critical_tasks):
            return length
        return super()._work(i, length)

    def _bound(self, k, length, past, u, g, blocker):
        if self.options['bound'] == 'analytic':
            return super()._bound(k, length, past, u, g, blocker)
        self.bound_evaluations += 1
        return v4.IntervalEngine._interval_energy(self, k, length, past, u, g, blocker)

    def _legacy_answer(self, k):
        return self.legacy.candidate(k) if self.legacy is not None else None

    def clear(self):
        super().clear()
        if self.legacy is not None:
            self.legacy.clear()
        self.legacy_answer.cache_clear()
        self.account.cache_clear()
        self.threshold.cache_clear()

    def anchors(self, b):
        # Independent of beta: stronger supply cannot remove an anchor.
        return range(b+1)

    def _account(self, k, q, w, b, z, blocker, past, all_blockers):
        first = self.profile[k][0] if q > 1 else w+1
        lo = max(0, z-(b-past))
        hi = min(z, past, z-int(b-past >= first))
        g = max(0, w-b-1-(q-1-z))
        extra = max(self.powers[:k+1])-self.powers[k] if all_blockers else 0
        maximum = None
        for u in range(lo, hi+1):
            value = self.bound(k, w-b+past, past, u, g, blocker)
            if value is not None:
                value += extra
                maximum = value if maximum is None else max(maximum, value)
        return (None if maximum is None else
                min(maximum, (past+1)*self.slot_energy_cap))

    def witness(self, k, q, w, b, z, blocker, ell, all_blockers=False):
        model.require(ell in self.anchors(b), 'invalid anchor')
        model.require(not all_blockers or blocker == k, 'invalid common blocker')
        past = b if ell == 0 else ell-1
        supply = self.e0+self.beta[b] if ell == 0 else self.beta[ell]
        upper = self.account(k, q, w, b, z, blocker, past, all_blockers)
        if upper is not None and upper > supply:
            return None
        resets = []
        if self.capacity is not None:
            # Origin: saturation in slots [0,b); restart: [b-ell,b).
            for d in range(b if ell == 0 else ell):
                value = self.account(k, q, w, b, z, blocker, d, all_blockers)
                available = self.capacity+self.beta[d]
                if value is not None and value > available:
                    return None
                resets.append(dict(d=d, upper_energy=value, supply=available))
        return dict(blocker=blocker, all_blockers=all_blockers, ell=ell,
                    upper_energy=upper, supply=supply, saturation_checks=resets)

    def progress_requirements(self, k, q, w):
        """Exact capacity requirements within the two retained-floor tests.

        beta(1) guarantees this floor after every debit/harvest transition.
        delta=0 also requires the same floor at the release boundary. Keeping
        both fixed delta branches avoids losing a witness when supply grows.
        """
        candidates = []
        for delta in (0, 1):
            denial_slots = w-q+1-delta
            if denial_slots <= 0:
                continue
            hp = sum(min(self.work(i, w), denial_slots) for i in range(k))
            units = hp//denial_slots+1
            if units > self.m:
                continue
            energy = self.threshold(k, units)
            if self.beta[1] < energy or (delta == 0 and self.e0 < energy):
                continue
            candidates.append(dict(kind='RETAINED_PROGRESS_WINDOW', q=q, R=w,
                                   discarded_release_slot=delta, denial_slots=denial_slots,
                                   hp_capacity=hp, hp_units_per_denial=units,
                                   energy_threshold=energy))
        return candidates

    def charging_requirement(self, k, q, w):
        """Progress after bounded recharge intervals, including beta(1)=0.

        While the target is pending, a slot without any execution in its
        priority prefix has no lower-priority execution either. A prefix
        execution occurs by initial_gap, and successive such slots are at
        most recharge_gap apart. Count slots, not parallel execution units.
        This is an additional sufficient test, not a complete service model.
        """
        power, gap, initial = self.charging_gaps[k]
        if initial is None or initial >= w:
            return None
        slots = 1 + ((w-1-initial)//gap if gap is not None else 0)
        hp = sum(self.work(i, w) for i in range(k))
        if hp+q > slots:
            return None
        return dict(kind='CHARGING_GAP_WINDOW', q=q, R=w,
                    prefix_unit_power=power, recharge_gap=gap,
                    initial_gap=initial, guaranteed_prefix_slots=slots,
                    hp_workload=hp)

    def last_block_window(self, k, q, w):
        """The saturation-cut predicate, exposed for theory ablation only."""
        model.require(k < len(self.critical_tasks), 'background is not a certified task')
        detail = super().window(k, q, w)
        return None if detail is None else dict(detail, kind='STRUCTURAL_WINDOW')

    def window(self, k, q, w):
        model.require(k < len(self.critical_tasks), 'background is not a certified task')
        model.require(q in (1, self.tasks[k].wcet) and q <= w <= self.tasks[k].deadline,
                      'invalid milestone/window')
        for detail in self.progress_requirements(k, q, w):
            if self.capacity is None or detail['energy_threshold'] <= self.capacity:
                self.window_evaluations += 1
                return detail
        detail = self.charging_requirement(k, q, w)
        if detail is not None and (self.capacity is None or
                                   detail['prefix_unit_power'] <= self.capacity):
            self.window_evaluations += 1
            return detail
        return self.last_block_window(k, q, w)

    def required_window_capacity(self, k, q, w):
        """Minimal capacity for these fixed X,k,q,w in the self-contained union.

        No battery-value scan. Does not minimize over X or w. None denotes
        absence of a certificate in that fixed-window predicate.
        """
        model.require(q in (1, self.tasks[k].wcet) and q <= w <= self.tasks[k].deadline,
                      'invalid milestone/window')
        progress = self.progress_requirements(k, q, w)
        progress_need = min((d['energy_threshold'] for d in progress), default=None)
        charging = self.charging_requirement(k, q, w)
        charging_need = None if charging is None else charging['prefix_unit_power']
        necessary = w-q+1
        hp = sum(min(self.work(i,w),necessary) for i in range(k))
        last_need = 0 if hp < self.m*necessary else None
        first = self.profile[k][0] if q > 1 else w+1
        if last_need is not None:
            for b in range(w):
                for z in range(int(b >= first),min(b,q-1)+1):
                    for j in range(k+1):
                        branch_need = None
                        for ell in self.anchors(b):
                            past = b if ell == 0 else ell-1
                            supply = self.e0+self.beta[b] if ell == 0 else self.beta[ell]
                            upper = self.account(k,q,w,b,z,j,past,False)
                            if upper is not None and upper > supply:
                                continue
                            need = 0
                            for d in range(b if ell == 0 else ell):
                                value = self.account(k,q,w,b,z,j,d,False)
                                if value is not None:
                                    need = max(need,value-self.beta[d])
                            branch_need = need if branch_need is None else min(branch_need,need)
                        if branch_need is None:
                            last_need = None
                            break
                        last_need = max(last_need,branch_need)
                    if last_need is None:break
                if last_need is None:break
        candidates = [b for b in (last_need,progress_need,charging_need) if b is not None]
        return dict(q=q,R=w,last_block_capacity=last_need,progress_capacity=progress_need,
                    charging_capacity=charging_need,
                    required_capacity=min(candidates) if candidates else None)

    def full_candidate(self, k):
        old = self.legacy_answer(k)
        end = self.critical_tasks[k].deadline if old is None else old['R']
        for w in range(self.critical_tasks[k].wcet, end+1):
            detail = self.window(k, self.critical_tasks[k].wcet, w)
            if detail is not None:
                return detail
        return None if old is None else dict(kind='LEGACY_V4', R=old['R'], evidence=old)


def verify_certificate(tasks, m, beta, e0, certificate):
    tasks, beta = model.normalize(tasks, m, beta, e0)
    cert = certificate
    model.require(isinstance(cert, dict) and cert.get('version') == VERSION
                  and cert.get('model') == MODEL, 'certificate identity')
    opts = cert.get('settings')
    model.require(isinstance(opts, dict) and set(opts) ==
                  {'capacity', 'background_power', 'bound', 'legacy_v4'}, 'certificate settings')
    opts = settings(**opts)
    model.require(cert.get('input_sha256') == fingerprint(tasks, m, beta, e0, opts), 'fingerprint')
    profile, details = cert.get('profile'), cert.get('details')
    model.require(isinstance(profile, list) and len(profile) == len(tasks)
                  and isinstance(details, list) and len(details) == len(tasks), 'incomplete certificate')
    expected_scope = 'CRITICAL_PREFIX' if opts['background_power'] else 'ALL_TASKS'
    model.require(cert.get('scope') == expected_scope, 'changed guarantee scope')
    engine = StructuralEngine(tasks, m, beta, e0, profile, **opts)
    try:
        responses = []
        for k, (t, (f, r), detail) in enumerate(zip(tasks, profile, details)):
            full = detail.get('full')
            model.require(isinstance(full, dict) and model.integer(full.get('R'), t.wcet)
                          and full['R'] <= r, 'full post-fixed point')
            if full.get('kind') == 'LEGACY_V4':
                old = engine.legacy_answer(k)
                model.require(old is not None and full == dict(kind='LEGACY_V4', R=old['R'], evidence=old),
                              'invalid legacy evidence')
            else:
                rebuilt = engine.window(k, t.wcet, full['R'])
                model.require(rebuilt is not None and full == rebuilt, 'full window evidence')
            first = detail.get('first')
            model.require(isinstance(first, dict) and model.integer(first.get('R'), 1)
                          and first['R'] <= f, 'first post-fixed point')
            if first.get('kind') == 'FULL_COMPLETION_IMPLIES_FIRST':
                model.require(first == dict(kind='FULL_COMPLETION_IMPLIES_FIRST', R=full['R']),
                              'completion implication')
            else:
                rebuilt = engine.window(k, 1, first['R'])
                model.require(rebuilt is not None and first == rebuilt, 'first window evidence')
            responses.append(full['R'])
        model.require(cert.get('response_bounds') == responses, 'response vector')
    finally:
        engine.clear()
    return dict(status='PASS', scope=expected_scope, certified_tasks=len(tasks))


def least_certificate(tasks, m, beta, e0=0, *, capacity=None, background_power=0,
                      bound='flow', legacy_v4=None, max_iterations=None, verify=True):
    """Least joint certificate in the explicitly selected sufficient family.

    With background_power>0, only the listed highest-priority tasks are proved;
    arbitrary lower-priority work has per-executed-unit energy <= that bound.
    None capacity means no overflow. All units are exact scaled integers.
    """
    tasks, beta = model.normalize(tasks, m, beta, e0)
    opts = settings(capacity, background_power, bound, legacy_v4)
    model.require(capacity is None or e0 <= capacity, 'release floor exceeds capacity')
    model.require(type(verify) is bool, 'invalid verify option')
    natural = 1+sum(t.deadline-t.wcet+(t.deadline-1 if t.wcet > 1 else 0) for t in tasks)
    if max_iterations is not None:
        model.require(model.integer(max_iterations, 1), 'invalid iteration limit')
    limit = natural if max_iterations is None else min(natural, max_iterations)
    profile = tuple((1, t.wcet) for t in tasks)
    history = []
    out = dict(version=VERSION, status=None, proven=False, taskset_proven=False,
               critical_prefix_proven=False, certificate=None, history=history)
    for _ in range(limit):
        engine = StructuralEngine(tasks, m, beta, e0, profile, **opts)
        details, next_profile = [], []
        try:
            for k, t in enumerate(tasks):
                full = engine.full_candidate(k)
                if full is None:
                    break
                first = engine.candidate(k, 1, 1, full['R']-1) if t.wcet > 1 else None
                if first is None:
                    first = dict(kind='FULL_COMPLETION_IMPLIES_FIRST', R=full['R'])
                details.append(dict(first=first, full=full))
                next_profile.append((first['R'], full['R']))
        finally:
            engine.clear()
        history.append(dict(profile=[list(p) for p in profile], outputs=[list(p) for p in next_profile]))
        if len(next_profile) < len(tasks):
            out.update(status='NO_CERTIFICATE_IN_SELECTED_FAMILY', first_unproven_task=len(next_profile)+1)
            return out
        model.require(all(f <= a and r <= b for (f, r), (a, b) in zip(profile, next_profile)),
                      'monotone bottom iteration violated')
        if tuple(next_profile) == profile:
            scope = 'CRITICAL_PREFIX' if background_power else 'ALL_TASKS'
            cert = dict(version=VERSION, model=MODEL, settings=opts, scope=scope,
                        input_sha256=fingerprint(tasks, m, beta, e0, opts),
                        profile=[list(p) for p in profile], response_bounds=[d['full']['R'] for d in details],
                        details=details)
            out.update(status='CERTIFIED', proven=True, taskset_proven=not background_power,
                       critical_prefix_proven=True, certificate=cert)
            if verify:
                out['verification'] = verify_certificate(tasks, m, beta, e0, cert)
            return out
        profile = tuple(next_profile)
    model.require(limit < natural, 'finite lattice iteration failed to stabilize')
    out['status'] = 'ITERATION_LIMIT'
    return out


def capacity_for_certificate(tasks, m, beta, e0, certificate):
    """Minimal battery for the supplied profile and proof-window endpoints.

    Uses last-block cuts, retained progress, and charging-gap progress. Returns
    a finite-storage certificate at that capacity. Neither the physical system
    nor the full joint family is claimed to have this minimum capacity.
    """
    verify_certificate(tasks,m,beta,e0,certificate)
    model.require(not certificate['settings']['legacy_v4'],
                  'capacity extraction requires a self-contained certificate')
    tasks,beta = model.normalize(tasks,m,beta,e0)
    opts = dict(certificate['settings'],capacity=None)
    profile = certificate['profile']
    engine = StructuralEngine(tasks,m,beta,e0,profile,**opts)
    needed,windows = e0,[]
    try:
        for k,detail in enumerate(certificate['details']):
            pairs = [(tasks[k].wcet,detail['full']['R'])]
            if detail['first']['kind'] != 'FULL_COMPLETION_IMPLIES_FIRST':
                pairs.append((1,detail['first']['R']))
            for q,w in pairs:
                requirement = engine.required_window_capacity(k,q,w)
                model.require(requirement['required_capacity'] is not None,
                              'verified structural window lost its capacity witness')
                needed = max(needed,requirement['required_capacity'])
                windows.append(dict(task=k,**requirement))
    finally:
        engine.clear()
    opts['capacity'] = needed
    finite = StructuralEngine(tasks,m,beta,e0,profile,**opts)
    details = []
    try:
        for k,old in enumerate(certificate['details']):
            full = finite.window(k,tasks[k].wcet,old['full']['R'])
            first = (dict(old['first']) if old['first']['kind'] == 'FULL_COMPLETION_IMPLIES_FIRST'
                     else finite.window(k,1,old['first']['R']))
            model.require(full is not None and first is not None,'capacity transfer failed')
            details.append(dict(full=full,first=first))
    finally:
        finite.clear()
    transferred = dict(version=VERSION,model=MODEL,settings=opts,scope=certificate['scope'],
                       input_sha256=fingerprint(tasks,m,beta,e0,opts),
                       profile=[list(p) for p in profile],
                       response_bounds=[d['full']['R'] for d in details],details=details)
    return dict(status='CAPACITY_FOR_FIXED_WINDOWS',required_capacity=needed,
                minimum_scope='FIXED_PROFILE_AND_WINDOWS_SUFFICIENT_PREDICATE',
                window_requirements=windows,certificate=transferred,
                verification=verify_certificate(tasks,m,beta,e0,transferred))
