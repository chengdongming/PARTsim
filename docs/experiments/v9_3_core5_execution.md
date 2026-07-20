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

## Frozen formal profiles

The formal profiles use `ASAP_BLOCK_V9_3_CORE5_FORMAL_PLAN_V1`,
`ASAP_BLOCK_V9_3_CORE5_FORMAL_RUN_V1`, and a distinct formal checkpoint
schema. The CLI dispatches by validated profile; the V2 runner and its exact
8/16/20 guard are unchanged. Formal and V2 analyzers inspect the run schema
before reading data and reject mixed profiles. The formal configs have unique
output roots, taskset stores, config hashes, and authorization-seal bindings.

Both formal profiles use E0 `1/20`, capacity 20, the two production variants,
exact-rational mode, one retry at 240 seconds after a 120-second timeout, and
the `synthetic-piecewise-default-v1` service. The latter explicitly disables
real-solar input in the materialized system configuration; its name alone does
not select the source.

### CORE-5A algorithmic profile

`configs/v9_3_core5a_formal_algorithmic.yaml` expands each of
`U={3/10,1/2,7/10}` over:

- task count `n={5,10,15,20}` at M=4 and periods 40..200;
- cores `M={2,4,8}` at n=20 and periods 40..200;
- exact time scale `{1,2,4}` at M=4 and n=10.

Duplicate baselines are eliminated by the full mathematical input, leaving
eight configurations per utilization and 24 cells. With 100 tasksets and two
methods this is 4,800 mathematical/solver requests. Scale 2 and 4 reuse the
same frozen scale-1 source taskset and multiply C, D, and T by the exact integer
factor. Utilization, D/T, task order/identity, power, and source hash remain
paired. Worker count is fixed at one. Runtime distributions, peak RSS, solver
counters, retries, timeouts, and censoring are algorithmic outputs; throughput
worker comparisons are excluded.

### CORE-5B worker profile

`configs/v9_3_core5b_formal_workers.yaml` fixes M=4, n=10, periods 40..200,
the three utilizations, 50 tasksets per utilization, and both methods: exactly
300 mathematical requests. Worker counts `{1,2,4,8}` each have five
repetitions. The 20-run order is a deterministic permutation seeded by
93044505, producing 6,000 solver executions.

Worker and repetition are operational dimensions and never enter the
mathematical input hash. Every repeated request must match input hash,
terminal, response bounds, fixed-point/search/inverse-service counters, and
candidate count. Any mismatch is P0. Reports include wall time, analyses/s,
child CPU time, peak RSS, speedup, and parallel efficiency. Single-request
worker runtimes are marked as excluded from the CORE-5A complexity regression.
The analyzer rejects missing worker/repetition children or a mixed config.

Formal parent resume classifies each child artifact root before execution. A
durably complete child is validated and skipped; a valid partial child resumes
through the shared engine; a missing child starts fresh. Configuration,
authorization-seal, checkpoint, table-schema, or request/terminal identity
mismatches fail closed. A signal interruption inside a child leaves the parent
`INTERRUPTED`, so `--resume` continues that child without repeating its
durable terminals.

CORE-5A writes `core5a_metrics.json`, reconstructed exclusively from child
requests, attempts, taskset/task terminals, and RSS observations. It persists
terminal counts, completed-runtime median/P95/max, peak RSS, available search
and inverse-service counters, candidate counts, retry/timeout counts, and
censoring states. Fixed-point iterations are not exposed by the frozen solver
interface and therefore remain explicit `null` with status `UNAVAILABLE`,
never a synthetic zero.

CORE-5B writes `worker_semantic_checks.csv`. Each of the 300 rows proves the
20-execution closure: workers `{1,2,4,8}`, five distinct repetitions per
worker, and exact equality of input hash, terminal, response bounds, search
and inverse-service counters, and candidate count. Both profiles finish with a
parent `file_hashes.sha256`; the formal analyzer validates that manifest and
then independently reconstructs the profile-specific persisted artifact from
all child evidence.

Inspect either frozen plan without generating tasksets or invoking the solver:

```text
python3 scripts/run_v9_3_core5.py --config configs/v9_3_core5a_formal_algorithmic.yaml --dry-run
python3 scripts/run_v9_3_core5.py --config configs/v9_3_core5a_formal_algorithmic.yaml --list-cells
python3 scripts/run_v9_3_core5.py --config configs/v9_3_core5b_formal_workers.yaml --dry-run
python3 scripts/run_v9_3_core5.py --config configs/v9_3_core5b_formal_workers.yaml --list-cells
```

Do not combine these configs or output roots. No formal experiment was run
while freezing the contracts.
