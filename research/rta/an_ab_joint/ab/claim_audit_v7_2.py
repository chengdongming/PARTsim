"""Small mechanism audit of frozen V7.2; not a performance benchmark.

The production analyzer is unchanged. Ablations only discard necessary
constraints or sufficient branches, so they cannot strengthen a predicate.
No ablation output is serialized as a production V7.2 certificate.
"""
import argparse
from collections import Counter
import hashlib
from itertools import product
import json
from pathlib import Path
import time

import ab_structural_rta as sr
import ab_milestone_rta as ap


MODES = ('full', 'completion_only', 'no_first_work', 'no_first_cuts',
         'no_shared_budget', 'progress_only', 'last_block_only')


class AuditEngine(sr.StructuralEngine):
    def __init__(self, *args, mode='full', **kwargs):
        if mode not in MODES:
            raise ValueError(mode)
        self.mode = mode
        self.audit_counts = Counter()
        super().__init__(*args, **kwargs)
        self.first_work = mode not in ('completion_only', 'no_first_work')
        self.first_cuts = mode not in ('completion_only', 'no_first_cuts')

    def _account(self, k, q, w, b, z, blocker, past, all_blockers):
        self.audit_counts['account_misses'] += 1
        first = self.profile[k][0] if q > 1 and self.first_cuts else w+1
        lo = max(0, z-(b-past))
        hi = min(z, past, z-int(b-past >= first))
        g = max(0, w-b-1-(q-1-z))
        extra = max(self.powers[:k+1])-self.powers[k] if all_blockers else 0
        maximum = None
        for u in range(lo, hi+1):
            self.audit_counts['u_visits'] += 1
            value = self.bound(k, w-b+past, past, u, g, blocker)
            if value is not None:
                value += extra
                maximum = value if maximum is None else max(maximum, value)
        return (None if maximum is None else
                min(maximum, (past+1)*self.slot_energy_cap))

    def _bound(self, k, length, past, u, g, blocker):
        self.audit_counts['bound_misses'] += 1
        if self.mode != 'no_shared_budget':
            return super()._bound(k, length, past, u, g, blocker)
        if self.options['bound'] != 'analytic':
            raise ValueError('no_shared_budget is an analytic relaxation')
        # Remove only the cross-region budget x_i+y_i<=W_i(L). Keep
        # the prefix bound, necessary tail capacity and N/S/B capacities.
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
            hp[i] = (cap, min(g, self.work(i, tail)), history, current)
        if sum(v[1] for v in hp.values()) < self.m*g:
            return None
        lp = {i: min(u, self.work(i, past)) for i in range(k+1, len(self.tasks))}
        separate = sum(self.powers[i]*v[0] for i, v in hp.items())
        separate += sum(self.powers[i]*cap for i, cap in lp.items())
        region = self._largest({i: min(past-u, v[2]) for i, v in hp.items()}, self.m*(past-u))
        region += self._largest({**{i: min(u, v[2]) for i, v in hp.items()}, **lp}, (self.m-1)*u)
        region += self._largest({i: v[3] for i, v in hp.items()}, self.m-1)
        return u*self.powers[k]+self.powers[blocker]+min(separate, region)

    def witness(self, *args, **kwargs):
        self.audit_counts['anchor_visits'] += 1
        return super().witness(*args, **kwargs)

    def window(self, k, q, w):
        self.audit_counts['window_calls'] += 1
        if self.mode == 'last_block_only':
            return self.last_block_window(k, q, w)
        if self.mode == 'progress_only':
            for detail in self.progress_requirements(k, q, w):
                if self.capacity is None or detail['energy_threshold'] <= self.capacity:
                    return detail
            detail = self.charging_requirement(k, q, w)
            if detail is not None and (self.capacity is None or
                                      detail['prefix_unit_power'] <= self.capacity):
                return detail
            return None
        return super().window(k, q, w)


