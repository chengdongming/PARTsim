"""AN joint-vector RTA v2, research implementation with checked certificates.

Conditional model: integer ticks, sporadic sequential tasks, fixed priority in
input order, debit before harvest, no overflow, and E >= release_energy at
EVERY job release. beta bounds arrivals in EVERY interval of a given length.
The implementation does not infer these environmental guarantees from samples.

The frozen mathematical envelopes are unchanged. Tentative residence vectors
are search hypotheses; only a complete simultaneous certificate is accepted.
See PROOF_AND_MODEL.md for the first-violation argument and its scope.
"""
from dataclasses import dataclass, asdict
import hashlib
import json

from frozen_core.an_ab_profile import ProfileAnalyzer

VERSION = "AN_JOINT_VECTOR_V2"
MODEL = "NO_OVERFLOW_EVERY_RELEASE_FLOOR_DEBIT_BEFORE_HARVEST"
SCHEMA = 1


def require(condition, message, error=ValueError):
    if not condition:
        raise error(message)


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


@dataclass(frozen=True)
class Task:
    C: int
    T: int
    D: int
    p: int

    def __post_init__(self):
        require(all(integer(x, 1) for x in (self.C, self.T, self.D, self.p)),
                "C, T, D and p must be positive exact integers (not bool)")
        require(self.C <= self.D <= self.T, "require C <= D <= T")


def normalized_inputs(tasks, cores, beta, release_energy):
    tasks, beta = tuple(tasks), tuple(beta)
    require(bool(tasks), "empty taskset")
    require(all(isinstance(t, Task) for t in tasks), "use validated Task objects")
    require(integer(cores, 1), "cores must be a positive exact integer")
    require(integer(release_energy), "release_energy must be a nonnegative exact integer")
    require(len(beta) > max(t.D for t in tasks), "insufficient beta horizon")
    require(all(integer(x) for x in beta), "beta values must be nonnegative exact integers")
    require(beta[0] == 0, "require beta(0) = 0")
    require(all(a <= b for a, b in zip(beta, beta[1:])), "beta must be nondecreasing")
    return tasks, beta


def input_fingerprint(tasks, cores, beta, release_energy):
    payload = {"schema": SCHEMA, "model": MODEL, "tasks": [asdict(t) for t in tasks],
               "cores": cores, "beta": list(beta), "release_energy": release_energy}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def verify_certificate(tasks, cores, beta, release_energy, certificate):
    tasks, beta = normalized_inputs(tasks, cores, beta, release_energy)
    require(type(certificate) is dict, "missing certificate")
    require(certificate.get("version") == VERSION and type(certificate.get("schema")) is int
            and certificate["schema"] == SCHEMA,
            "unsupported certificate version")
    require(certificate.get("model") == MODEL, "model mismatch")
    require(certificate.get("input_sha256") == input_fingerprint(tasks, cores, beta, release_energy),
            "certificate belongs to different inputs")
    rho, windows, details = (certificate.get(k) for k in
                             ("residence_bounds", "response_bounds", "details"))
    require(all(type(v) is list and len(v) == len(tasks) for v in (rho, windows, details)),
            "certificate must contain every task")
    require(all(integer(r, 1) and integer(w, 1) and t.C <= w <= r <= t.D
                for t, w, r in zip(tasks, windows, rho)), "require C <= w <= rho <= D")
    engine = ProfileAnalyzer(tasks, cores, release_energy, rho)
    counts = {"tasks": len(tasks), "checkpoints": 0, "prefix": 0, "suffix": 0}
    try:
        for k, (w, detail) in enumerate(zip(windows, details)):
            require(type(detail) is dict, f"task {k}: missing detail")
            a = engine.progress(k, w)
            require(integer(detail.get("R"), 1) and detail["R"] == w and
                    integer(detail.get("A"), 1) and detail["A"] == a and a <= w,
                    f"task {k}: invalid processor witness")
            hs, points = detail.get("h"), detail.get("checkpoints")
            require(type(hs) is list and type(points) is list and len(hs) == len(points) == a,
                    f"task {k}: incomplete progress certificate")
            require(all(integer(h) and h <= w-a for h in hs) and
                    all(x <= y for x, y in zip(hs, hs[1:])), f"task {k}: invalid blocking budgets")
            for q, (h, point) in enumerate(zip(hs, points), 1):
                s = h + q - 1
                require(s < w and type(point) is dict, f"task {k}: invalid checkpoint")
                branch = point.get("branch")
                if branch == "prefix":
                    value, supply = engine.phase(k, q, h), release_energy + beta[s]
                    recorded = point.get("energy")
                    require((recorded is None if value is None else integer(recorded) and recorded == value)
                            and "energy" in point and integer(point.get("supply")) and point["supply"] == supply,
                            f"task {k}: changed prefix evidence")
                    require(value is None or value <= supply, f"task {k}: prefix inequality fails")
                elif branch == "suffix":
                    ell = point.get("ell")
                    require(integer(ell, 1) and ell <= s, f"task {k}: suffix starts before release")
                    require(beta[ell] >= engine.suffix_energy(k, ell-1),
                            f"task {k}: suffix inequality fails")
                else:
                    raise ValueError(f"task {k}: unknown energy branch")
                counts["checkpoints"] += 1
                counts[branch] += 1
    finally:
        engine.clear()
    return counts


def analyze(tasks, cores, beta, release_energy, *, max_iterations=None):
    tasks, beta = normalized_inputs(tasks, cores, beta, release_energy)
    natural_limit = 1 + sum(t.D-t.C for t in tasks)
    if max_iterations is not None:
        require(integer(max_iterations, 1), "max_iterations must be a positive exact integer")
    budget = natural_limit if max_iterations is None else min(natural_limit, max_iterations)
    rho = tuple(t.D for t in tasks)
    history = []
    output = {"version": VERSION, "model": MODEL, "taskset_proven": False,
              "certificate": None, "candidate_history": history}
    for iteration in range(1, budget+1):
        engine = ProfileAnalyzer(tasks, cores, release_energy, rho)
        try:
            answers = [engine.candidate_profile(k, beta) for k in range(len(tasks))]
        finally:
            engine.clear()
        windows = tuple(a["R"] if a is not None else None for a in answers)
        closed = all(w is not None and w <= r for w, r in zip(windows, rho))
        history.append({"iteration": iteration, "candidate_residence": list(rho),
                        "candidate_outputs": list(windows), "simultaneously_closed": closed})
        if closed:
            certificate = {"version": VERSION, "schema": SCHEMA, "model": MODEL,
                           "input_sha256": input_fingerprint(tasks, cores, beta, release_energy),
                           "residence_bounds": list(rho), "response_bounds": list(windows),
                           "details": answers}
            verification = verify_certificate(tasks, cores, beta, release_energy, certificate)
            output.update(status="CERTIFIED", taskset_proven=True, certificate=certificate,
                          verification=verification)
            return output
        updated = tuple(min(r, w) if w is not None else r for r, w in zip(rho, windows))
        require(all(t.C <= new <= old <= t.D for t, new, old in zip(tasks, updated, rho)),
                "candidate descent violated its domain", RuntimeError)
        if updated == rho:
            output["status"] = "NO_CLOSED_VECTOR"
            return output
        rho = updated
    require(budget < natural_limit, "integer descent failed to terminate", RuntimeError)
    output["status"] = "ITERATION_LIMIT"
    return output
