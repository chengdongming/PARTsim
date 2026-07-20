# v9.3 EXT-1B formal mechanism experiment manifest

Status: preregistered formal profiles; no formal simulation has been run.

## Repository identity and scope

- Baseline commit: `8259cda80d593de45c1416d700bd165682db419d`.
- Branch: `experiment/v9-3-ext1b-formal-mechanisms-r1`.
- PR commit: the immutable head SHA recorded by the Draft PR and by each
  post-commit plan-only environment manifest. A Git commit cannot contain its
  own SHA; the PR head and environment manifest are the authorities.
- EXT-1B formal status: `FORMAL`.
- The CORE-1/2 status `FROZEN_FOR_FORMAL_EXECUTION` and its file-authorization
  protocol do not apply to `Ext1BRunner` and must not be copied here.
- This freeze changes only EXT-1B configuration acceptance. Runner execution,
  generation, source-index/retry behavior, taskset-store semantics, pairing,
  observation, auditors, scheduler semantics, the simulator kernel, workload
  power model, and RTA are unchanged.

These are mechanism experiments, not utilization-versus-acceptance performance
experiments. A deadline miss, including misses under every compared scheduler,
is not by itself a mechanism failure. Performance experiments are separately
designed and archived only after B1/B2/B3 mechanism experiments close.

## Frozen profiles

| Profile | Classification | Formal config | Experiment ID | Seed space | Base seed | Cells | Schedulers | Paired instances | Requests |
|---|---|---|---|---|---:|---:|---|---:|---:|
| B1 bypass | Existing and retained unchanged | `configs/v9_3_ext1b1_formal_r1.yaml` | `asap-block-v9.3-ext1b1-formal-r1-workload-contract-v2` | `EXT1B1_FORMAL_R1_WORKLOAD_CONTRACT_V2` | 951201 | 6 | `gpfp_asap_block`, `gpfp_asap_nonblock` | 1200 | 2400 |
| B2 sync batch | New formal profile from the unique calibration | `configs/v9_3_ext1b2_sync_formal_r1_workload_contract_v2.yaml` | `asap-block-v9.3-ext1b2-sync-formal-r1-workload-contract-v2` | `EXT1B2_FORMAL_MECHANISM_R1_WORKLOAD_CONTRACT_V2` | 971201 | 6 | `gpfp_asap_block`, `gpfp_asap_sync` | 1200 | 2400 |
| B3 timing | New formal profile from the unique timing calibration | `configs/v9_3_ext1b3_timing_formal_r1_workload_contract_v2_capacity_contract_v1.yaml` | `asap-block-v9.3-ext1b3-timing-formal-r1-workload-contract-v2-capacity-contract-v1` | `EXT1B3_FORMAL_MECHANISM_R1_WORKLOAD_CONTRACT_V2_CAPACITY_CONTRACT_V1` | 971301 | 4 | `gpfp_asap_block`, `gpfp_alap_block`, `gpfp_st_block` | 800 | 2400 |

Every cell contains exactly 200 accepted paired instances. Every paired
instance emits its complete scheduler group or emits no requests.

## Parameter provenance

### B1

The existing `v9_3_ext1b1_formal_r1.yaml` is the authority. Its mechanism
parameters derive from `v9_3_ext1b1_energy_calibration.yaml` and were already
frozen before this PR. This PR does not change its experiment ID, seed space,
base seed 951201, output/store names, bootstrap seed, scheduler set, six-cell
grid, or any mechanism parameter. Tests compare its exact frozen identity and
verify compatibility with the three-profile validator.

### B2

The unique mechanism source is
`configs/v9_3_ext1b2_sync_calibration.yaml`, introduced by merged PR #25 and
unchanged by the subsequent control-classification repair in merged PR #30.
The evidence chain fixes:

- six cells: `U in {1/5, 2/5}` by `eta in {1/4, 1/2, 3/4}`;
- 120/120 structurally accepted paired calibration instances;
- 120/120 SYNC target mechanism activation;
- 120/120 matched BLOCK controls closed after the 12 proven prior-trajectory
  divergences were correctly classified as not applicable rather than missing;
- zero illegal partial launch, illegal transition, or unclassifiable decision;
- retained semantic traces, non-idle workload-contract-v2 inputs, and no
  calibration-parameter changes in the repair.

Formal B2 changes only status, experiment/seed/output/store identity, base seed
971201, scale 20 to 200 per cell, and preregistered bootstrap seed 9712902.
The affordable prefix `p=1`, active top-M batch `q=4`, energy interpolation,
deadline construction, supply grid, retry limit, horizon, and scheduler order
are byte-equivalent to the calibration YAML after those allowed fields are
removed.

### B3

The unique mechanism template is
`configs/v9_3_ext1b3_timing_calibration.yaml`. Its two timing cells, timing
transition semantics, scheduler order, battery capacity, workload-contract-v2
mapping, and `B3_TASK_CAPACITY_FEASIBILITY_CONTRACT_V1` are retained exactly.
The 240-request pilot at baseline ancestry completed with zero audit, hash, or
pairing failures; ASAP activated in 100%, ALAP in 98.75%, and the ST charging
cell in 97.5% of paired instances. Formal B3 changes only status,
experiment/seed/output/store identity, base seed 971301, scale 20 to 200 per
cell, and preregistered bootstrap seed 9712903.

## Shared frozen contract

All profiles freeze:

