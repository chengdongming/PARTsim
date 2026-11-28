"""Integer energy-bound evaluators for the reviewed AN same-slot RTA.

OPT uses exact integer region counts and E-slot pattern multiplicities.
ANA uses a frequency-aware relaxation; MR merges historical P/E regions.
Both relaxations retain the shared high-priority workload and physical cap.
The names of internal functions match the equations' roles, not distinct
schedulers. All numeric comparisons are exact integers or scaled rationals.
"""
from functools import lru_cache
from itertools import combinations, product
import math

OPT = "RTA-AN-OPT"
ANA = "RTA-AN-ANA"
MR = "RTA-AN-MR"
METHODS = (OPT, ANA, MR)
NEG = -math.inf
# Exact OPT may have a very large DP. Never prune an allowed configuration:
# exceeding this protective state/option budget aborts the request, and is
# reported as RESOURCE_LIMIT, not an infeasible branch or a no-certificate.
DP_STATE_LIMIT = 200_000


class AnalysisResourceLimit(RuntimeError):
    """The exact optimization did not finish within the state budget."""


def _guard_size(values):
    if len(values) > DP_STATE_LIMIT:
        raise AnalysisResourceLimit(f"AN integer configuration exceeds {DP_STATE_LIMIT} states/options")


@lru_cache(maxsize=65536)
def W(task, L, x):
    """Conditioned carry-in workload, using the two representative ages."""
    C, D, T, p = task
    F, R = x

    def N(v):
        return 0 if v <= 0 else v // T * C + min(C, v % T)
    out = 0
    for lo, hi, j in [(0, F - 1, 0), (F, R - 1, 1)]:
        if lo <= hi:
            a = max(lo, min(hi, R - min(L, C - j)))
            out = max(out, min(L, C - j, R - a) + N(L - T + a))
    return min(L, out)


@lru_cache(maxsize=2048)
def kappa(tasks, M, k):
    """Maximum cost of a legal one-slot low-priority subset (eq. 5a)."""
    pk = tasks[k][3]
    low = [t[3] for t in tasks[k+1:] if t[3] < pk]
    best = 0
    examined = 0
    for count in range(1, min(M, len(low))+1):
        for subset in combinations(low, count):
            examined += 1
            if examined > DP_STATE_LIMIT:
                raise AnalysisResourceLimit("AN legal subset precomputation exceeds budget")
            cost = sum(subset)
            if cost < pk:
                best = max(best, cost)
    return best


def top(items, capacity):
    if capacity < 0:
        raise ValueError('negative capacity')
    out = 0
    for p, n in sorted(items, reverse=True):
        count = min(n, capacity)
        out += p * count
        capacity -= count
        if capacity == 0:
            break
    return out


@lru_cache(maxsize=8192)
def setup_reduced(tasks, M, k, X, h, s, g):
    """Precompute B-region and shared prefix/T-suffix workload caps."""
    pk = tasks[k][3]
    L = h + 1 + s
    hp = []
    lp = []
    free = 0
    available = 0
    gross = 0
    losses = []
    for i, t in enumerate(tasks):
        if i == k:
            continue
        p = t[3]
        wh = W(t, h, X[i])
        if i < k:
            wb = W(t, h + 1, X[i])
            wl = W(t, L, X[i])
            wt = min(g, W(t, s, X[i]))
            hp.append((p, wh, wb))
            a = min(wb, wh + 1, wl)
            free_i = min(wt, wl - a)
            available += wt
            free += free_i
            gross += p * a
            losses.append((p, wt - free_i))
        else:
            lp.append((p, wh, p < pk))
    if available < M * g:
        return None
    delta = max(0, M * g - free)
    loss = 0
    for p, n in sorted(losses):
        c = min(delta, n)
        loss += p * c
        delta -= c
        if delta == 0:
            break
    assert delta == 0
    Hshare = gross - loss
    eb = kappa(tasks, M, k)
    B = top([(p, min(1, wb)) for p, wh, wb in hp], M - 1)
    pi = sum(sorted((t[3] for t in tasks), reverse=True)[:M])
    return (pk, tuple(hp), tuple(lp), Hshare, eb, B, pi)


