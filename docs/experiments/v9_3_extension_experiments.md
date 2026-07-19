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
- `configs/v9_3_ext1b1_{smoke,pilot}.yaml` for B1;
- `configs/v9_3_ext1b1_official_candidate.yaml` is an executable B1 batch
  candidate copied from the pilot settings; its sample size is not a final
  paper parameter and requires user approval;
- `configs/v9_3_ext1b2_{smoke,pilot}.yaml` for B2;
- `configs/v9_3_ext1b3_{smoke,pilot}.yaml` for B3.

The same non-interactive CLI is suitable for a reviewed local or remote batch
environment. This repository does not embed host credentials or remote-login
steps.

```bash
python3 scripts/run_v9_3_ext1.py --config configs/v9_3_ext1_smoke.yaml
python3 scripts/analyze_v9_3_ext1.py --output-root artifacts/v9_3_ext1_smoke

python3 scripts/run_v9_3_ext1b.py --config configs/v9_3_ext1b2_smoke.yaml --dry-run
python3 scripts/run_v9_3_ext1b.py --config configs/v9_3_ext1b2_smoke.yaml
python3 scripts/run_v9_3_ext1b.py --config configs/v9_3_ext1b2_smoke.yaml --resume
python3 scripts/analyze_v9_3_ext1b.py --output-root artifacts/v9_3_ext1b2_smoke --verify-hashes

python3 scripts/run_v9_3_ext1b.py \
  --config configs/v9_3_ext1b1_official_candidate.yaml \
  --output-root /path/to/v9_3_ext1b1
python3 scripts/run_v9_3_ext1b.py \
  --config configs/v9_3_ext1b1_official_candidate.yaml \
  --output-root /path/to/v9_3_ext1b1 --resume
python3 scripts/analyze_v9_3_ext1b.py \
  --output-root /path/to/v9_3_ext1b1 --verify-hashes
```

Use `--max-cells` and `--max-tasksets` only for bounded validation. Pilot or
larger execution requires an explicit reviewed config and is not implied by
these examples.

## Reanalysis and outputs

The output root preserves the normalized run config, seeds and request IDs,
Git commit, simulator/build hash, terminal records, raw CSVs, retained semantic
traces when enabled, B2/B3 audit CSVs, aggregate/statistical tables,
`ext1b_plot_data.csv`, and `file_hashes.sha256`. `analyze_v9_3_ext1b.py`
rebuilds derived tables from retained raw results and audit evidence.

B1 additionally writes `b1_bypass_episodes.csv`, `b1_task_effects.csv`,
`b1_paired_effects.csv`, and `b1_summary.csv`. Its primary effect direction is
NONBLOCK minus BLOCK; other scheduler results remain secondary raw evidence.
An episode merges consecutive native bypass events for one blocked job without
crossing an execution boundary. Recovery is that job's first later execution;
unresolved episodes are censored and excluded from recovery-delay averages.
High/low task metrics are reaggregated over every eligible terminal job, whose
stable identities remain in the terminal JSON and are recorded in the task
effect table for paired validation.

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
