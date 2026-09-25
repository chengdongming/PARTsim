"""Small independent theory checks; no performance/acceptance-rate campaign.

The finite-storage graph below imports no RTA transition selector, workload,
flow, or existing exact oracle. It checks integer harvests only. General real
harvest soundness rests on STRUCTURAL_THEORY_V7.md, not this finite audit.
"""
from collections import deque
from copy import deepcopy
from itertools import product
import argparse
import json
import random
import unittest

import ab_structural_rta as sr
import ab_milestone_rta as ap
import ab_interval_joint as v4
from test_ab_milestone_rta import allocation_dp, age_scan

COUNTS = {}


def finite_graph(values, m, capacity, quantum, period, bounds, *, e0=0,
                 background_power=0, max_states=200000):
    """Exhaustive finite graph for beta(L)=quantum*floor(L/period).

    values=(C,D,T,p). Empty critical queues, any initial energy, all optional
    sporadic releases and demands 1..C. Arbitrary lower-priority work may spend
    any integer amount <= power_bound*free_cores after the critical prefix.
    Larger harvests quotient to max(capacity, quantum); stored supply memory
    is clipped at quantum. Every explored prefix admits a legal continuation.
    """
    n = len(values)
    empty = ((0, 0, 0, False),)*n  # cooldown, remaining, age, has_executed
    roots = [(empty, energy, ()) for energy in range(capacity+1)]
    seen = set(roots)
    queue = deque(roots)
    transitions = 0
    while queue:
        jobs, energy, hist = queue.popleft()
        release_choices = [range(c+1) if cd == 0 and rem == 0 and energy >= e0 else (0,)
                           for (c, d, t, p), (cd, rem, age, started) in zip(values, jobs)]
        for releases in product(*release_choices):
            current = [(t, demand, 0, False) if demand else job
                       for (c, d, t, p), job, demand in zip(values, jobs, releases)]
            left_energy, free = energy, m
            executed, blocked = set(), False
            for i, ((c, d, t, power), (cd, rem, age, started)) in enumerate(zip(values, current)):
                if not rem:
                    continue
                if free == 0:
                    break
                if power > left_energy:
                    blocked = True
                    break
                executed.add(i)
                left_energy -= power
                free -= 1
            following = []
            for i, ((cd, rem, age, started), (f, r)) in enumerate(zip(current, bounds)):
                remaining = rem-int(i in executed)
                now_started = started or i in executed
                now_age = age+1 if rem else 0
                if remaining and (now_age >= r or (not now_started and now_age >= f)):
                    return dict(status='COUNTEREXAMPLE', states=len(seen), transitions=transitions,
                                task=i, age=now_age, energy=energy, jobs=current,
                                releases=list(releases), executed=sorted(executed))
                following.append((max(0, cd-1), remaining,
                                  now_age if remaining else 0, now_started if remaining else False))
            bg_limit = 0 if blocked else min(left_energy, free*background_power)
            for bg_debit in range(bg_limit+1):
                for harvest in range(max(capacity, quantum)+1):
                    clipped = min(quantum, harvest)
                    if len(hist) == period-1 and sum(hist)+clipped < quantum:
                        continue
                    new_hist = (hist+(clipped,))[-(period-1):] if period > 1 else ()
                    state = (tuple(following), min(capacity, left_energy-bg_debit+harvest), new_hist)
                    transitions += 1
                    if state not in seen:
                        if len(seen) >= max_states:
                            return dict(status='UNKNOWN_STATE_LIMIT', states=len(seen), transitions=transitions)
                        seen.add(state)
                        queue.append(state)
    return dict(status='CLOSED_SAFE_GRAPH', states=len(seen), transitions=transitions)


def task_list(values):
    return [sr.model.Task(str(i), *row) for i, row in enumerate(values)]


