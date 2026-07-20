# v9.3 extension experiments

EXT-1 is a paired, finite-horizon simulation study. A successful simulation
is an observation, not a schedulability proof. The public implementation has
four parts:

| Part | Research question | Primary comparison | Scenario |
|---|---|---|---|
| EXT-1A | How do the nine energy-aware GPFP schedulers compare on ordinary random tasksets? | all nine registered schedulers | random paired tasksets |
| B1 | When does lower-priority bypass change outcomes under energy pressure? | ASAP-BLOCK vs ASAP-NONBLOCK | `BYPASS_STRESS` |
| B2 | When does atomic payment of a new-candidate group cause waiting? | ASAP-BLOCK vs ASAP-SYNC | `SYNC_BATCH_STRESS` |
| B3 | How do ASAP, ALAP, and ST differ in start timing? | ASAP-BLOCK vs ALAP-BLOCK vs ST-BLOCK | `TIMING_STRESS` |

NONBLOCK and SYNC B3 comparisons are secondary. Every paired group fixes the
taskset, seed, releases, priorities, energy profile, initial energy, horizon,
processor count, power vector, and native build identity.

## Evidence and metrics

Common outputs include ready-but-idle time, first execution, response time,
deadline misses, timeout/internal-error status, and raw task/job/result CSVs.
B1 uses bypass events. B2 uses exact-energy batch events and
`ext1b_b2_batch_audit.py`; its atomic-wait denominator is affordable atomic
launches plus atomic waits with an affordable member. B3 uses
`b3_timing_observation` events and `ext1b_b3_timing_audit.py`, including ST
release reasons. Missing or inconsistent trace evidence fails closed.

## Configurations and commands

Bounded configurations are:

- `configs/v9_3_ext1_smoke.yaml` for EXT-1A;
- `configs/v9_3_ext1b1_{smoke,pilot}.yaml` for B1; the pilot is the only
  executable B1 batch configuration and its sample size still requires review;
- `configs/v9_3_ext1b2_{smoke,pilot}.yaml` for B2;
- `configs/v9_3_ext1b3_{smoke,pilot}.yaml` for B3.

The same non-interactive CLI is suitable for a reviewed local or remote batch
environment. This repository does not embed host credentials or remote-login
steps.

```bash
python3 scripts/run_v9_3_ext1.py --config configs/v9_3_ext1_smoke.yaml
python3 scripts/analyze_v9_3_ext1.py --output-root artifacts/v9_3_ext1_smoke

python3 scripts/run_v9_3_ext1b.py --config configs/v9_3_ext1b2_smoke.yaml --dry-run
python3 scripts/run_v9_3_ext1b.py \
  --config configs/v9_3_ext1b3_timing_calibration.yaml \
  --output-root /path/to/v9_3_ext1b3_plan \
  --taskset-store /path/to/v9_3_ext1b3_plan_store --plan-only
python3 scripts/run_v9_3_ext1b.py --config configs/v9_3_ext1b2_smoke.yaml
python3 scripts/run_v9_3_ext1b.py --config configs/v9_3_ext1b2_smoke.yaml --resume
python3 scripts/analyze_v9_3_ext1b.py --output-root artifacts/v9_3_ext1b2_smoke --verify-hashes

python3 scripts/run_v9_3_ext1b.py \
  --config configs/v9_3_ext1b1_pilot.yaml \
  --output-root /path/to/v9_3_ext1b1
python3 scripts/run_v9_3_ext1b.py \
  --config configs/v9_3_ext1b1_pilot.yaml \
  --output-root /path/to/v9_3_ext1b1 --resume
python3 scripts/analyze_v9_3_ext1b.py \
  --output-root /path/to/v9_3_ext1b1 --verify-hashes
```

Use `--max-cells` and `--max-tasksets` only for bounded validation. Pilot or
larger execution requires an explicit reviewed config and is not implied by
these examples.

