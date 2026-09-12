"""Causal suffix restart S, added after the frozen three-factor diagnosis.

The balance restarts immediately AFTER a past debit, whose residual is >= 0.
For ell harvested slots, only ell-1 historical execution slots remain to charge.
This is a new sufficient branch, OR'ed with the existing prefix certificate.
"""
from tightened_rta import Analyzer, check


class SuffixAnalyzer(Analyzer):
    def candidate(self, k, beta, battery, mask):
        if not mask & 8:
            return super().candidate(k, beta, battery, mask)
        check(bool(mask & 1) == self.tight, "wrong workload analyzer")
        # The earliest sufficient suffix horizon. No monotonicity of the raw
        # energy inequality is assumed: all shorter integer lengths are scanned.
        tail = None
        tail_capacity = 0
        for ell in range(1, self.tasks[k].D):
            tail_capacity = max(tail_capacity, self.suffix_energy(k, ell - 1) - beta[ell - 1])
            if beta[ell] >= self.suffix_energy(k, ell - 1) and battery >= tail_capacity:
                tail = ell
                break
        for w in range(self.tasks[k].C, self.tasks[k].D + 1):
            a = self.progress(k, w)
            if a > w:
                continue
            old_cap = self.old_capacity(k, w, beta)
            limit = w - a
            def point(q, h):
                s = h + q - 1
                if tail is not None and s >= tail:
                    return True, {"branch": "suffix", "ell": tail, "capacity": tail_capacity}
                energy = self.phase(k, q, h)
                cap = self.new_capacity(k, s, beta) if mask & 2 else old_cap
                return ((energy is None or energy <= beta[s]) and battery >= cap,
                        {"branch": "prefix", "energy": energy, "capacity": cap})
            if mask & 4:
                previous, hs, points = 0, [], []
                for q in range(1, a + 1):
                    for h in range(previous, limit + 1):
                        good, detail = point(q, h)
                        if good:
                            previous = h
                            hs.append(h); points.append(detail)
                            break
                    else:
                        break
                if len(hs) == a:
                    return {"R": w, "A": a, "h": hs, "checkpoints": points,
                            "suffix_horizon": tail, "capacity": max(x["capacity"] for x in points)}
            else:
                for h in range(limit + 1):
                    points = []
                    for q in range(1, a + 1):
                        good, detail = point(q, h)
                        if not good:
                            break
                        points.append(detail)
                    if len(points) == a:
                        return {"R": w, "A": a, "h": [h] * a, "checkpoints": points,
                                "suffix_horizon": tail, "capacity": max(x["capacity"] for x in points)}
        return None
