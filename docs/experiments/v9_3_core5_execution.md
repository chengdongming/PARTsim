# ASAP-BLOCK v9.3 CORE-5 resource and scalability execution

CORE-5 measures bounded implementation cost. Worker throughput is reported as
a system-level metric and is never presented as an algorithmic-complexity
improvement. The template is not a frozen formal experiment.

## Single-axis cells and shared execution

`experiments/v9_3/core5_scalability.py` expands task count, core count,
period/time scale, optional utilization, and worker count into independent
cells. Every non-target mathematical dimension is fixed to the declared
baseline. Both RTA variants run in every cell through the common v9.3
production execution engine and taskset store.

The CORE-5 method scope is exactly `[CW_THETA_CW, LOC_THETA_LOC]`. Worker
levels share a store keyed only by frozen mathematical generation inputs;
`worker_count` changes the execution/scalability identity and analysis
concurrency, but never the generation identity, seed, task vector, service
identity, E0, numerical mode, or solver variant.

The service configuration is unchanged across period cells, but each cell
materializes and validates the exact service prefix required by its own maximum
period. This prevents a longer-period request from silently using a truncated
default prefix.

## Commands and hard bound

```text
python3 scripts/run_v9_3_core5.py --config configs/v9_3_core5_smoke.yaml --dry-run
python3 scripts/run_v9_3_core5.py --config configs/v9_3_core5_smoke.yaml --list-cells
python3 scripts/run_v9_3_core5.py --config configs/v9_3_core5_smoke.yaml --resume
python3 scripts/analyze_v9_3_core5.py artifacts/v9_3_core5_smoke
```

The V2 runner requires the complete bounded plan: 8 cells, 16 analyses, and a
hard limit of 20. It rejects a truncated production invocation. Resume,
atomic terminal state, attempt append, signal handling, duplicate prevention,
and configuration hash come from the shared engine.

Each child outcome is validated immediately. A stopped child, request/terminal
set mismatch, P0 witness, technical or unknown terminal, or resource join
failure stops the parent before the next cell. Existing evidence is
materialized, a top-level P0 witness and `STOPPED` checkpoint are written, and
the run CLI returns nonzero.

## Resource semantics

Each attempt stores solver wall and CPU time, total wall, worker startup,
result serialization, IPC remainder, timeout budget, terminal state, and the
v9.3 checked-w/h/q and envelope-call counters. Candidate count and first failed
priority come from the frozen production result.

On Linux, peak RSS is the child solver process's `ru_maxrss` in KiB; macOS
bytes are divided by 1024. It excludes descendants and counts resident
shared-library pages according to platform RSS semantics. A killed no-payload
timeout may have an explicit `EXPECTED_UNAVAILABLE` RSS sample. A
payload-bearing completion or error must include RSS; its absence is a P0
resource-contract failure. Spawn
deserialization and fixed-point iteration count are not exposed reliably by
the frozen production interfaces; both are written as `UNAVAILABLE`, never
zero. The runner does not change the solver merely to obtain a counter.

`resource_usage.csv` retains every attempt. Scientific search counters and
summary RSS use only the final attempt, so retries are not double-counted.
Summary fields expose available-observation counts and label mixed evidence
`PARTIAL`; a group with no available counter remains `UNAVAILABLE`, not zero.

## Timeout and censoring

`COMPLETED` and `NO_CANDIDATE` are scientific completions. Only the strict
`TIMEOUT` solver status is right-censored at the configured budget;
`outer_timeout` is supporting provenance and never overrides the solver
terminal class. Internal, numeric, invalid, worker/IPC, unknown, and malformed
terminals are technical failures even if a contradictory outer-timeout flag is
present. Technical terminals enter neither completed runtime nor timeout-rate
denominators nor RMST, and stop the top-level run. Completed-only
mean/median/p95/max use completion events only; timeout rate and Kaplan-Meier
RMST use exactly completed plus timeout observations and report the restriction
time. All-censored input is valid; zero-evaluable groups remain unavailable.

Worker pairing is by `(taskset index, analysis variant)` across every declared
worker level, then checks generation/taskset identities, seed, M/n/U/period,
C/D/T/P and priority/power hashes, E0, service, numerical mode, and variant.
Scientific pairs must have identical solver/certification/task vectors;
timeout pairs are `TIMEOUT_CENSORED`; missing, technical, mathematical, and
genuine semantic mismatches are P0.

## V2 artifact closure and analyzer

`ASAP_BLOCK_V9_3_CORE5_RUN_V2` and
`ASAP_BLOCK_V9_3_CORE5_CHECKPOINT_V2` use phases `INITIALIZED`, `RUNNING`,
`FINALIZING`, `COMPLETED`, and `STOPPED`. Success writes all artifacts, writes a
`FINALIZING` checkpoint, creates and immediately validates the exact SHA-256
manifest and artifact structure, then atomically writes `COMPLETED` last.
Checkpoint and manifest are excluded from the manifest itself.

The standalone analyzer first requires a valid completed V2 closure, rebuilds
summary and plot data from authoritative raw tables, validates the rebuilt
evidence, and reseals the manifest. Empty, partial, stopped, duplicate,
header-mismatched, missing/extra-terminal, symlinked, path-traversing, or hash-
tampered roots return nonzero; absence of evidence is never reported as zero
P0.

## Outputs and smoke scope

Outputs include scalability cells, tasksets, requests, attempts, taskset/task
results, per-attempt resource usage, runtime censoring, CSV/JSON summaries,
plot data, worker semantic checks, failures, checkpoint, configuration, and
file hashes. Plot data supports runtime by task/core/period scale, RSS,
search counts, timeout rate, variant comparison, and worker throughput.

The smoke uses task counts 6/10, core counts 2/4, period ranges 40-200 and an
exact 2x 80-400 range accepted by the existing generator interface, workers
1/2, one taskset per level, U=0.2, and the two main variants. Worker cells use
identical mathematical inputs and require identical terminal/candidate
semantics. This bounded run produces implementation evidence only, not paper
complexity claims or frozen parameters.