def charging_slot_dp(powers, m, capacity, quantum, period, initial, horizon, bg):
    """Independent prefix scheduler; minimize active slots over all choices.

    The last critical task stays pending. HP readiness can vary arbitrarily.
    This oracle imports no RTA workload, selector, gap formula, or flow code.
    """
    states = {(initial, ()): 0}
    minima = []
    for _ in range(horizon):
        following = {}
        for (energy, history), active in states.items():
            for ready in product((False, True), repeat=len(powers)-1):
                left, free, selected, blocked = energy, m, [], False
                for i, present in enumerate((*ready, True)):
                    if not present:
                        continue
                    if free == 0:
                        break
                    if left < powers[i]:
                        blocked = True
                        break
                    left -= powers[i]
                    free -= 1
                    selected.append(i)
                bg_limit = min(left, free*bg) if not blocked else 0
                for debit in range(bg_limit+1):
                    for harvest in range(max(capacity, quantum)+1):
                        clipped = min(quantum, harvest)
                        if len(history) == period-1 and sum(history)+clipped < quantum:
                            continue
                        memory = (history+(clipped,))[-(period-1):] if period > 1 else ()
                        key = (min(capacity, left-debit+harvest), memory)
                        count = active+bool(selected)
                        following[key] = min(following.get(key, count), count)
        states = following
        minima.append(min(states.values()))
    return minima


