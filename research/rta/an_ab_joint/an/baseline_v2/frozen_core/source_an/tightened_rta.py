"""Research-only AN RTA: independently switchable W, C and H refinements.

W: deadline-window workload packing. C: joint suffix/current energy capacity.
H: nondecreasing per-progress blocking budgets. All energies are exact integers.
The unchanged v1 is the mathematical baseline. No production code is imported.
"""
from dataclasses import dataclass
from functools import cache
from itertools import combinations


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


@dataclass(frozen=True)
class Task:
    C: int
    T: int
    D: int
    p: int

    def __post_init__(self):
        check(all(isinstance(v, int) for v in (self.C, self.T, self.D, self.p)), "integer parameters")
        check(1 <= self.C <= self.D <= self.T and self.p > 0, "task domain")


VARIANTS = {0: "v1", 1: "W", 2: "C", 3: "W+C", 4: "H", 5: "W+H", 6: "C+H", 7: "W+C+H"}


class Analyzer:
    def __init__(self, tasks, cores, tight_work=False):
        self.tasks, self.m, self.tight = tuple(tasks), cores, tight_work
        self.work = cache(self._work)
        self.phase = cache(self._phase)
        self.global_energy = cache(self._global_energy)
        self.suffix_energy = cache(self._suffix_energy)
        self.progress = cache(self._progress)
        self.gamma = tuple(self._gamma(k) for k in range(len(tasks)))
        self.Q = tuple(t.p + sum(sorted((j.p for j in tasks[:k]), reverse=True)[:cores - 1])
                       for k, t in enumerate(tasks))

    def clear(self):
        for f in (self.work, self.phase, self.global_energy, self.suffix_energy, self.progress):
            f.cache_clear()

    def _work(self, i, length):
        if length == 0:
            return 0
        j = self.tasks[i]
        coarse = min(length, j.C * ((length + j.D + j.T - 2) // j.T))
        if not self.tight:
            return coarse
        values = [0]
        for index, r in enumerate(range(1 - j.D, length)):
            overlap = min(length, r + j.D) - max(0, r)
            weight = min(j.C, max(0, overlap))
            previous = values[max(0, index + 1 - j.T)]
            values.append(max(values[-1], previous + weight))
        check(values[-1] <= coarse, "refined workload exceeds coarse bound")
        return values[-1]

    def _gamma(self, k):
        cheap = [i for i in range(k + 1, len(self.tasks)) if self.tasks[i].p < self.tasks[k].p]
        best = 0
        for count in range(1, min(self.m, len(cheap)) + 1):
            for selected in combinations(cheap, count):
                value = sum(self.tasks[i].p for i in selected)
                if value < self.tasks[k].p:
                    best = max(best, value)
        return best

    def _global_energy(self, length):
        free, total = self.m * length, 0
        for i in sorted(range(len(self.tasks)), key=lambda i: self.tasks[i].p, reverse=True):
            units = min(free, self.work(i, length))
            total += units * self.tasks[i].p
            free -= units
        return total

    def _suffix_energy(self, k, length):
        states = {(0, 0): self.tasks[k].p}
        for i, j in enumerate(self.tasks):
            history = self.work(i, length)
            augmented = self.work(i, length + 1)
            if i == k:
                history = min(history, j.C - 1, augmented - 1)
            options = [(v, a) for a in ((0, 1) if i < k else (0,))
                       for v in range(history + 1)
                       if i >= k or v + a <= augmented]
            nxt = {}
            for (used, current), value in states.items():
                for v, a in options:
                    key = used + v, current + a
                    if key[0] <= self.m * length and key[1] <= self.m - 1:
                        amount = value + (v + a) * j.p
                        if amount > nxt.get(key, -1):
                            nxt[key] = amount
            states = nxt
        result = max(states.values())
        check(result <= self.Q[k] + self.global_energy(length), "joint capacity envelope not dominated")
        return result

    def _progress(self, k, window):
        d = max(d for d in range(window + 1)
                if sum(min(self.work(i, window), d) for i in range(k)) >= self.m * d)
        return self.tasks[k].C + d

    def _phase(self, k, q, h):
        best = None
        m, tasks = self.m, self.tasks
        for z in range(1, min(tasks[k].C, q) + 1):
            d = q - z
            hp = {(0, 0, 0): 0}
            for i in range(k):
                cap = self.work(i, q + h)
                choices = [(g, s, e) for g in range(min(d, cap) + 1)
                           for s in range(min(z, cap - g) + 1)
                           for e in range(min(h, cap - g - s) + 1)]
                nxt = {}
                for (gg, ss, ee), value in hp.items():
                    for g, s, e in choices:
                        key = gg + g, ss + s, ee + e
                        if key[0] <= m * d and key[1] <= (m - 1) * z and key[2] <= (m - 1) * h:
                            cost = value + (g + s + e) * tasks[i].p
                            if cost > nxt.get(key, -1):
                                nxt[key] = cost
                hp = nxt
            hp = {(s, e): value for (g, s, e), value in hp.items() if g == m * d}
            if not hp:
                continue
            lp = {(0, 0, 0): 0}
            for i in range(k + 1, len(tasks)):
                cap = self.work(i, q + h - 1)
                bmax = h if tasks[i].p < tasks[k].p else 0
                choices = [(c, b) for c in range(min(z - 1, cap) + 1)
                           for b in range(min(bmax, cap - c) + 1)]
                nxt = {}
                for (cc, bb, be), value in lp.items():
                    for c, b in choices:
                        key = cc + c, bb + b, be + b * tasks[i].p
                        if key[0] <= (m - 1) * (z - 1) and key[1] <= m * h and key[2] <= h * self.gamma[k]:
                            cost = value + (c + b) * tasks[i].p
                            if cost > nxt.get(key, -1):
                                nxt[key] = cost
                lp = nxt
            for (s, e), hp_value in hp.items():
                for (c, b, _), lp_value in lp.items():
                    if s + c <= (m - 1) * z and e + b <= m * h:
                        value = z * tasks[k].p + hp_value + lp_value
                        best = value if best is None else max(best, value)
        return best

    def old_capacity(self, k, window, beta):
        return self.Q[k] + max(self.global_energy(ell) - beta[ell] for ell in range(window))

    def new_capacity(self, k, checkpoint, beta):
        return max(self.suffix_energy(k, ell) - beta[ell] for ell in range(max(1, checkpoint)))

    def candidate(self, k, beta, battery, mask):
        check(bool(mask & 1) == self.tight, "wrong workload analyzer")
        for w in range(self.tasks[k].C, self.tasks[k].D + 1):
            a = self.progress(k, w)
            if a > w:
                continue
            old_cap = self.old_capacity(k, w, beta)
            if not mask & 2 and battery < old_cap:
                continue
            limit = w - a
            def point(q, h):
                energy = self.phase(k, q, h)
                cap = self.new_capacity(k, h + q - 1, beta) if mask & 2 else old_cap
                return (energy is None or energy <= beta[h + q - 1]) and battery >= cap, energy, cap
            if mask & 4:
                previous, hs, energies, capacities = 0, [], [], []
                for q in range(1, a + 1):
                    for h in range(previous, limit + 1):
                        good, energy, cap = point(q, h)
                        if good:
                            previous = h
                            hs.append(h); energies.append(energy); capacities.append(cap)
                            break
                    else:
                        break
                if len(hs) == a:
                    return {"R": w, "A": a, "h": hs, "energy": energies, "capacity": max(capacities)}
            else:
                for h in range(limit + 1):
                    checkpoints = []
                    for q in range(1, a + 1):
                        good, energy, cap = point(q, h)
                        if not good:
                            break
                        checkpoints.append((energy, cap))
                    if len(checkpoints) == a:
                        return {"R": w, "A": a, "h": [h] * a, "energy": [x[0] for x in checkpoints],
                                "capacity": max(x[1] for x in checkpoints)}
        return None

    def analyze(self, beta, battery, mask):
        return [self.candidate(k, beta, battery, mask) for k in range(len(self.tasks))]

    def gates(self, k, beta, battery):
        cpu = cap = energy = together = False
        for w in range(self.tasks[k].C, self.tasks[k].D + 1):
            a = self.progress(k, w)
            if a > w:
                continue
            cpu = True
            good_cap = battery >= self.old_capacity(k, w, beta)
            good_energy = False
            for h in range(w - a + 1):
                if all((v := self.phase(k, q, h)) is None or v <= beta[h + q - 1]
                       for q in range(1, a + 1)):
                    good_energy = True
                    break
            cap |= good_cap
            energy |= good_energy
            together |= good_cap and good_energy
        category = ("certified" if together else "processor" if not cpu else
                    "both" if not cap and not energy else "capacity" if energy and not cap else
                    "energy" if cap and not energy else "window_conflict")
        return {"category": category, "CPU": cpu, "CPU_C": cap, "CPU_E": energy, "CPU_C_E": together}


def beta_for(pattern, maximum):
    return tuple(min(sum(pattern[(phase + u) % len(pattern)] for u in range(ell))
                     for phase in range(len(pattern))) for ell in range(maximum + 1))
