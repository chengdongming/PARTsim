"""Least simultaneous certificate in the frozen v2 predicate family.

The one-step map F(rho) returns each task's least valid candidate window.
F is isotone: larger residence windows only enlarge workload/energy feasible
sets and cannot decrease processor progress requirements. Start at C and
increase to the least fixed point, then verify all tasks together. Intermediate
vectors are NOT bounds. This does not claim the true minimum worst-case bound.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent/"baseline_v2"))
from an_rta_joint import (Task, ProfileAnalyzer, verify_certificate, normalized_inputs,
                          require, integer, input_fingerprint, VERSION, SCHEMA, MODEL)

SOLVER_VERSION = "AN_LEAST_JOINT_CERTIFICATE_V3"


def evaluate(tasks, cores, beta, e0, rho):
    engine = ProfileAnalyzer(tasks, cores, e0, rho)
    try:
        return [engine.candidate_profile(k, beta) for k in range(len(tasks))]
    finally:
        engine.clear()


def make_certificate(tasks, cores, beta, e0, rho, answers):
    cert = {"version":VERSION, "schema":SCHEMA, "model":MODEL,
            "input_sha256":input_fingerprint(tasks, cores, beta, e0),
            "residence_bounds":list(rho), "response_bounds":[a["R"] for a in answers],
            "details":answers}
    counts = verify_certificate(tasks, cores, beta, e0, cert)
    return cert, counts


def least_certificate(tasks, cores, beta, e0, *, max_iterations=None):
    tasks, beta = normalized_inputs(tasks, cores, beta, e0)
    natural_limit = 1+sum(t.D-t.C for t in tasks)
    if max_iterations is not None:
        require(integer(max_iterations, 1), "invalid iteration budget")
    budget = natural_limit if max_iterations is None else min(max_iterations, natural_limit)
    rho, history = tuple(t.C for t in tasks), []
    out = {"solver_version":SOLVER_VERSION, "taskset_proven":False, "certificate":None,
           "candidate_history":history, "minimality_scope":"frozen v2 simultaneous certificate predicates"}
    for iteration in range(budget):
        answers = evaluate(tasks, cores, beta, e0, rho)
        vector = tuple(a["R"] if a is not None else None for a in answers)
        history.append({"rho":list(rho), "outputs":list(vector)})
        if any(w is None for w in vector):
            # From bottom iteration, every putative post-fixed certificate must
            # dominate rho. Isotonicity rules it out if a component is infinite.
            out.update(status="NO_CERTIFICATE_IN_THIS_FAMILY")
            return out
        require(all(c <= w for c,w in zip(rho,vector)), "one-step map violated ascending monotonicity", RuntimeError)
        if vector == rho:
            cert, counts = make_certificate(tasks, cores, beta, e0, rho, answers)
            out.update(status="CERTIFIED", taskset_proven=True, certificate=cert, verification=counts)
            return out
        rho = vector
    require(budget < natural_limit, "ascending integer iteration failed to terminate", RuntimeError)
    out["status"] = "ITERATION_LIMIT"
    return out


def refine_certified(tasks, cores, beta, e0, initial_certificate):
    """Separate top-down control: continue refining already certified v2 bounds."""
    verify_certificate(tasks, cores, beta, e0, initial_certificate)
    rho = tuple(initial_certificate["response_bounds"])
    history = []
    for _ in range(1+sum(r-t.C for r,t in zip(rho,tasks))):
        answers = evaluate(tasks, cores, beta, e0, rho)
        require(all(a is not None for a in answers), "lost a previously valid whole certificate", RuntimeError)
        vector = tuple(a["R"] for a in answers)
        require(all(t.C <= w <= r for t,w,r in zip(tasks,vector,rho)), "certified refinement increased", RuntimeError)
        cert, counts = make_certificate(tasks, cores, beta, e0, rho, answers)
        history.append({"rho":list(rho), "outputs":list(vector)})
        if vector == rho:
            return {"status":"CERTIFIED", "certificate":cert, "verification":counts, "history":history}
        rho = vector
    raise RuntimeError("descending integer iteration failed to terminate")
