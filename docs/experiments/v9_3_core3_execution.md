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

## Frozen formal B20/B100 r2 tracks

The r1 files `configs/v9_3_core3_formal_b20.yaml` and
`configs/v9_3_core3_formal_b100.yaml` are retained only as audit history. They
set initial battery equal to capacity. The real-solar trace offers
`11666700000000003/10000000000000` J over 30,000 ms, so both have negative
harvest headroom and fail the mandatory preflight. These r1 parameters never
produced formal experiment results, are superseded by r2, and must not be used
for a formal run. They declare the repository-wide non-idle workload pool so
the common config loader can inspect them, but that schema migration does not
reauthorize them or change their failing energy result.

The only authorized formal entry configurations are
`configs/v9_3_core3_formal_b20_r2.yaml` and
`configs/v9_3_core3_formal_b100_r2.yaml`. Both use simulation initial battery
1 J, which covers the maximum release certificate E0=1 J. Their scientific
energy settings differ only in finite battery capacity: 20 J versus 100 J.
They share the same service identity, solar trace, solar scale, generation
identity, taskset indices, and seeds; experiment IDs, output roots, taskset
stores, and config hashes remain disjoint.

After the workload-contract-v2 migration, the r2 experiment IDs are
`asap-block-v9.3-core3-formal-b20-r2-workload-contract-v2` and
`asap-block-v9.3-core3-formal-b100-r2-workload-contract-v2`. Their output roots
are respectively `results/v9_3_core3_formal_b20_r2_workload_contract_v2` and
`results/v9_3_core3_formal_b100_r2_workload_contract_v2`; their stores are
`results/v9_3_core3_taskset_store_formal_b20_r2_workload_contract_v2` and
`results/v9_3_core3_taskset_store_formal_b100_r2_workload_contract_v2`. These
fresh paths cannot resume a legacy V1/V2 store. The normalized config hashes,
generation identities, seeds, and taskset IDs consequently migrate; the
scientific energy parameters and B20/B100 pairing rules do not.

The real-solar scale is frozen by a result-independent feasibility rule. At
the template's 1 m² reference area, use the same data, day/time, efficiency,
tick mapping, system projection, harvest constructor, and 30,000 ms horizon as
runtime to compute `H_raw`. Search the predetermined sequence
`1, 1/2, 1/4, ...` and choose the largest `s` satisfying
`1 + s*H_raw <= 20 - 1`, where the last 1 J is a fixed safety margin. This
selects `s=1/128`; therefore effective `pv_area_m2=1/128`. The choice does not
use RTA acceptance, simulation misses, response times, or runtime results.
B100 deliberately uses the same scale and receives no separate harvest boost.

Both r2 tracks expand the same `M=4`, `n=10`, constrained-deadline generation
grid: eight utilization levels, indices `0..199`, seed `930433`, three release
assumptions, and two methods. Each has 24 cells, 1,600 unique tasksets, 9,600
RTA requests, 1,600 simulations, and 11,200 terminals. The fixed horizon and
extension policy remain 30,000 and `none`; timeout/retry parameters are
unchanged. `simulation.reuse_across_e0: true` continues to project one
simulation onto the three E0 assumptions while recomputing each release gate.

`--preflight` is read-only: it uses a cleaned `TemporaryDirectory`, creates no
output root, taskset store, checkpoint, or terminal record, and returns nonzero
when the contract fails. `--dry-run` may include the same audit under
`energy_preflight`. A real runner invocation executes this identical gate
before the first output directory, taskset, RTA request, checkpoint, or
simulation is created. Missing inputs, non-finite/negative harvest, invalid
scales, and insufficient capacity fail closed.

Configuration loading first enforces the independent global workload gate:
the exact stable pool is `[bzip2, control, decrypt, encrypt, hash]`, `idle` is
reserved for system state, and each generated or loaded task must match the
actual power model exactly. Passing that gate cannot bypass the energy
preflight, and passing the energy preflight cannot make a legacy or malformed
workload store executable.

Record the gates and plans before any separately authorized formal run:

```bash
python3 scripts/run_v9_3_core3.py --config configs/v9_3_core3_formal_b20_r2.yaml --preflight
python3 scripts/run_v9_3_core3.py --config configs/v9_3_core3_formal_b100_r2.yaml --preflight
python3 scripts/run_v9_3_core3.py --config configs/v9_3_core3_formal_b20_r2.yaml --dry-run
python3 scripts/run_v9_3_core3.py --config configs/v9_3_core3_formal_b100_r2.yaml --list-cells
```

The following B20 excerpt can be embedded directly in an artifact audit
record; all energy identities are canonical fractions:

```json
{
  "schema": "ASAP_BLOCK_V9_3_CORE3_ENERGY_PREFLIGHT_V1",
  "service_curve_id": "paired-real-solar-service-v2-dyadic-1-128",
  "system_template_sha256": "7b0f16f4a0c248b1da7e125d6402b8ca073fe5301346b881018e730f92d27de9",
  "solar_data_sha256": "c251c931560ed2498fd9d9e9ded74f923d64ac57e3d9806952752ee21079e44e",
  "horizon_ms": 30000,
  "raw_reference_pv_area_m2": "1",
  "raw_offered_harvest_j": "11666700000000003/10000000000000",
  "dyadic_scale_selection_rule": "largest_feasible_dyadic_v1",
  "largest_feasible_dyadic_scale": "1/128",
  "applied_solar_scale": "1/128",
  "pv_area_m2": "1/128",
  "scaled_offered_harvest_j": "18229218750000003/2000000000000000",
  "simulation_initial_battery_j": "1",
  "battery_capacity_j": "20",
  "required_capacity_j": "20229218750000003/2000000000000000",
  "available_headroom_j": "19770781249999997/2000000000000000",
  "required_safety_margin_j": "1",
  "no_overflow_preflight_valid": true
}
```

No formal experiment was run while repairing and freezing r2. A finite
simulation remains empirical observation, not a theoretical proof.

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
