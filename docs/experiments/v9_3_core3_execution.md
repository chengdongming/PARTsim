# ASAP-BLOCK v9.3 CORE-3 execution guide

CORE-3 pairs the two production main-method RTAs with one discrete
ASAP-BLOCK simulation of the same frozen taskset. It searches for empirical
counterexamples and measures observed response-time tightness; a finite
simulation pass is never reported as a proof of theoretical soundness.

## Entry points and invariants

- Runner: `python3 scripts/run_v9_3_core3.py --config <yaml>`.
- Read-only re-analysis: `python3 scripts/analyze_v9_3_core3.py <run-root>`.
- RTA remains the shared production path
  `experiments.v9_3.execution_engine.ExecutionEngine` ->
  `asap_block_v9_3_runner.dispatch_rta_version` ->
  `asap_block_rta_v9_3_taskset.analyze_taskset_v9_3`.
- Simulation uses the `gpfp_asap_block` C++ factory and schema-v2 JSON trace.
- Every serialized simulation terminal explicitly carries integer
  `trace_schema_version: 2`. Deserialization rejects a missing field, booleans,
  strings, null, and every version other than 2.
- Frozen tasksets, seeds, cells, taskset hashes, task ordering, attempt journal,
  terminal records, and config-hash resume checks are the CORE-1/CORE-2
  framework's existing formats.
- The simulator projection preserves `C,D,T,P`, RM priority rank, release
  phase, workload, `M`, harvesting source, and deadline convention. It fixes
  the template's CPU speed parameters to one so one declared execution unit is
  one simulation tick; power parameters are unchanged.

The detailed tick/release/deadline/energy mapping and its source evidence are
in `docs/audits/v9_3_core3_semantic_mapping.md`.

## Energy mapping

These values are distinct and are persisted separately:

- `energy.initial_energy_values`: RTA's conditional per-job release guarantee
  `E(r_J) >= E0`;
- `energy.simulation_initial_battery`: actual battery at simulation time zero;
- the simulation harvesting trace: actual configured arrivals during the run;
- the validated RTA `beta`: minimum service over windows of the same configured
  harvesting input.

CORE-3 checks every arrival event's pre-execution energy against the selected
RTA `E0`. It also requires
`battery_capacity >= simulation_initial_battery + total offered harvest`
through `maximum_horizon`, a conservative no-overflow guard. Failure of either
premise is excluded from soundness/tightness rather than converted to a miss or
pass.

## Horizon and censoring

`simulation.horizon`, `warmup`, `minimum_jobs_per_task`, `maximum_horizon`, and
`horizon_extension_policy` control observation. With `double`, an insufficient
run is restarted deterministically at twice the horizon, capped by the
maximum. The five terminal statuses are:

- `SIM_PASS_OBSERVED`;
- `SIM_DEADLINE_MISS`;
- `SIM_HORIZON_INSUFFICIENT`;
- `SIM_RUNTIME_TIMEOUT`;
- `SIM_INTERNAL_ERROR`.

Every release at or after warmup is indexed by task and release order. A tail
job unfinished at the final boundary is recorded as right-censored. A task
enters tightness only after it has the configured minimum number of completed
eligible jobs; insufficient tasks do not enter the ordinary gap distribution.
Deadline misses anywhere in the run remain definitive even if the job was
released before warmup.

## Job and tightness outputs

`simulation_job_results.csv` records task ID, job index, release, completion,
absolute deadline, response, miss, first execution, preemption count,
energy-blocked ticks, processor-wait ticks, and censoring. Per-task
`R_sim_max` is the maximum completed-job response, never an average.

The common tightness domain requires a legal task-level RTA candidate and an
eligible, miss-free simulation task. It exports absolute gap, normalized gap,
ratio, and deadline slack. Method summaries include mean/median/p95/max plus
exact equality and the LOC-versus-CW gap relation. Equal candidates are
asserted to yield equal tightness; an internal envelope difference is not
counted as response-time improvement.

The taskset/method soundness matrix keeps the raw simulation status separate
from comparison eligibility. Only E0-valid, no-overflow, comparison-eligible
observations enter the four RTA/simulation PASS/FAIL quadrants. Invalid E0 is
`ASSUMPTION_E0_NOT_SATISFIED`; a missing no-overflow proof and other observation
ineligibility have separate classes; insufficient horizon is
`HORIZON_CENSORED`; simulator timeout/error remains `SIM_TIMEOUT_OR_ERROR`.
These classes have independent counts and never enter the soundness
denominator.