def fast_value(data, M, h, u):
    """Fixed-u, P/E merged upper bound. All terms are integer and safe."""
    pk, hp, lp, Hshare, kp, B, pi = data
    l = h - u
    hpN = [(p, min(l, w)) for p, w, wb in hp]
    lpN = [(p, min(l, w)) for p, w, eligible in lp if eligible]
    allS = [(p, min(u, w)) for p, w, wb in hp] + [(p, min(u, w)) for p, w, eligible in lp]
    lpS = [(p, min(u, w)) for p, w, eligible in lp]
    ENlow = min(l * kp, top(lpN, M * l))
    EN = min(top(hpN + lpN, M * l), top(hpN, M * l) + ENlow)
    SS = top(allS, (M - 1) * u)
    BR = EN + SS + B
    Lshare = min(sum((p * min(w, u + l * int(eligible)) for p, w, eligible in lp)), ENlow + top(lpS, (M - 1) * u))
    BW = Hshare + Lshare
    return min((h + 1) * pi, (u + 1) * pk + min(BR, BW))


def urange(X, k, q, b, z, h):
    return (max(0, z - b + h), min(z, h, z - int(q > 1 and b - h >= X[k][0])))


def max_discrete_concave(f, lo, hi):
    if lo > hi:
        return NEG
    while lo < hi:
        m = (lo + hi) // 2
        if f(m) < f(m + 1):
            lo = m + 1
        else:
            hi = m
    return f(lo)


@lru_cache(maxsize=8192)
def net_lp(tasks, M, k, X, d):
    pk = tasks[k][3]
    items = [(t[3], min(d, W(t, d, X[i]))) for i, t in enumerate(tasks) if i > k and t[3] < pk]
    return min(d * kappa(tasks, M, k), top(items, M * d))


@lru_cache(maxsize=16384)
def merged_bound(tasks, M, k, X, h, s, g, lo, hi, enumerate_u=False):
    """MR, Appendix A: regional and shared-workload relaxations."""
    data = setup_reduced(tasks, M, k, X, h, s, g)
    if data is None or lo > hi:
        return NEG
    f = lambda u: fast_value(data, M, h, u)
    return max(f(u) for u in range(lo, hi+1)) if enumerate_u else max_discrete_concave(f, lo, hi)

@lru_cache(maxsize=128)
def patterns(tasks, M, k):
    """Legal E-slot pattern (selected high count, low-task incidences)."""
    p = tasks[k][3]
    indices = tuple(range(k + 1, len(tasks)))
    eligible = tuple((i for i in indices if tasks[i][3] < p))
    out = []
    examined = 0
    for a in range(min(k, M - 1) + 1):
        for cnt in range(min(M - a, len(eligible)) + 1):
            for S in combinations(eligible, cnt):
                examined += 1
                if examined > DP_STATE_LIMIT:
                    raise AnalysisResourceLimit("AN pattern subset enumeration exceeds budget")
                if sum((tasks[i][3] for i in S)) < p:
                    out.append((a,) + tuple((int(i in S) for i in indices)))
                    _guard_size(out)
    return tuple(out)


@lru_cache(maxsize=4)
def reachable(tasks, M, k, e, caps):
    """Exact sums of e legal E-slot patterns; include all low-count vectors."""
    pat = patterns(tasks, M, k)
    states = {(0,) + (0,) * len(caps)}
    for _ in range(e):
        nxt = set()
        for st in states:
            for pt in pat:
                z = tuple((a + b for a, b in zip(st, pt)))
                if all((v <= cap for v, cap in zip(z[1:], caps))):
                    nxt.add(z)
                    _guard_size(nxt)
        states = nxt
    by = {}
    for d, *v in states:
        by.setdefault(d, []).append(tuple(v))
    return by