def audit_run(rows, m, beta, capacity, mode='full', background_power=0,
              return_details=False):
    tasks = [sr.model.Task(str(i+1), *row) for i, row in enumerate(rows)]
    opts = dict(capacity=capacity, background_power=background_power,
                bound='analytic', legacy_v4=False)
    profile = tuple((t.wcet if mode == 'completion_only' else 1, t.wcet) for t in tasks)
    natural = 1+sum(t.deadline-t.wcet+(t.deadline-1 if t.wcet > 1 else 0) for t in tasks)
    counts, history = Counter(), []
    started = time.process_time()
    for iteration in range(natural):
        engine = AuditEngine(tasks, m, beta, 0, profile, mode=mode, **opts)
        output, details = [], []
        try:
            for k, task in enumerate(tasks):
                full = engine.full_candidate(k)
                if full is None:
                    break
                first = (engine.candidate(k, 1, 1, full['R']-1)
                         if task.wcet > 1 and mode != 'completion_only' else None)
                if first is None:
                    first = dict(kind='FULL_COMPLETION_IMPLIES_FIRST', R=full['R'])
                output.append((first['R'], full['R']))
                details.append(dict(first=first, full=full))
            counts.update(engine.audit_counts)
            counts.update({'account_hits': engine.account.cache_info().hits,
                           'bound_hits': engine.bound.cache_info().hits})
        finally:
            engine.clear()
        history.append(dict(input=profile, output=output))
        if len(output) < len(tasks):
            status = 'NO_CERTIFICATE_IN_ABLATED_FAMILY'
            break
        if not all(f <= a and r <= b for (f, r), (a, b) in zip(profile, output)):
            raise AssertionError('bottom iteration not monotone')
        if tuple(output) == profile:
            status = 'POST_FIXED_POINT'
            break
        profile = tuple(output)
    else:
        raise AssertionError('finite iteration did not stabilize')
    result = dict(mode=mode, status=status, profile=output if status == 'POST_FIXED_POINT' else None,
                  last_input=history[-1]['input'], last_output=history[-1]['output'],
                  iterations=iteration+1, operation_counts=dict(counts),
                  cpu_seconds_audit_only=time.process_time()-started,
                  window_kinds=[d['full']['kind'] for d in details])
    if return_details:
        result['details'] = details
        result['history'] = history
    if mode == 'full':
        production = sr.least_certificate(tasks, m, beta, capacity=capacity,
            background_power=background_power, bound='analytic', legacy_v4=False)
        expected = (production.get('certificate') or {}).get('profile')
        normalized = [list(p) for p in result['profile']] if result['profile'] else None
        if expected != normalized:
            raise AssertionError(('audit/production mismatch', expected, normalized))
        result['production_agreement'] = True
    return result


