# ASAP-BLOCK v9.3 five-variant pilot execution plan

## Reused production paths

- `global_task_generator.py` remains the task generator. The pilot passes the
  frozen task count, normalized-to-total utilization, period bounds, implicit
  deadlines, compensated WCET rounding, tolerance, and a deterministic seed.
- Task parsing, RM/DM ordering under implicit deadlines, the scheduler power
  model, and the synthetic harvesting trace reuse `asap_block_rta.py` helpers.
- Every configuration is dispatched through
  `asap_block_v9_3_runner.dispatch_rta_version("v9.3", ...)`, which validates
  the service curve and calls the production
  `asap_block_rta_v9_3_taskset.analyze_taskset_v9_3()` entry.
- Per-taskset, per-task, and dependency rows use
  `serialize_taskset_analysis_v1_3_12()` and the frozen v1.3.12 schema binding.

## Minimal pilot adapters

`scripts/run_v9_3_pilot.py` materializes the existing RTA-only synthetic
energy convention, converts the existing per-tick power and service values to
exact rationals, executes hard checks, and writes pilot projections plus the
canonical serializer outputs. `scripts/analyze_v9_3_pilot.py` rechecks and
summarizes a completed output directory. No experiment infrastructure is
redesigned.

Each configuration runs in an isolated worker. A shared 15-second budget is
delegated to the existing production single-task solver, so budget exhaustion
returns the analyzer's formal `TIMEOUT` terminal state and the remaining prefix
records. The worker process deadline is retained only as a fail-closed guard
against a non-returning implementation; its two-second shutdown/serialization
allowance is outside the frozen 15-second production analysis budget.

## Five-configuration schedule

For each taskset, execution is strictly `CW_D`, `LOC_D`, `CW_THETA_CW`,
`LOC_THETA_CW`, then `LOC_THETA_LOC`. The fourth request receives only the
frozen `CW_THETA_CW` result for that taskset. An uncertified source produces
the production dependency-not-applicable terminal result; no deadline or old
RTA fallback is permitted.

## Deterministic taskset identity

The generation seed is SHA-256-derived from the ASCII tuple
`(base_seed, U_norm index, E0 index, taskset index)` and never uses Python
`hash()`. The taskset ID binds those indices to the canonical task semantics.
Raw YAML timestamps are excluded from semantic identity. Smoke reproducibility
compares generated semantic hashes and all non-runtime analysis outcomes. For
a task stopped by the wall-clock timeout only, the final `w/h/q` and envelope
visit counts are cutoff-dependent and excluded; the timeout task, completed
prefix, candidate/certification states, and failure code must still match.

## Pilot outputs

The pilot records generation coordinates, seed, semantic identity, actual
utilization, E0, variant/role, solver and certification states, candidate and
certified counts, failure priority, wall/CPU time, timeout/numeric/dependency/
dominance/interface states, source and carry-in hashes, per-task candidates,
witnesses, scan counters, failure provenance, dominance comparisons, failures,
summary metrics, and a recursive SHA-256 manifest. Canonical v1.3.12 result
rows are written without adding contract fields; pilot-only measurements live
in separate pilot CSVs.

## Frozen code outside pilot scope

The pilot does not modify v9.3 theory or formulas, candidate/envelope search,
joint certification/finalization, fixed-carry-in semantics, service-curve
definition, the v1.3.12 contract or validators, C++ schedulers, generator
semantics, v20.4/v21 implementations, existing formal results, or CORE-0A
evidence and authority machinery.
