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

The service configuration is unchanged across period cells, but each cell
materializes and validates the exact service prefix required by its own maximum
period. This prevents a longer-period request from silently using a truncated
default prefix.

## Commands and hard bound

```text
python3 scripts/run_v9_3_core5.py --config configs/v9_3_core5_smoke.yaml --dry-run
python3 scripts/run_v9_3_core5.py --config configs/v9_3_core5_smoke.yaml --list-cells
python3 scripts/run_v9_3_core5.py --config configs/v9_3_core5_smoke.yaml --max-cells 1 --max-tasksets 1
python3 scripts/run_v9_3_core5.py --config configs/v9_3_core5_smoke.yaml --resume
python3 scripts/analyze_v9_3_core5.py artifacts/v9_3_core5_smoke
```

The runner rejects a request count above `scalability.max_analyses`; the smoke
limit is 20 and its expansion is 16 analyses. Resume, atomic terminal state,
attempt append, signal handling, duplicate prevention, and configuration hash
come from the shared engine.

## Resource semantics

Each attempt stores solver wall and CPU time, total wall, worker startup,
result serialization, IPC remainder, timeout budget, terminal state, and the
v9.3 checked-w/h/q and envelope-call counters. Candidate count and first failed
priority come from the frozen production result.

On Linux, peak RSS is the child solver process's `ru_maxrss` in KiB. It excludes
descendants and counts resident shared-library pages according to platform RSS
semantics. A killed worker may have no returned RSS sample. Spawn
deserialization and fixed-point iteration count are not exposed reliably by
the frozen production interfaces; both are written as `UNAVAILABLE`, never
zero. The runner does not change the solver merely to obtain a counter.

## Timeout and censoring

Completed-only mean/median/p95/max use completed events only. A solver timeout
is a right-censored observation at its configured timeout budget and contributes
to timeout rate, censored count, the lower-bound column, and Kaplan-Meier
restricted mean through the group restriction time. It is never inserted into
an ordinary completed-runtime mean. Numeric/internal terminals are visible but
are not relabeled as timeouts or censored runtime events.

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
