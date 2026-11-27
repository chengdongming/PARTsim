"""Unlimited-storage ASAP-BLOCK analysis for harvest-before-debit.

Reuse only the V7.2 workload/accounting engine, not its supply witnesses,
legacy fallback, model identifiers, or sealed reproduction artifacts.
All energy inputs are exact scaled integers. The release floor is the stored
energy *before* the current slot's harvest, at EVERY release.
"""
from bisect import bisect_left
import hashlib
import json
from pathlib import Path
import sys
import time

_AB = (Path(__file__).resolve().parents[1] / "asapblock_unlimited" /
       "runtime/reproduction/optimized/research/rta/an_ab_joint/ab")
sys.path.insert(0, str(_AB))
import ab_structural_rta as frozen

MODEL = "SLOTTED_AB_HARVEST_BEFORE_DEBIT_UNLIMITED"
VERSION = "AB_STRUCTURAL_SAME_SLOT_V1"
Task = frozen.model.Task
require = frozen.model.require


class SameSlotEngine(frozen.StructuralEngine):
    """Same workload relaxation, corrected raw-supply witnesses."""

    def __init__(self, tasks, m, beta, e0, profile, *, bound="analytic"):
        super().__init__(tasks, m, beta, e0, profile,
                         capacity=None, background_power=0,
                         bound=bound, legacy_v4=False)
        # beta is defined only on 0..max(D) (or longer). First use can
        # occur at slot ell with e0+beta(ell+1)>=prefix maximum power.
        self.charging_gaps = []
        prefix = 0
        for power in self.powers:
            prefix = max(prefix, power)
            gap = bisect_left(self.beta, prefix, lo=1)
            first = bisect_left(self.beta, max(0, prefix-self.e0), lo=1)
            self.charging_gaps.append((
                prefix, gap if gap < len(self.beta) else None,
                first-1 if first < len(self.beta) else None))

    def required_window_capacity(self, *args, **kwargs):
        raise NotImplementedError("same-slot RTA supports unlimited storage only")

    def witness(self, k, q, w, b, z, blocker, ell, all_blockers=False):
        require(ell in self.anchors(b), "invalid anchor")
        require(not all_blockers or blocker == k, "invalid common blocker")
        past = b if ell == 0 else ell-1
        supply = self.e0+self.beta[b+1] if ell == 0 else self.beta[ell]
        upper = self.account(k, q, w, b, z, blocker, past, all_blockers)
        if upper is not None and upper > supply:
            return None
        return dict(blocker=blocker, all_blockers=all_blockers, ell=ell,
                    upper_energy=upper, supply=supply, saturation_checks=[])

    def progress_requirements(self, k, q, w):
        # The one-slot harvest guarantee also holds at the release slot.
        # In contrast to the old theorem, e0 need not separately cover it.
        candidates = []
        for delta in (0, 1):
            denial_slots = w-q+1-delta
            if denial_slots <= 0:
                continue
            hp = sum(min(self.work(i, w), denial_slots) for i in range(k))
            units = hp//denial_slots+1
            if units > self.m:
                continue
            threshold = self.threshold(k, units)
            if self.beta[1] >= threshold:
                candidates.append(dict(kind="SAME_SLOT_PROGRESS_WINDOW",
                    q=q, R=w, discarded_release_slot=delta,
                    denial_slots=denial_slots, hp_capacity=hp,
                    hp_units_per_denial=units, energy_threshold=threshold))
        return candidates

    def charging_requirement(self, k, q, w):
        power, gap, initial = self.charging_gaps[k]
        if initial is None or initial >= w:
            return None
        # Ignore gaps beyond the current finite window: they guarantee no
        # additional execution within it, and must not require beta(w+1).
        gap = gap if gap is not None and gap <= w else None
        slots = 1 + ((w-1-initial)//gap if gap is not None else 0)
        hp = sum(self.work(i, w) for i in range(k))
        if hp+q > slots:
            return None
        return dict(kind="SAME_SLOT_CHARGING_GAP_WINDOW", q=q, R=w,
                    prefix_unit_power=power, recharge_gap=gap,
                    initial_gap=initial, guaranteed_prefix_slots=slots,
                    hp_workload=hp)


def fingerprint(tasks, m, beta, e0, bound):
    model = dict(model=MODEL, version=VERSION, bound=bound, processors=m,
                 tasks=[[t.name, t.wcet, t.deadline, t.period, int(t.power)]
                        for t in tasks],
                 beta=list(beta), pre_harvest_release_floor=e0)
    return hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()


def verify_certificate(tasks, m, beta, e0, certificate):
    tasks, beta = frozen.model.normalize(tasks, m, beta, e0)
    c = certificate
    require(c["version"] == VERSION and c["model"] == MODEL, "wrong timing model")
    bound = c["bound"]
    require(c["input_sha256"] == fingerprint(tasks, m, beta, e0, bound),
            "input fingerprint mismatch")
    profile = tuple(tuple(p) for p in c["profile"])
    require(len(profile) == len(tasks) == len(c["details"]), "profile length")
    engine = SameSlotEngine(tasks, m, beta, e0, profile, bound=bound)
    try:
        for k, (t, (f, r), detail) in enumerate(zip(tasks, profile, c["details"])):
            require(type(f) is int and type(r) is int and
                    1 <= f <= r <= t.deadline and t.wcet <= r, "invalid profile")
            full = engine.window(k, t.wcet, r)
            require(full is not None and detail["full"] == full, "full predicate failed")
            first = detail["first"]
            if first["kind"] == "FULL_COMPLETION_IMPLIES_FIRST":
                require(f == r and first == {"kind": "FULL_COMPLETION_IMPLIES_FIRST",
                                             "R": r}, "invalid first implication")
            else:
                require(engine.window(k, 1, f) == first, "first predicate failed")
        require(c["response_bounds"] == [r for _, r in profile], "response vector")
    finally:
        engine.clear()
    return {"status": "PASS", "model": MODEL, "certified_tasks": len(tasks)}


def least_certificate(tasks, m, beta, e0=0, *, bound="analytic"):
    tasks, beta = frozen.model.normalize(tasks, m, beta, e0)
    profile = tuple((1, t.wcet) for t in tasks)
    limit = 1+sum(t.deadline-t.wcet + (t.deadline-1 if t.wcet > 1 else 0)
                  for t in tasks)
    history = []
    for _ in range(limit):
        engine = SameSlotEngine(tasks, m, beta, e0, profile, bound=bound)
        details, next_profile = [], []
        try:
            for k, task in enumerate(tasks):
                full = engine.full_candidate(k)
                if full is None:
                    return dict(version=VERSION, model=MODEL,
                                status="NO_CERTIFICATE", certificate=None,
                                first_unproven_task=task.name, history=history)
                first = (engine.candidate(k, 1, 1, full["R"]-1)
                         if task.wcet > 1 else None)
                if first is None:
                    first = dict(kind="FULL_COMPLETION_IMPLIES_FIRST", R=full["R"])
                details.append(dict(first=first, full=full))
                next_profile.append((first["R"], full["R"]))
        finally:
            engine.clear()
        history.append(dict(profile=profile, outputs=next_profile))
        require(all(f <= a and r <= b for (f, r), (a, b)
                    in zip(profile, next_profile)), "non-monotone iteration")
        if tuple(next_profile) == profile:
            cert = dict(version=VERSION, model=MODEL, bound=bound,
                        input_sha256=fingerprint(tasks, m, beta, e0, bound),
                        profile=profile, details=details,
                        response_bounds=[r for _, r in profile])
            return dict(version=VERSION, model=MODEL, status="CERTIFIED",
                        certificate=cert, history=history,
                        verification=verify_certificate(tasks, m, beta, e0, cert))
        profile = tuple(next_profile)
    raise AssertionError("finite lattice iteration did not stabilize")


def analyze_case(case, *, bound="analytic", translated_seq=False):
    """Evaluate a new-mode case, never silently reinterpret a stored old floor."""
    m = case["model"]
    require(m["harvest_order"] == "HARVEST_BEFORE_DEBIT", "wrong harvest order")
    require(m["capacity"] is None and m["background_power"] == 0,
            "only unlimited storage and zero background are implemented")
    require(m["priority"] == "INPUT_ORDER", "unsupported priority")
    require(m["release_floor_stage"] == "PRE_HARVEST", "wrong release floor stage")
    require(type(m.get("energy_scale")) is int and m["energy_scale"] > 0,
            "invalid energy scale")
    require(all(type(t["power"]) is int and t["power"] > 0 for t in m["tasks"]),
            "task energy must be an exactly scaled positive integer")
    tasks = [Task(t["name"], t["C"], t["D"], t["T"], t["power"])
             for t in m["tasks"]]
    beta, cores, e0 = m["beta"], m["processors"], m["release_floor"]
    started = time.process_time()
    result = least_certificate(tasks, cores, beta, e0, bound=bound)
    result["analysis_cpu_seconds"] = time.process_time()-started
    if translated_seq:
        # With no overflow A_t=E_t+H_t obeys the historical recurrence
        # A_(t+1)=A_t-P_t+H_(t+1). The shifted raw supply has the same
        # any-window lower bound; every release has A_t>=e0+beta(1).
        # This is a conservative translated baseline, not native new SEQ.
        result["translated_seq"] = dict(
            method="SEQ_V7_2_SHIFTED_UNLIMITED",
            effective_release_floor=e0+beta[1],
            result=frozen.model.least_certificate(tasks, cores, beta, e0+beta[1]))
    return result