@lru_cache(maxsize=1024)
def pat_alloc(tasks, M, k, X, q, w, b, z, h, u, e):
    """OPT fixed split: DP over P/E/S/B/T high counts, then E patterns.

    Match the exact total high E count; low S capacity and shared work
    reduce to a descending-energy greedy fill after E counts are fixed.
    """
    s = w - b - 1
    g = max(0, s - q + 1 + z)
    L = h + 1 + s
    m = h - u - e
    if (m or g) and k < M:
        return NEG
    caps = (M * m, (M - 1) * e, (M - 1) * u, M - 1, M * g)
    hp = {(0, 0, 0, 0, 0): 0}
    for i in range(k):
        p = tasks[i][3]
        wh = W(tasks[i], h, X[i])
        wb = W(tasks[i], h + 1, X[i])
        wl = W(tasks[i], L, X[i])
        wt = min(g, W(tasks[i], s, X[i]))
        opts = []
        for a, d, ss, c, t in product(range(min(m, wh) + 1), range(min(e, wh) + 1), range(min(u, wh) + 1), range(2), range(wt + 1)):
            if a + d + ss <= wh and a + d + ss + c <= wb and (a + d + ss + c + t <= wl):
                opts.append(((a, d, ss, c, t), p * (a + d + ss + c)))
                _guard_size(opts)
        nxt = {}
        for st, cost in hp.items():
            for vv, v in opts:
                key = tuple((a + b for a, b in zip(st, vv)))
                if any((a > b for a, b in zip(key, caps))):
                    continue
                if cost + v > nxt.get(key, NEG):
                    nxt[key] = cost + v
                    _guard_size(nxt)
        hp = nxt
        if not hp:
            return NEG
    lp = tuple(range(k + 1, len(tasks)))
    work = tuple((W(tasks[i], h, X[i]) for i in lp))
    reach = reachable(tasks, M, k, e, tuple((min(e, wh) for wh in work)))
    best = NEG
    for (a, d, ss, c, t), val in hp.items():
        if a != caps[0] or t != caps[4]:
            continue
        for v in reach.get(d, ()):
            cost = sum((tasks[i][3] * vv for i, vv in zip(lp, v)))
            items = [(tasks[i][3], min(u, wh - vv)) for i, wh, vv in zip(lp, work, v)]
            cost += top(items, caps[2] - ss)
            best = max(best, val + cost + (u + 1) * tasks[k][3])
    return best


@lru_cache(maxsize=2048)
def pat_bound(tasks, M, k, X, q, w, b, z, h):
    pi = sum(sorted((t[3] for t in tasks), reverse=True)[:M])
    lo = max(0, z - b + h)
    hi = min(z, h, z - int(q > 1 and b - h >= X[k][0]))
    return min((h + 1) * pi, max((pat_alloc(tasks, M, k, X, q, w, b, z, h, u, e) for u in range(lo, hi + 1) for e in range(h - u + 1)), default=NEG))