class StructuralTests(unittest.TestCase):
    def test_charging_gap_independent_slot_dp(self):
        checks = configurations = 0
        for powers in ((1,), (1,1), (2,1), (1,2,1)):
            ceiling = max(powers)
            for m,period,bg in ((1,2,0),(2,2,2),(2,3,1)):
                for initial in (0,ceiling):
                    minima = charging_slot_dp(powers,m,ceiling,ceiling,period,
                                              initial,7,bg)
                    first = 0 if initial >= ceiling else period
                    for w,actual in enumerate(minima,1):
                        guaranteed = 0 if w <= first else 1+(w-1-first)//period
                        self.assertGreaterEqual(actual,guaranteed)
                        checks += 1
                    configurations += 1
        # Debit-before-harvest: a release at zero with no energy cannot use
        # the first recharge burst until the following decision boundary.
        self.assertEqual(charging_slot_dp((1,),1,1,1,2,0,3,0),[0,0,1])
        COUNTS.update(charging_dp_configurations=configurations,
                      charging_dp_window_checks=checks)

    def test_zero_single_slot_supply_strict_family(self):
        cases = graphs = 0
        for n,period in product(range(2,5),(2,3)):
            deadline = n*period+1
            values = [(1,deadline,2*n*period+1,1)]*n
            tasks = task_list(values)
            beta = [n*(l//period) for l in range(deadline+1)]
            expected = [[(i+1)*period+1]*2 for i in range(n)]
            for method in ('flow','analytic'):
                out = sr.least_certificate(tasks,n,beta,capacity=1,bound=method,
                                          legacy_v4=False)
                self.assertTrue(out['proven'])
                self.assertEqual(out['certificate']['profile'],expected)
                engine = sr.StructuralEngine(tasks,n,beta,0,expected,capacity=1,
                    background_power=0,bound=method,legacy_v4=False)
                try:
                    for w in range(1,deadline+1):
                        self.assertIsNone(engine.last_block_window(1,1,w))
                        self.assertEqual(engine.progress_requirements(1,1,w),[])
                finally:
                    engine.clear()
                transfer = sr.capacity_for_certificate(tasks,n,beta,0,out['certificate'])
                self.assertEqual(transfer['required_capacity'],1)
                for field in ('initial_gap','recharge_gap','guaranteed_prefix_slots','hp_workload'):
                    cert = deepcopy(out['certificate'])
                    cert['details'][1]['full'][field] += 1
                    with self.assertRaises(ValueError):
                        sr.verify_certificate(tasks,n,beta,0,cert)
                cases += 1
            if n == 2 and period == 2:
                graph = finite_graph(values,n,1,n,period,expected)
                self.assertEqual(graph['status'],'CLOSED_SAFE_GRAPH',graph)
                graphs += 1
                COUNTS['zero_floor_graph_states'] = graph['states']
                COUNTS['zero_floor_graph_transitions'] = graph['transitions']
        # Positive release reserve can give one immediate prefix slot even
        # when the finite supply contract provides no recharge guarantee.
        tasks = task_list([(1,1,2,1)])
        out = sr.least_certificate(tasks,1,[0,0],1,capacity=1,legacy_v4=False)
        self.assertTrue(out['proven'])
        self.assertEqual(out['certificate']['response_bounds'],[1])
        self.assertIsNone(out['certificate']['details'][0]['full']['recharge_gap'])
        COUNTS.update(zero_floor_family_analysis_runs=cases,
                      zero_floor_independent_graphs=graphs)

    def test_retained_progress_counting_and_small_battery_family(self):
        checks = denials = 0
        for n in range(1,5):
            for powers in product((1,2,3),repeat=n):
                for m in range(1,4):
                    for units in range(1,m+1):
                        threshold = sum(sorted(powers,reverse=True)[:units])
                        for ready_hp in product((False,True),repeat=n-1):
                            for initial in (threshold,threshold+1):
                                energy,selected = initial,[]
                                for i,ready in enumerate((*ready_hp,True)):
                                    if not ready:continue
                                    if len(selected)==m or energy<powers[i]:break
                                    energy-=powers[i];selected.append(i)
                                if n-1 not in selected:
                                    self.assertGreaterEqual(len(selected),units)
                                    denials += 1
                                checks += 1
        for m in range(2,7):
            values=[(1,m+1,2*m+1,1)]*m
            tasks=task_list(values);beta=[m*l for l in range(m+2)]
            out=sr.least_certificate(tasks,m,beta,capacity=1)
            expected=[[i+2,i+2] for i in range(m)]
            self.assertTrue(out['proven']);self.assertEqual(out['certificate']['profile'],expected)
            e=sr.StructuralEngine(tasks,m,beta,0,expected,capacity=1,
                                  background_power=0,bound='flow',legacy_v4=False)
            try:
                self.assertTrue(all(e.last_block_window(1,1,w) is None for w in range(1,m+2)))
            finally:e.clear()
            if m<=3:
                graph=finite_graph(values,m,1,m,1,expected)
                self.assertEqual(graph['status'],'CLOSED_SAFE_GRAPH')
        self.assertGreater(denials,0)
        COUNTS.update(retained_progress_local_choices=checks,retained_progress_denials=denials,
                      small_battery_family_sizes=5)

    def test_fixed_window_capacity_is_not_physical_minimum(self):
        cases=[([(1,3,4,1),(1,3,4,1)],2,[0,2,4,6],0),
               ([(1,4,5,3),(2,5,7,1),(1,7,9,2)],2,[4*(l//2) for l in range(8)],0),
               ([(1,3,3,1),(2,4,5,1)],2,[0,3,6,9,12],3)]
        checks=0
        for values,m,beta,bg in cases:
            tasks=task_list(values)
            for method in ('flow','analytic'):
                source=sr.least_certificate(tasks,m,beta,background_power=bg,bound=method,legacy_v4=False)
                self.assertTrue(source['proven'])
                transfer=sr.capacity_for_certificate(tasks,m,beta,0,source['certificate'])
                need=transfer['required_capacity']
                self.assertGreater(need,0)
                # Brute capacity scan is used only here, independently of the extraction.
                for capacity in range(need+2):
                    engine=sr.StructuralEngine(tasks,m,beta,0,source['certificate']['profile'],
                                               capacity=capacity,background_power=bg,bound=method,legacy_v4=False)
                    try:
                        passes=all(engine.window(row['task'],row['q'],row['R']) is not None
                                   for row in transfer['window_requirements'])
                        self.assertEqual(passes,capacity>=need)
                        checks+=1
                    finally:engine.clear()
        tasks=task_list(cases[0][0]);beta=cases[0][2]
        fast=sr.least_certificate(tasks,2,beta,legacy_v4=False)
        slow=sr.least_certificate(tasks,2,beta,capacity=1)
        self.assertEqual(sr.capacity_for_certificate(tasks,2,beta,0,fast['certificate'])['required_capacity'],2)
        self.assertEqual(sr.capacity_for_certificate(tasks,2,beta,0,slow['certificate'])['required_capacity'],1)
        self.assertEqual(slow['certificate']['response_bounds'],[2,3])
        for field in ('energy_threshold','discarded_release_slot'):
            changed=deepcopy(slow['certificate'])
            self.assertEqual(changed['details'][1]['full']['kind'],'RETAINED_PROGRESS_WINDOW')
            changed['details'][1]['full'][field]=0
            with self.assertRaises(ValueError):sr.verify_certificate(tasks,2,beta,0,changed)
        COUNTS['fixed_window_capacity_scan_checks']=checks

    def test_exact_saturation_expansion(self):
        checks = 0
        for capacity in range(4):
            for initial in range(capacity+1):
                queue = [(initial, [], [])]
                for _ in range(4):
                    following = []
                    for energy, debits, harvests in queue:
                        for debit in range(energy+1):
                            for harvest in range(4):
                                ds, hs = debits+[debit], harvests+[harvest]
                                actual = min(capacity, energy-debit+harvest)
                                expansion = min([initial+sum(hs)-sum(ds)]+
                                                [capacity+sum(hs[s+1:])-sum(ds[s+1:])
                                                 for s in range(len(ds))])
                                self.assertEqual(actual, expansion)
                                # Restart excludes the first debit, but includes every reset.
                                for ell in range(1, len(ds)+1):
                                    base = sum(hs[-ell:])-sum(ds[len(ds)-ell+1:])
                                    resets = [capacity+sum(hs[len(ds)-d:])-sum(ds[len(ds)-d:])
                                              for d in range(ell)]
                                    self.assertGreaterEqual(actual, min([base]+resets))
                                    checks += 1
                                following.append((actual, ds, hs))
                    queue = following
        COUNTS['energy_expansion_and_restart_inequalities'] = checks

    def test_workload_and_flow_monotonicity(self):
        work_checks = 0
        for c in range(1,4):
            for period in range(c,7):
                profiles = [(f,r) for r in range(c,period+1)
                            for f in (range(1,r+1) if c > 1 else (r,))]
                for small, large in product(profiles, repeat=2):
                    if all(a <= b for a,b in zip(small,large)):
                        for length in range(10):
                            a = ap.workload(c,period,*small,length)
                            b = ap.workload(c,period,*large,length)
                            self.assertEqual(a,age_scan(c,period,*small,length))
                            self.assertLessEqual(a,b)
                            work_checks += 1
        rng = random.Random(20260922)
        allocation_checks = 0
        monotone_checks = 0
        for _ in range(180):
            values, profile, bigger = [], [], []
            for i in range(rng.randint(1,4)):
                c = rng.randint(1,2)
                d = rng.randint(c,5)
                values.append((c,d,rng.randint(d,6),rng.randint(1,4)))
                r = rng.randint(c,d)
                f = rng.randint(1,r) if c > 1 else r
                rr = rng.randint(r,d)
                ff = rng.randint(f,rr) if c > 1 else rr
                profile.append((f,r)); bigger.append((ff,rr))
            tasks = task_list(values)
            m, k, length = rng.randint(1,3), rng.randrange(len(tasks)), rng.randint(1,5)
            past = rng.randrange(length)
            u = rng.randint(0,min(past,tasks[k].wcet-1))
            g, j = rng.randint(0,length-past-1), rng.randrange(k+1)
            exact = allocation_dp(tasks,m,profile,k,length,past,u,g,j)
            small_values = {}
            for method in ('flow','analytic'):
                engines = [sr.StructuralEngine(tasks,m,[0]*7,0,p,capacity=None,
                                              background_power=0,bound=method,legacy_v4=False)
                           for p in (profile,bigger)]
                try:
                    small, large = [e.bound(k,length,past,u,g,j) for e in engines]
                    if small is not None:
                        self.assertIsNotNone(large)
                        self.assertLessEqual(small,large)
                    small_values[method] = small
                    monotone_checks += 1
                finally:
                    for e in engines: e.clear()
            self.assertEqual(exact,small_values['flow'])
            if exact is not None:
                self.assertIsNotNone(small_values['analytic'])
                self.assertLessEqual(exact,small_values['analytic'])
            allocation_checks += 1
        COUNTS.update(workload_order_checks=work_checks, allocation_dp_checks=allocation_checks,
                      envelope_order_checks=monotone_checks)

    def test_least_joint_profiles(self):
        cases = [([(1,3,3,1),(2,4,5,1)],2,3,1,3,0),
                 ([(1,3,4,2),(2,4,5,1)],2,3,1,4,0),
                 ([(2,4,4,1),(1,4,5,2)],2,3,2,4,0),
                 ([(1,3,3,1),(2,4,5,1)],2,3,1,3,3)]
        checks = fixed_points = 0
        for values,m,quantum,period,capacity,bg in cases:
            tasks = task_list(values)
            beta = [quantum*(l//period) for l in range(6)]
            domain = [[(f,r) for r in range(c,d+1) for f in (range(1,r+1) if c>1 else (r,))]
                      for c,d,t,p in values]
            for method in ('flow','analytic'):
                out = sr.least_certificate(tasks,m,beta,capacity=capacity,background_power=bg,bound=method)
                records = {}
                for profile in product(*domain):
                    engine = sr.StructuralEngine(tasks,m,beta,0,profile,capacity=capacity,
                                                 background_power=bg,bound=method,legacy_v4=False)
                    result = []
                    try:
                        for k,t in enumerate(tasks):
                            full = engine.full_candidate(k)
                            if full is None:
                                result.append((99,99)); continue
                            first = engine.candidate(k,1,1,full['R']-1) if t.wcet>1 else None
                            result.append((full['R'] if first is None else first['R'],full['R']))
                    finally:
                        engine.clear()
                    records[profile] = tuple(result)
                    checks += 1
                    if all(a<=b for x,y in zip(result,profile) for a,b in zip(x,y)):
                        fixed_points += 1
                        self.assertTrue(out['proven'])
                        self.assertTrue(all(a<=b for x,y in zip(out['certificate']['profile'],profile)
                                            for a,b in zip(x,y)))
                for small,large in product(records,repeat=2):
                    if all(a<=b for x,y in zip(small,large) for a,b in zip(x,y)):
                        self.assertTrue(all(a<=b for x,y in zip(records[small],records[large])
                                            for a,b in zip(x,y)))
        self.assertGreater(fixed_points,0)
        COUNTS.update(joint_profiles_enumerated=checks, postfixed_profiles=fixed_points)

    def test_finite_graphs_and_background(self):
        rows = [(1,2,2,1),(1,3,3,2),(2,4,4,1),(2,4,5,2)]
        cases = []
        for values in [([a]) for a in rows]+[[rows[i],rows[j]] for i,j in ((0,0),(0,2),(1,2),(2,0),(2,2))]:
            for m,quantum,period,capacity,bg,e0 in (
                    (1,1,1,2,0,0),(2,2,1,2,0,0),(2,3,2,4,0,0),
                    (2,4,1,4,0,0),(2,3,1,3,3,0),(2,4,2,4,2,0),(2,2,1,3,0,1)):
                cases.append((values,m,quantum,period,capacity,bg,e0))
        graphs = states = transitions = 0
        prefix_graphs = noncertified = 0
        for values,m,quantum,period,capacity,bg,e0 in cases:
            tasks = task_list(values)
            beta = [quantum*(l//period) for l in range(max(t.deadline for t in tasks)+1)]
            for method in ('flow','analytic'):
                out = sr.least_certificate(tasks,m,beta,e0,capacity=capacity,background_power=bg,bound=method)
                if not out['proven']:
                    noncertified += 1
                    continue
                graph = finite_graph(values,m,capacity,quantum,period,out['certificate']['profile'],
                                     e0=e0,background_power=bg)
                self.assertEqual(graph['status'],'CLOSED_SAFE_GRAPH',(values,m,capacity,bg,method,graph))
                graphs += 1; states += graph['states']; transitions += graph['transitions']
                prefix_graphs += bool(bg)
        self.assertGreater(graphs,0); self.assertGreater(prefix_graphs,0)
        COUNTS.update(closed_certificate_graphs=graphs, critical_prefix_graphs=prefix_graphs,
                      graph_states=states, graph_transitions=transitions,
                      unanalyzed_noncertificates=noncertified, graph_input_cases=len(cases))

    def test_capacity_family_and_unsafe_transfer(self):
        checks = 0
        for m in range(2,6):
            values = [(1,2,3,1)]*m
            tasks = task_list(values)
            beta = [0,m,2*m]
            old = sr.least_certificate(tasks,m,beta,legacy_v4=False)
            self.assertTrue(old['proven'])
            for capacity in (m-1,m,m+1):
                out = sr.least_certificate(tasks,m,beta,capacity=capacity)
                self.assertEqual(out['proven'],capacity>=m)
                if capacity >= m:
                    self.assertEqual(out['certificate']['profile'],[[2,2]]*m)
                checks += 1
            if m <= 3:
                graph = finite_graph(values,m,m-1,m,1,[[2,2]]*m)
                self.assertEqual(graph['status'],'COUNTEREXAMPLE')
            for k in range(1,m+1):
                out = sr.least_certificate(tasks[:k],m,beta,capacity=k,background_power=17)
                self.assertTrue(out['critical_prefix_proven'])
                self.assertFalse(out['taskset_proven'])
                self.assertEqual(out['certificate']['profile'],[[2,2]]*k)
                checks += 1
        COUNTS['parametric_capacity_family_checks'] = checks

    def test_block_vs_bypass_parametric_family(self):
        for power in range(2,10):
            d = power+1
            critical = task_list([(1,d,d,power)])
            out = sr.least_certificate(critical,2,list(range(d+1)),capacity=power,background_power=1)
            self.assertTrue(out['critical_prefix_proven'])
            self.assertEqual(out['certificate']['profile'],[[d,d]])
            results = {}
            for bypass in (False,True):
                energy, remaining = 0, [1,power]
                finished = [None,None]
                for slot in range(d):
                    free = 2
                    for i,cost in enumerate((power,1)):
                        if remaining[i] == 0 or not free:
                            continue
                        if energy < cost:
                            if bypass:continue
                            break
                        energy -= cost;free -= 1;remaining[i] -= 1
                        if not remaining[i]:finished[i]=slot+1
                    energy = min(power,energy+1)
                results[bypass]=(remaining,finished)
            self.assertEqual(results[False][1][0],d)
            self.assertEqual(results[True][0][0],1)
            self.assertEqual(results[True][1][1],d)
        COUNTS['block_vs_bypass_family_parameters'] = 8

    def test_inclusion_scaling_sustainability_and_verifier(self):
        rows = [([(1,4,5,3),(2,5,7,1),(1,7,9,2)],4),
                ([(1,6,8,1),(2,6,9,2),(1,7,9,2)],3)]
        for values, quantum in rows:
            tasks = task_list(values)
            beta = [quantum*(l//2) for l in range(8)]
            ap_out = ap.analytic_certificate(tasks,2,beta,0)
            unified = sr.least_certificate(tasks,2,beta)
            self.assertTrue(unified['proven'])
            self.assertTrue(all(a<=b for a,b in zip(unified['certificate']['response_bounds'],
                                                    ap_out['certificate']['response_bounds'])))
            capacity = sum(sorted([t.power.numerator for t in tasks],reverse=True)[:2])*7
            finite = sr.least_certificate(tasks,2,beta,capacity=capacity)
            self.assertTrue(finite['proven'])
            scale = 10**6
            scaled = sr.least_certificate(task_list([(c,d,t,p*scale) for c,d,t,p in values]),2,
                                          [v*scale for v in beta],capacity=capacity*scale)
            self.assertEqual(finite['certificate']['profile'],scaled['certificate']['profile'])
            self.assertEqual(finite['history'],scaled['history'])
            stronger = sr.least_certificate(tasks,2,[v+int(i>0) for i,v in enumerate(beta)],
                                            capacity=capacity+1)
            self.assertTrue(stronger['proven'])
            self.assertTrue(all(a<=b for x,y in zip(stronger['certificate']['profile'],finite['certificate']['profile'])
                                for a,b in zip(x,y)))
            cheaper = sr.least_certificate(task_list([(c,d,t,max(1,p-1)) for c,d,t,p in values]),
                                           2,beta,capacity=capacity)
            self.assertTrue(cheaper['proven'])
            self.assertTrue(all(a<=b for x,y in zip(cheaper['certificate']['profile'],finite['certificate']['profile'])
                                for a,b in zip(x,y)))
            for kind in ('fingerprint','scope','first','profile','reset'):
                cert = deepcopy(finite['certificate'])
                if kind=='fingerprint':cert['input_sha256']='changed'
                elif kind=='scope':cert['scope']='CRITICAL_PREFIX'
                elif kind=='first':cert['details'][1]['first']['R']=1
                elif kind=='profile':cert['profile'][1][1]=1
                else:
                    changed=False
                    for detail in cert['details']:
                        for group in detail['full'].get('groups',[]):
                            for point in group['witnesses']:
                                if point['saturation_checks']:
                                    point['saturation_checks'].pop(0);changed=True;break
                            if changed:break
                        if changed:break
                    self.assertTrue(changed)
                with self.assertRaises(ValueError): sr.verify_certificate(tasks,2,beta,0,cert)
            self.assertEqual(sr.least_certificate(tasks,2,beta,capacity=capacity,max_iterations=1)['status'],
                             'ITERATION_LIMIT')
            with self.assertRaises(ValueError):sr.least_certificate(tasks,2,beta,capacity=capacity,legacy_v4=True)
        # Frozen holdout_025 was accepted by V4 but lost by AP. This is a
        # regression check, not a newly held-out sample or performance result.
        tasks = task_list([(1,4,10,2),(2,5,9,3),(1,6,7,1)])
        beta = [2*l for l in range(7)]
        old = v4.least_certificate(tasks,1,beta,0)
        self.assertFalse(ap.analytic_certificate(tasks,1,beta,0)['taskset_proven'])
        new = sr.least_certificate(tasks,1,beta)
        self.assertTrue(old['taskset_proven']); self.assertTrue(new['proven'])
        self.assertTrue(all(a<=b for a,b in zip(new['certificate']['response_bounds'],old['certificate']['response_bounds'])))
        COUNTS['strict_separation_examples_retained'] = len(rows)
        COUNTS['known_v4_only_regression_retained'] = 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', help='write audit counts and unittest result')
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(StructuralTests))
    summary = dict(status='PASS' if result.wasSuccessful() else 'FAIL',tests=result.testsRun,counts=COUNTS)
    print(json.dumps(summary,sort_keys=True))
    if args.json:
        from pathlib import Path
        Path(args.json).write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