def seed_cases():
    return [dict(name='joint_first_history', rows=[(1,4,5,3),(2,5,7,1),(1,7,9,2)],
                 m=2, beta=[4*(l//2) for l in range(8)], capacity=8),
            dict(name='zero_slot_progress', rows=[(1,5,9,1)]*2,
                 m=2, beta=[2*(l//2) for l in range(6)], capacity=1),
            dict(name='critical_background', rows=[(1,3,3,1),(2,4,5,1)],
                 m=2, beta=[3*l for l in range(5)], capacity=3, background_power=3)]


def run_seeds():
    cases = []
    for case in seed_cases():
        args = {k:v for k,v in case.items() if k != 'name'}
        results = [audit_run(**args, mode=mode) for mode in MODES]
        cases.append(dict(**case, results=results))
    core = Path(sr.__file__).parent
    hashes = {name:hashlib.sha256((core/name).read_bytes()).hexdigest()
              for name in ('ab_structural_rta.py','ab_milestone_rta.py','ab_interval_joint.py')}
    return dict(purpose='MECHANISM_WITNESSES_NOT_PERFORMANCE_EVALUATION',
                analyzer_version=sr.VERSION, source_sha256=hashes, cases=cases)


def shared_budget_enumeration():
    """Independent enumeration of one N/S/B/T allocation, without RTA bounds."""
    # k=3,w=7,b=5,ell=4 gives h=3,L=5,u=0,g=1,j=3.
    powers, whole, past_work, prefix_work, tail_work = [3,1], [2,3], [1,2], [2,2], [1,1]
    answers = {}
    for coupled in (True, False):
        options = []
        for i in range(2):
            feasible = []
            for n,c,t in product(range(4),range(2),range(2)):
                if n > past_work[i] or n+c > prefix_work[i] or t > tail_work[i]:
                    continue
                if coupled and n+c+t > whole[i]:
                    continue
                feasible.append((n,c,t))
            options.append(feasible)
        best, argbest, count = -1, None, 0
        for allocation in product(*options):
            if sum(v[0] for v in allocation)>6 or sum(v[1] for v in allocation)>1:
                continue
            if sum(v[2] for v in allocation)!=2:
                continue
            count += 1
            value = 2+sum(p*(n+c) for p,(n,c,t) in zip(powers,allocation))
            if value > best:
                best,argbest = value,allocation
        answers['coupled' if coupled else 'separate'] = dict(
            upper_energy=best, allocation_n_c_t=argbest, feasible_allocations=count)
    assert answers['coupled']['upper_energy']==7
    assert answers['separate']['upper_energy']==10
    return answers


def first_history_trace():
    """Independent synchronous witness; shows real two-core use, not WCRT."""
    remaining, energy, trace = [1,2,1], 0, []
    for slot in range(5):
        selected, before = [], energy
        for i,cost in enumerate((3,1,2)):
            if not remaining[i]:
                continue
            if energy < cost or len(selected)==2:
                break
            energy -= cost
            remaining[i] -= 1
            selected.append(i+1)
        harvest = 4 if slot%2 else 0
        energy = min(8,energy+harvest)
        trace.append(dict(slot=slot,energy_before=before,selected=selected,
                          raw_harvest=harvest,energy_next=energy,remaining=list(remaining)))
    assert any(len(row['selected'])==2 for row in trace)
    assert remaining==[0,0,0]
    return trace


def checked_mechanisms():
    base = seed_cases()[0]
    rows, beta = base['rows'], base['beta']
    tasks = [sr.model.Task(str(i+1),*r) for i,r in enumerate(rows)]
    profile = [(3,3),(3,5),(7,7)]
    opts = dict(capacity=8,background_power=0,bound='analytic',legacy_v4=False)
    branch_rows = []
    geometries = [('no_first_cuts',1,2,5,4,1,0,2),
                  ('no_shared_budget',2,1,7,5,0,2,4)]
    for mode,k,q,w,b,z,j,ell in geometries:
        record = dict(removed=mode,target=k+1,q=q,w=w,b=b,z=z,j=j+1,ell=ell)
        for current in ('full',mode):
            e = AuditEngine(tasks,2,beta,0,profile,mode=current,**opts)
            record[current] = dict(upper=e.account(k,q,w,b,z,j,ell-1,False),
                                   witness=e.witness(k,q,w,b,z,j,ell))
            if current != 'full':
                record['all_anchor_upper'] = [dict(ell=a,
                    upper=e.account(k,q,w,b,z,j,b if a==0 else a-1,False),
                    supply=beta[b] if a==0 else beta[a]) for a in e.anchors(b)]
                assert e.find_witness(k,q,w,b,z,j) is None
            e.clear()
        branch_rows.append(record)
    assert branch_rows[0]['full']['upper']==3
    assert branch_rows[0]['no_first_cuts']['upper']==6
    assert branch_rows[1]['full']['upper']==7
    assert branch_rows[1]['no_shared_budget']['upper']==10
    # An exact flow solves the same allocation at the selected coupled point.
    exact = sr.StructuralEngine(tasks,2,beta,0,profile,
        capacity=8,background_power=0,bound='flow',legacy_v4=False)
    assert exact.bound(2,5,3,0,1,2)==7
    exact.clear()
    # All candidate deadlines for task 3 fail without the shared budget,
    # even at the smallest HP F/R allowed by the synchronous actual trace.
    e = AuditEngine(tasks,2,beta,0,profile,mode='no_shared_budget',**opts)
    failed_windows = []
    for w in range(1,8):
        b = w-1 if w<7 else 5
        margins = []
        for ell in e.anchors(b):
            upper = e.account(2,1,w,b,0,2,b if ell==0 else ell-1,False)
            supply = beta[b] if ell==0 else beta[ell]
            assert upper is not None and upper>supply
            margins.append(upper-supply)
        assert e.window(2,1,w) is None
        failed_windows.append(dict(w=w,b=b,minimum_anchor_deficit=min(margins)))
    e.clear()
    periods = []
    for period in (7,9,37,1000):
        changed = [*rows[:2],(1,7,period,2)]
        results = [audit_run(changed,2,beta,8,mode)
                   for mode in ('full','no_first_cuts','no_shared_budget','progress_only')]
        assert results[0]['profile']==profile
        assert all(r['profile'] is None for r in results[1:])
        periods.append(dict(period=period,results=results))
    scales = []
    for scale in (1,1000,1000000):
        scaled = [(c,d,t,p*scale) for c,d,t,p in rows]
        result = audit_run(scaled,2,[v*scale for v in beta],8*scale)
        scales.append(dict(energy_scale=scale,result=result))
    assert all(s['result']['operation_counts']==scales[0]['result']['operation_counts']
               for s in scales)
    return dict(branches=branch_rows, no_shared_failed_windows=failed_windows,
                independent_allocation=shared_budget_enumeration(),
                parallel_trace=first_history_trace(),period_checks=periods,
                energy_scaling_checks=scales,
                bounds_tightness='The parallel trace is not a WCRT-attaining trace.')


def finite_profile_contract():
    """Check the ideal B4 offered-energy envelope; not floating-point replay.

    Energy is in arbitrary exact units: 5 per ms outside [5000,15000),
    1 inside; the source is defined only on [0,30000).
    """
    horizon, dark_begin, dark_end = 30000,5000,15000
    prefix = [0]
    for slot in range(horizon):
        prefix.append(prefix[-1]+(1 if dark_begin<=slot<dark_end else 5))
    lengths = (0,1,4999,5000,9999,10000,10001,14999,15000,25000,30000)
    rows = []
    for length in lengths:
        actual = min(prefix[a+length]-prefix[a] for a in range(horizon-length+1))
        formula = min(length,10000)+5*max(0,length-10000)
        assert actual==formula
        rows.append(dict(length=length,enumerated_minimum=actual,formula=formula))
    return dict(domain=[0,horizon],checked_lengths=rows,
                scope='Ideal exact-unit finite-domain contract, not an infinite-supply or C++ floating-point equivalence proof.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = run_seeds()
    result['mechanism_checks'] = checked_mechanisms()
    result['finite_profile_contract'] = finite_profile_contract()
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    for case in result['cases']:
        print(case['name'])
        for r in case['results']:
            print(r['mode'], r['status'], r['profile'])
