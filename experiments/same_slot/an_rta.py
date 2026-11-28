"""Same-slot unlimited-storage ASAP-NONBLOCK sufficient response analysis.

Public methods: RTA-AN-OPT, RTA-AN-ANA, RTA-AN-MR. The reviewed integer
optimization and analytical formulas are in an_rta_bounds. This module
handles the common input contract, all three window tests, complete
integer-window joint iteration, and deterministic certificate replay.

No assumption of synchronous releases or monotone actual AN scheduling
under energy changes is made. Failure to certify is not a deadline miss.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import time

from . import an_rta_bounds as bounds

OPT, ANA, MR = bounds.METHODS
METHODS = bounds.METHODS
MODEL = "SLOTTED_AN_HARVEST_BEFORE_DEBIT_UNLIMITED"
VERSION = "AN_RTA_SAME_SLOT_V1"


def normalize_case(case):
    """Validate the same exact-integer model schema used by the AB experiment."""
    model = case["model"]
    if not isinstance(model, dict):
        raise ValueError("invalid model")
    expected = {
        "harvest_order": "HARVEST_BEFORE_DEBIT",
        "release_floor_stage": "PRE_HARVEST",
        "capacity": None,
        "background_power": 0,
        "priority": "INPUT_ORDER",
    }
    for key, value in expected.items():
        if key not in model or model[key] != value:
            raise ValueError(f"unsupported AN model field: {key}")
    if type(model["background_power"]) is not int:
        raise ValueError("background_power must be integer zero")
    if type(model.get("energy_scale")) is not int or model["energy_scale"] <= 0:
        raise ValueError("invalid energy scale")
    M = model["processors"]
    e0 = model["release_floor"]
    if type(M) is not int or M < 1:
        raise ValueError("positive integer core count required")
    if type(e0) is not int or e0 < 0:
        raise ValueError("invalid every-release pre-harvest floor")
    rows = model["tasks"]
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError("nonempty task list required")
    names, tasks = [], []
    for row in rows:
        name = row["name"]
        task = tuple(row[key] for key in ("C", "D", "T", "power"))
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("task names must be nonempty and unique")
        if any(type(x) is not int for x in task):
            raise ValueError("time and energy must be exact integers")
        C, D, T, p = task
        if not 1 <= C <= D <= T or p <= 0:
            raise ValueError("invalid constrained-deadline task")
        names.append(name)
        tasks.append(task)
    B = tuple(model["beta"])
    if (len(B) <= max(t[1] for t in tasks)
            or any(type(v) is not int or v < 0 for v in B)
            or B[0] != 0 or any(a > b for a, b in zip(B, B[1:]))):
        raise ValueError("need nonnegative nondecreasing beta through Dmax, beta(0)=0")
    return tuple(tasks), M, B, e0, tuple(names)


def fingerprint(case, method, gap):
    # Bind certificate to exact model, method, auxiliary setting and version.
    payload = dict(version=VERSION, model=MODEL, input=case["model"],
                   method=method, gap=gap)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def gap_kind(method, gap):
    if method not in METHODS:
        raise ValueError("unknown AN method")
    if gap == "auto":
        return "freq" if method in (OPT, ANA) else "base"
    if gap in ("base", "freq"):
        return gap
    raise ValueError("unknown gap variant")


@lru_cache(maxsize=4096)
def gap_indices(tasks, M, B, k, X, e0, gap):
    D, pk = tasks[k][1], tasks[k][3]
    fn = bounds.priced_gap if gap == "freq" else bounds.net_lp
    L0 = next((L for L in range(1, D+1)
               if e0+B[L] >= pk+fn(tasks, M, k, X, L-1)), None)
    G = next((L for L in range(1, D+1)
              if B[L] >= pk+fn(tasks, M, k, X, L-1)), None)
    return L0, G


def _common(tasks, M, B, k, X, q, w, e0, gap):
    """Conditions (6), (20), (25) of the AN analysis."""
    N = w-q+1
    H = sum(min(bounds.W(tasks[i], w, X[i]), N) for i in range(k))
    nu = H//N+1
    theta = tasks[k][3] + sum(sorted((t[3] for t in tasks[:k]),
                                      reverse=True)[:nu-1])
    if nu <= M and B[1] >= theta:
        return N, H, dict(kind="slot", N=N, H=H, nu=nu,
                          threshold=theta, supply=B[1])
    L0, G = gap_indices(tasks, M, B, k, X, e0, gap)
    S = 0 if L0 is None or L0 > w else (1 if G is None else 1+(w-L0)//G)
    lhs = q+sum(bounds.W(tasks[i], w, X[i]) for i in range(k))
    if lhs <= S:
        return N, H, dict(kind="gap", gap=gap, L0=L0, G=G, left=lhs, right=S)
    return N, H, None


def _branches(X, k, q, w):
    for b in range(w):
        for z in range(min(b, q-1)+1):
            skip = q > 1 and b >= X[k][0] and z == 0
            yield b, z, skip


def _upper(tasks, M, k, X, q, w, b, z, h, method):
    return bounds.energy_bound(tasks, M, k, X, q, w, b, z, h, method)


@lru_cache(maxsize=32768)
def window(tasks, M, B, k, X, q, w, method, e0, gap):
    N, H, quick = _common(tasks, M, B, k, X, q, w, e0, gap)
    if quick:
        return quick["kind"]
    if H >= M*N:
        return ""
    # Try the cheap internal one-slot check first and the release origin next.
    # Every permitted origin is still searched; h=b uniquely uses e0.
    for b, z, skip in _branches(X, k, q, w):
        if skip:
            continue
        if b and _upper(tasks, M, k, X, q, w, b, z, 0, method) <= B[1]:
            continue
        if _upper(tasks, M, k, X, q, w, b, z, b, method) <= e0+B[b+1]:
            continue
        if any(_upper(tasks, M, k, X, q, w, b, z, h, method) <= B[h+1]
               for h in range(1, b)):
            continue
        return ""
    return "last"


def verify_window(tasks, M, B, k, X, q, w, method, e0, gap):
    """Recompute one window, returning a complete finite proof record."""
    if not q <= w <= tasks[k][1]:
        return None
    N, H, quick = _common(tasks, M, B, k, X, q, w, e0, gap)
    if quick:
        return quick
    if H >= M*N:
        return None
    records = []
    for b, z, skip in _branches(X, k, q, w):
        if skip:
            records.append(dict(b=b, z=z, kind="first_unit_necessary_condition"))
            continue
        # Different origin order from search; all branches require a witness.
        witness = None
        for h in range(b+1):
            upper = _upper(tasks, M, k, X, q, w, b, z, h, method)
            supply = B[h+1]+(e0 if h == b else 0)
            if upper <= supply:
                witness = dict(b=b, z=z, h=h, supply=supply,
                               upper=None if upper == bounds.NEG else upper,
                               kind="infeasible_relaxation" if upper == bounds.NEG else "energy")
                break
        if witness is None:
            return None
        records.append(witness)
    return dict(kind="last", N=N, H=H, branches=records)


def _records(tasks, M, B, X, method, e0, gap):
    if len(X) != len(tasks):
        raise ValueError("profile length mismatch")
    records = []
    for k, (task, profile) in enumerate(zip(tasks, X)):
        C, D, _, _ = task
        if len(profile) != 2:
            raise ValueError("invalid profile")
        F, R = profile
        if type(F) is not int or type(R) is not int or not (
                1 <= F <= R <= D and C <= R and (C != 1 or F == R)):
            raise ValueError("invalid F/R bound")
        full = verify_window(tasks, M, B, k, X, C, R, method, e0, gap)
        if full is None:
            raise ValueError("completion window cannot be replayed")
        first = verify_window(tasks, M, B, k, X, 1, F, method, e0, gap)
        if first is None:
            if F != R:
                raise ValueError("first-unit window cannot be replayed")
            first = dict(kind="implied_by_full", window=R)
        records.append(dict(task=k, first_window=F, completion_window=R,
                            first=first, completion=full))
    return records


def _make_certificate(case, method, gap, tasks, M, B, e0, X, names):
    records = _records(tasks, M, B, X, method, e0, gap)
    return dict(version=VERSION, model=MODEL, method=method, gap=gap,
                input_sha256=fingerprint(case, method, gap),
                profile=[list(pair) for pair in X], task_names=list(names),
                first_bounds=[f for f, _ in X], response_bounds=[r for _, r in X],
                records=records)


def verify_certificate(case, certificate):
    """Reject wrong-model, wrong-input and altered window certificates."""
    try:
        tasks, M, B, e0, names = normalize_case(case)
        method = certificate["method"]
        gap = certificate["gap"]
        if gap not in ("base", "freq") or gap_kind(method, gap) != gap:
            raise ValueError("invalid certificate auxiliary configuration")
        if certificate.get("version") != VERSION or certificate.get("model") != MODEL:
            raise ValueError("wrong AN certificate model/version")
        if certificate.get("input_sha256") != fingerprint(case, method, gap):
            raise ValueError("AN certificate input fingerprint mismatch")
        X = tuple(tuple(p) for p in certificate["profile"])
        expected = _make_certificate(case, method, gap, tasks, M, B, e0, X, names)
        # JSON representation also handles results reloaded from disk.
        # Exact comparison rejects tampered witnesses and extra fields.
        if json.dumps(expected, sort_keys=True) != json.dumps(certificate, sort_keys=True):
            raise ValueError("AN certificate record mismatch")
        return dict(status="PASS", model=MODEL, certified_tasks=len(tasks))
    finally:
        clear()


def clear():
    bounds.clear()
    window.cache_clear()
    gap_indices.cache_clear()


def analyze_case(case, *, method=ANA, gap="auto"):
    """Full q..D search and joint least fixed point; no coarse window option.

    All methods use their reviewed default auxiliary conditions unless an
    explicit gap variant is requested for a controlled comparison. No result
    is certified without replaying the fixed-point windows.
    """
    tasks, M, B, e0, names = normalize_case(case)
    gap = gap_kind(method, gap)
    start = time.process_time()
    X = tuple((1, t[0]) for t in tasks)
    history = []
    limit = 1+sum(t[1]-t[0]+(t[1]-1)*(t[0] > 1) for t in tasks)
    base = dict(version=VERSION, model=MODEL, method=method, gap=gap)
    try:
        for _ in range(limit):
            Y = []
            for k, (C, D, T, p) in enumerate(tasks):
                R = next((w for w in range(C, D+1)
                          if window(tasks, M, B, k, X, C, w, method, e0, gap)), None)
                if R is None:
                    return dict(**base, status="NO_CERTIFICATE", certificate=None,
                                first_unproven_task=names[k], history=history,
                                analysis_cpu_seconds=time.process_time()-start)
                # A proved completion window also proves first-unit completion.
                # Thus scanning 1..R-1 and falling back to R is exactly the
                # min(A_k,Q_k) update; when C=1 both events are identical.
                F = next((w for w in range(1, R)
                          if window(tasks, M, B, k, X, 1, w, method, e0, gap)), R) if C > 1 else R
                Y.append((F, R))
            Y = tuple(Y)
            history.append(dict(profile=X, outputs=Y))
            if not all(f <= g and r <= s for (f, r), (g, s) in zip(X, Y)):
                raise RuntimeError("AN non-monotone joint iteration")
            if Y == X:
                cert = _make_certificate(case, method, gap, tasks, M, B, e0, X, names)
                clear()  # replay without trusting the cached search
                verification = verify_certificate(case, cert)
                return dict(**base, status="CERTIFIED", certificate=cert, history=history,
                            verification=verification, analysis_cpu_seconds=time.process_time()-start)
            X = Y
        raise RuntimeError("AN finite lattice iteration did not stabilize")
    except bounds.AnalysisResourceLimit as exc:
        return dict(**base, status="RESOURCE_LIMIT", certificate=None, history=history,
                    reason=str(exc), analysis_cpu_seconds=time.process_time()-start)
    finally:
        clear()