@lru_cache(maxsize=128)
def price_rules(tasks, M, k):
    """Precompute prefix-threshold nonnegative weight breakpoints exactly."""
    lp = tasks[k + 1:]
    pk = tasks[k][3]
    ordered = tuple(sorted((j for j, t in enumerate(lp) if t[3] < pk), key=lambda j: (lp[j][3], j)))
    size = min(M, len(ordered))
    scale = math.lcm(*range(1, size + 1)) if size else 1
    feasible = []
    examined = 0
    for r in range(size + 1):
        for S in combinations(range(len(ordered)), r):
            examined += 1
            if examined > DP_STATE_LIMIT:
                raise AnalysisResourceLimit("AN price subset precomputation exceeds budget")
            cost = sum((lp[ordered[j]][3] for j in S))
            if cost < pk:
                feasible.append((S, cost))
                _guard_size(feasible)
    rules = []
    starts = [j + 1 for j in range(len(ordered)) if j + 1 == len(ordered) or lp[ordered[j]][3] != lp[ordered[j + 1]][3]]
    for n in starts:
        best = {}
        for S, cost in feasible:
            j = sum((a < n for a in S))
            best[j] = max(best.get(j, NEG), cost)
        lambdas = {0}
        for j, cost in best.items():
            for t, other in best.items():
                if j > t and cost >= other:
                    v = scale * (cost - other)
                    assert v % (j - t) == 0
                    lambdas.add(v // (j - t))
        for lam in sorted(lambdas):
            gamma = max((scale * p - lam * j for j, p in best.items()))
            rules.append((n, lam, gamma))
    return (scale, ordered, tuple(rules))


def price_cap_scaled(l, base, ordered_work, rules):
    scale, _, rows = rules
    pref = [0]
    for w in ordered_work:
        pref.append(pref[-1] + min(l, w))
    val = scale * base
    for n, lam, gamma in rows:
        val = min(val, l * gamma + lam * pref[n])
    return val


def top_truncated(items, c, r):
    """items pre-sorted in nonincreasing energy; per-task cap min(r,w)."""
    count = c * r
    val = 0
    for p, w in items:
        m = min(count, r, w)
        val += p * m
        count -= m
        if not count:
            break
    return val


@lru_cache(maxsize=4096)
def price_context(tasks, M, k, X, h, s, g):
    data = setup_reduced(tasks, M, k, X, h, s, g)
    if data is None:
        return None
    pk, hp, lp, Hshare, kp, B, pi = data
    hp_sort = tuple(sorted(((p, w) for p, w, wb in hp), reverse=True))
    lo_all = tuple(sorted(((p, w) for p, w, eligible in lp), reverse=True))
    lo_eligible = tuple(sorted(((p, w) for p, w, eligible in lp if eligible), reverse=True))
    allN = tuple(sorted(hp_sort + lo_eligible, reverse=True))
    allS = tuple(sorted(hp_sort + lo_all, reverse=True))
    rules = price_rules(tasks, M, k)
    ordered_work = tuple((lp[i][1] for i in rules[1]))
    return (pk, lp, Hshare, kp, B, pi, hp_sort, lo_all, lo_eligible, allN, allS, rules, ordered_work)


def price_value_scaled(ctx, M, h, u):
    pk, lp, Hshare, kp, B, pi, hp, loall, loeligible, N, S, rules, ordered_work = ctx
    scale = rules[0]
    l = h - u
    low = price_cap_scaled(l, min(l * kp, top_truncated(loeligible, M, l)), ordered_work, rules)
    EN = min(scale * top_truncated(N, M, l), scale * top_truncated(hp, M, l) + low)
    BR = EN + scale * (top_truncated(S, M - 1, u) + B)
    BL = min(scale * sum((p * min(w, u + l * int(eligible)) for p, w, eligible in lp)), low + scale * top_truncated(loall, M - 1, u))
    BW = scale * Hshare + BL
    return min(scale * (h + 1) * pi, scale * (u + 1) * pk + min(BR, BW))


@lru_cache(maxsize=16384)
def price_reduced(tasks, M, k, X, h, s, g, lo, hi, enumerate_u=False):
    """ANA: maximize scaled rational values before the final safe floor."""
    if lo > hi:
        return NEG
    ctx = price_context(tasks, M, k, X, h, s, g)
    if ctx is None:
        return NEG
    f = lambda u: price_value_scaled(ctx, M, h, u)
    v = max((f(u) for u in range(lo, hi + 1))) if enumerate_u else max_discrete_concave(f, lo, hi)
    return v // ctx[-2][0]


@lru_cache(maxsize=8192)
def priced_gap(tasks, M, k, X, d):
    base = net_lp(tasks, M, k, X, d)
    rules = price_rules(tasks, M, k)
    work = tuple((W(tasks[k + 1 + j], d, X[k + 1 + j]) for j in rules[1]))
    return price_cap_scaled(d, base, work, rules) // rules[0]


def energy_bound(tasks, M, k, X, q, w, b, z, h, method, *, enumerate_u=False):
    """Energy demand including the hypothetical final target unit.

    NEG means the relaxation is provably infeasible. No resource failure may
    be converted to NEG; it must propagate to the experiment boundary.
    """
    if method == OPT:
        return pat_bound(tasks, M, k, X, q, w, b, z, h)
    lo, hi = urange(X, k, q, b, z, h)
    s = w-b-1
    g = max(0, s-q+1+z)
    if method == ANA:
        return price_reduced(tasks, M, k, X, h, s, g, lo, hi, enumerate_u)
    if method == MR:
        return merged_bound(tasks, M, k, X, h, s, g, lo, hi, enumerate_u)
    raise ValueError("unknown AN RTA method")


def clear():
    """Drop case/iteration caches; large exact states are never shared across runs."""
    for fn in (W, kappa, setup_reduced, net_lp, merged_bound, patterns, reachable,
               pat_alloc, pat_bound, price_rules, price_context, price_reduced, priced_gap):
        fn.cache_clear()