`--dry-run` reports cardinalities only. `--plan-only` is the full structural
preflight: it runs taskset generation and retries, deadline transformation,
scenario construction, and request planning, then writes
`generated_tasksets.csv`, `generation_attempts.csv`,
`scenario_instances.csv`, and `simulation_requests.csv` without invoking the
simulator. B3 freezes a lexically ordered non-idle task workload pool in the
configuration and taskset provenance; `idle` remains available only as the
system idle-power state.

## Reanalysis and outputs

The output root preserves the normalized run config, seeds and request IDs,
Git commit, simulator/build hash, terminal records, raw CSVs, retained semantic
traces when enabled, B2/B3 audit CSVs, aggregate/statistical tables,
`ext1b_plot_data.csv`, and `file_hashes.sha256`. `analyze_v9_3_ext1b.py`
rebuilds derived tables from retained raw results and audit evidence.

B1 additionally writes `b1_bypass_episodes.csv`, `b1_task_effects.csv`,
`b1_paired_effects.csv`, and `b1_summary.csv`. Its primary effect direction is
NONBLOCK minus BLOCK; other scheduler results remain secondary raw evidence.
An episode opens at one blocked job's first native bypass event and remains open
across ticks with no bypass until that same job next executes. Later bypasses
only update its event count and last-bypass tick. Different jobs may have
overlapping episodes. Unresolved episodes are censored and excluded from
recovery-delay averages.
High/low task metrics are reaggregated over every eligible terminal job, whose
stable identities remain in the terminal JSON and are recorded in the task
effect table for paired validation. Paper-facing first-start delay is each
job's first-execution tick minus its release tick; absolute execution ticks
remain only in raw job evidence. Response, first-start, deadline-outcome, and
resolved-episode summaries expose their observed denominators and zero flags.

For EXT-1B1, the independent random-sample unit is the paired taskset
(`paired_instance_id`) within each U-by-eta cell. Multiple bypass episodes from
one taskset remain clustered and must not be interpreted as independent
replicates. `b1_summary.csv` recovery proportions and recovery delays are
descriptive episode summaries; inferential paired bootstrap rows in
`paired_statistics.csv` resample paired taskset effects separately within each
cell.

The frozen B1 effect direction is `delta = NONBLOCK - BLOCK`. Its primary
metrics are high- and low-priority deadline-miss deltas, high- and low-priority
first-start-delay mean deltas, resolved bypass-episode proportion, recovery
delay, and mechanism activation rate. Thus a positive high-priority delta
means NONBLOCK is worse for the high-priority task, while a negative
low-priority delta means NONBLOCK is better for the low-priority task. Total
deadline misses, ready-but-idle ticks, and high/low response means are
secondary. Taskset acceptance/pass rate and the number of episodes are not B1
primary endpoints or independent sample counts.

`simulation.deadline_miss_fail_fast` is an orchestration-level legacy name. In
the EXT-1B runner it is validated and included in the simulation configuration
identity, but it is not passed to the native simulator and does not stop a
request at its first miss. The native run continues to the configured horizon;
the trace reports `simulation_completed=true` and
`simulation_completion_reason=reached_horizon`, while the parsed terminal
status is `SIM_DEADLINE_MISS` if any job missed. This preserves later releases,
executions, bypass recovery, response/first-start observations, censoring
denominators, and the retained semantic trace.

Terminal simulation states are `SIM_PASS_OBSERVED`, `SIM_DEADLINE_MISS`,
`SIM_HORIZON_INSUFFICIENT`, `SIM_RUNTIME_TIMEOUT`, and `SIM_INTERNAL_ERROR`.
Auditors additionally expose illegal or unclassifiable mechanism evidence;
unsupported values are written as `UNAVAILABLE`, never silently converted to
zero.

Local verification:

```bash
python3 -m pytest -q test/test_v9_3_ext1_pipeline.py \
  test/test_v9_3_ext1b_mechanism_stress.py \
  test/test_v9_3_ext1b2_batch_audit.py \
  test/test_v9_3_ext1b3_timing_audit.py \
  test/test_v9_3_ext1b_observation_pipeline.py
```