An eligible `RTA_PASS_SIM_FAIL` is recorded as a taskset-level P0. Independently,
each eligible `CANDIDATE_FOUND` task with `R_sim_max > R_RTA` is recorded as
`RTA_RESPONSE_BOUND_VIOLATION`, even when `R_sim_max <= D` and no deadline was
missed. Its witness preserves the taskset/hash, method, task parameters, E0,
candidate, observation, negative gap, simulation/job tables, parsed job trace,
and retained raw trace when available. Deadline and response-bound failures may
both be present, while the summary reports their union as one unique
counterexample taskset. Either P0 stops the run when fail-fast is enabled.

## Execution

Inspect without writing tasksets or invoking an analyzer:

```bash
python3 scripts/run_v9_3_core3.py \
  --config configs/v9_3_core3_smoke.yaml --dry-run
```

Run and resume the bounded smoke:

```bash
python3 scripts/run_v9_3_core3.py \
  --config configs/v9_3_core3_smoke.yaml
python3 scripts/run_v9_3_core3.py \
  --config configs/v9_3_core3_smoke.yaml --resume
python3 scripts/analyze_v9_3_core3.py artifacts/v9_3_core3_smoke
```

Resume reuses four RTA terminal records and two simulation terminal records;
conflicting duplicate terminals and a changed config hash fail closed. CORE-3
comparison artifacts also carry artifact contract version 2. Resume and the
read-only analyzer reject pre-v2 comparison artifacts instead of treating an
old classification as valid.
When `checkpoint.json` exists, both entry points validate its artifact contract
version, checkpoint schema/version, `CORE-3` identity, and configuration hash
before looking for simulation terminals or materialized comparison outputs.
Consequently a checkpoint-only v1 or versionless run root fails closed, while a
root with neither checkpoint nor older comparison artifacts remains a valid new
run root.
Normal pass traces are parsed and deleted. Full traces are retained only for a
deadline miss, semantic/internal error, or invalid release-energy premise.

## Required run products

Each run writes `run_config.yaml`, `run_metadata.json`,
`generated_tasksets.csv`, `rta_results.csv`, the three simulation result
tables, `soundness_matrix.csv`, `response_bound_violations.csv`, both tightness tables,
`censoring_summary.csv`, `runtime_summary.csv`, `failures.csv`,
`checkpoint.json`, `summary.json`, `summary.csv`, `core3_plot_data.csv`, and
`file_hashes.sha256`. The shared engine's cells, requests, attempts, per-task
RTA rows, terminal state, and canonical taskset store remain alongside them.

## Recorded smoke (not a formal experiment)

Configuration: `M=4`, `task_n=10`, constrained deadlines,
`U={0.2,0.3}`, `E0={1}`, one taskset per cell, two production RTA methods,
800-tick initial/1600-tick maximum horizon, and three minimum completed jobs
per task. Simulation starts at 20 J with 100 J capacity; both release-time E0
and no-overflow checks passed.

Observed counts:

- 2 frozen tasksets, 4 terminal RTA analyses, 2 terminal simulations;
- both simulations `SIM_PASS_OBSERVED`; no extension was required;
- all four method pairs `RTA_FAIL_SIM_PASS` (terminal RTA status
  `NO_CANDIDATE`), so no certified-taskset soundness claim is made;
- 12 unique tasks (24 method-task rows) were in the legal-candidate/observed
  tightness domain;
- for each method: mean absolute gap 1.5 ticks, median 0, p95/max 8;
  mean normalized gap about 0.304894, max 2.666667; mean ratio about 1.304894,
  max 3.666667; 8 exact equalities;
- LOC gap equals CW gap for all 12 common tasks; strict LOC improvement was not
  required by smoke;
- P0/P1/P2 = 0/0/0 and no soundness-violation candidate occurred;
- resume produced 4 completed RTA IDs, 2 completed simulation IDs, no duplicate
  rows, and the SHA-256 manifest verifies.

These observations are smoke evidence for the runner and parser only. No
large-scale experiment or paper conclusion was produced.