- 4 processors, 10 tasks, constrained deadlines, `C_i <= D_i <= T_i`;
- periods 40--200 ms, RM priorities, compensated WCET rounding;
- utilization tolerance `1/100`, task utilization range `1/100` to `4/5`;
- exact workload pool `bzip2`, `control`, `decrypt`, `encrypt`, `hash`;
- `REAL_TIME_TASK_WORKLOAD_CONTRACT_V2`, with `idle` reserved for system state;
- exact rational encoding, numerical mode `EXACT_RATIONAL`, 1 ms native tick;
- horizon and maximum horizon 400 ms, warmup 0, minimum two jobs per task;
- no horizon extension, semantic retained traces, trace-on-failure;
- timeout 30 seconds, one worker, retry limit 16, deterministic source index
  `logical_index * retry_limit + attempt_index`;
- `resume=false`, `fail_fast_on_p0=true`, and attempt-history preservation.

Unknown formal seed spaces and cross-profile combinations fail closed. The
profile registry freezes the full normalized scenario, scheduler order,
required outputs, experiment identity, generation/workload contract, energy,
grid, RTA, simulation, execution path identity, and statistics.

## Mechanism targets and controls

### B1: bypass

- Target: a high-priority head job is not currently affordable while a lower
  priority job is affordable.
- `ASAP-BLOCK` forbids lower-priority bypass and preserves head-job execution
  and energy priority.
- `ASAP-NONBLOCK` may legally execute the affordable lower-priority job.
- Matched controls must not contain the target bypass difference.

### B2: synchronous atomic batch payment

- Target: the complete active batch of `q=4` jobs is not affordable, while the
  priority prefix of length `p=1` is affordable.
- `ASAP-BLOCK` may launch the affordable prefix.
- `ASAP-SYNC` must wait atomically and may not partially launch the batch.
- Matched BLOCK controls use scheduler-independent predecision state; proven
  prior trajectory divergence is not applicable, while absent or inconclusive
  evidence remains fail-closed.

### B3: execution timing

- `positive-slack-energy-available`: D/T `1/2..3/4`, supply 0,
  `TOP_M_AFFORDABLE` initial energy.
- `slack-limited-charging`: D/T `3/4..1`, supply `1/2`, `HALF_TARGET` initial
  energy.
- ASAP supplies immediate execution evidence, ALAP supplies positive-slack
  deferral and urgent release, and ST supplies energy-insufficient slack wait
  followed by recovered or urgent release.

## Plan-only acceptance

Plan-only uses a fresh output root, a fresh taskset store, one fresh Release
simulator built from the exact branch commit, and `resume=false`. The simulator
file is hashed for identity but is never invoked. The read-only auditor is:

```text
scripts/audit_v9_3_ext1b_formal_plan.py
```

Each plan archive contains or references `run_config.yaml`, `run_metadata.json`,
`plan_summary.json`, `workload_contract_summary.json`,
`generation_attempts.csv`, `generated_tasksets.csv`,
`scenario_instances.csv`, `simulation_requests.csv`, `scheduler_registry.csv`,
the taskset-store pairing manifest and tasksets, `file_hashes.sha256`, a formal
plan acceptance report, and an environment manifest with the simulator SHA-256.

Required plan-only closure:

- expected cells, generated accepted tasksets, paired instances, and requests;
- `simulator_invoked=false` and zero simulator terminals/attempts;
- complete scheduler group and identical paired fairness fields;
- deterministic source-index rule and unchanged retry limit;
- workload audit `COMPLIANT`: idle, unknown, power mismatch, and legacy counts 0;
- output file-hash audit `PASS` and taskset-store pairing audit `PASS`;
- no accepted B1/B2 task whose one-tick energy exceeds effective battery
  capacity plus the native epsilon; report the exact minimum headroom;
- B3 capacity-infeasible accepted task and taskset counts both 0.

Structural rejections are permitted and are reported by stable rejection code.
Retry exhaustion, partial scheduler emission, hash/pairing/workload failure,
simulator invocation, or a capacity violation stops the freeze without any
parameter adjustment.

## Preregistered formal-run gates

Common gates after the later formal simulation:

- requested equals expected requests, terminal equals requested, complete true;
- missing and duplicate requests 0; simulation error and timeout 0;
- pairing and hash failure 0; workload audit `COMPLIANT`;
- idle, unknown workload, and workload/power mismatch 0;
- illegal transition, unclassifiable, and audit failure 0.

B1 gates:

- structural bypass condition 100%;
- NONBLOCK target-cell activation at least 95%;
- BLOCK illegal bypass 0; matched control audit pass.

B2 gates:

- structural batch interval condition 100%;
- SYNC target-cell batch stall at least 95%;
- BLOCK target-cell prefix launch at least 95%;
- illegal partial batch launch and atomic payment violation 0;
- matched control audit pass.

B3 gates:

- ASAP and ALAP target activation each at least 95%;
- ST activation in charging cells at least 95%;
- accepted capacity-infeasible tasks and tasksets 0.

No threshold, seed, retry limit, source index, cell, taskset, or mechanism
parameter may be changed after results are observed. Instances may not be
deleted, resampled for non-activation, or selected by scheduler outcome.

## Formal archive requirements

Every completed or failed formal attempt receives a distinct immutable
output/store pair. Failed attempts are retained. Each package includes the
exact Git SHA and status, config and run config, metadata, taskset store and
pairing manifest, generated tasksets, scenario instances, requests and
attempts, simulator results and task outcomes, retained traces, mechanism event
and summary tables, statistics, plot source data, workload/capacity/hash/pairing
audits, simulator SHA-256, full log, environment, exit code, package `tar.gz`,
and package SHA-256.

Pilot or calibration stores must never be resumed, copied, silently upgraded,
or used to construct formal accepted samples. Formal runs require newly
generated stores after this PR is merged and must bind the final merged master
SHA. B1/B2/B3 mechanism results remain separate from later performance data.
