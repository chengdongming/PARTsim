# Optional priority-aligned tasksets

`scripts/run_scheduler_load_cross.py` defaults to `--taskset-profile ordinary`.
That path keeps historical generation dimensions, seeds, task payloads and
identities. Existing constrained/implicit and zero/half-battery experiments
remain available.

Select `--taskset-profile priority-aligned` for a separate experimental
population. The current version is `PRIORITY_ALIGNED_TASKSETS_V2`, scoped to
4 processors and 10 synchronous tasks. Both constrained (`C<=D<=T`) and
implicit (`D=T`) deadlines support RM and DM. Initial energy is selected with
the existing `--initial-energy-rule` option: zero or half battery.

## Material definition

Each candidate starts from the existing task/workload generator. Grouping uses
the selected runtime priority order: RM sorts by period; DM sorts by deadline,
then period, then the original canonical rank. In that order:

1. Choose an anchor among the first four tasks whose workload has the highest
   energy-per-tick tier in this taskset. If several qualify, use the last one.
   Require at least one cheaper task among ranks 5–10.
2. Set the anchor's utilization to 30% of the source's total `sum(C/T)`, capped
   by the existing per-task maximum 0.8 and its unchanged deadline. Its WCET
   is rounded down to an integer.
3. Redistribute computation within the first four tasks and the remaining six.
   The desired high-group utilization is twice its source value, clamped to
   feasible bounds. This changes multiple WCETs, not only the anchor.
4. Each low-priority task retains at least 2 ticks and the original minimum
   utilization 0.01. Together they carry at least 20% of actual total `sum(C/T)`,
   with at most one CPU's utilization. Each is capped at utilization 0.35, has a
   shorter WCET than the anchor, and has greater release-time laxity `D-C`.
   All allocations obey `C<=D`; constrained deadlines are never stretched
   to make a candidate feasible.
5. Search floor/ceiling WCET choices that satisfy the **actual integer** group
   bounds and conserve total utilization within 0.0001. The original total
   target tolerance 0.01 also applies. With four processors, these quantities
   correspond to paired normalized `U_C` drift at most 0.000025 and target
   `U_C` error at most 0.0025.

Periods, deadlines, task identities within the payload, arrival offsets,
workloads and canonical storage order are retained from the accepted source
candidate. Storage remains in RM order; the simulator applies the requested
RM/DM ranking, using the same tie rules as construction.
Power is recomputed using the existing C++ workload contract after changing
WCET. Relative changes above `1e-12` fail; floating-point rounding can produce
much smaller serialization differences. Workloads are not reassigned, and
not every high-priority task is forced to be costly or every low task cheap.

Candidates are accepted using task material only, before any scheduler runs.
Ineligible candidates are retried deterministically, at most 64 attempts per
taskset. Exhaustion stops with an error and never substitutes ordinary
material. Source WCETs, accepted candidate seed, retry reasons and actual
material features are saved with each canonical taskset.

## Parameters

Only two CLI options are added: the profile selector and an optional JSON
parameter file. Defaults are defined in
`experiments/v9_3/priority_aligned.py`; a ready-to-edit example is
`configs/taskset_profiles/priority_aligned.json`.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `high_group_multiplier` | `"2"` | Desired top-four utilization multiplier under the selected policy, clamped to feasibility |
| `anchor_total_share` | `"3/10"` | Anchor share before per-task and deadline caps |
| `low_group_share_min` | `"1/5"` | Minimum actual low-group computation share |
| `low_task_util_max` | `"7/20"` | Maximum individual low-task utilization |
| `low_group_util_max` | `"1"` | Maximum low-group `sum(C/T)` |
| `min_low_wcet` | `2` | Minimum low-task WCET in ticks; values below 2 are rejected |
| `pair_total_util_tolerance` | `"1/10000"` | Maximum total utilization drift from accepted source |
| `max_attempts` | `64` | Deterministic material attempts per taskset |

Rational parameters use strings such as `"1/5"` or integer values, rather than
JSON floating-point numbers. A file may contain only the parameters being
overridden. To use the example, append:

```bash
--taskset-profile-config configs/taskset_profiles/priority_aligned.json
```

The canonical parameter set is propagated through generation, taskset rows,
`run_config.json`, run identity, summary CSVs, analysis reports and plot labels.
Different profiles/parameters cannot resume into each other's output directory.
There is no account login, credential, approval, attestation or pinned Git/SHA
requirement in this mode.

## Full UC and UE experiments

Build the simulator using the existing README instructions. From the repository
root, select the deadline family and initial energy once. This loop runs both
policies and both scans, then produces the four RM/DM × UC/UE figures:

```bash
set -o pipefail
set -e
deadline_family=constrained    # constrained or implicit
energy_rule=zero              # zero or battery_capacity/2
energy_tag=zero               # use half when energy_rule=battery_capacity/2
case "$deadline_family" in
  constrained) version=v8; campaign_prefix= ;;
  implicit) version=a-implicit; campaign_prefix=a-implicit- ;;
  *) echo "Unknown deadline family" >&2; exit 1 ;;
esac
for policy in RM DM; do
  for scan in uc ue; do
    case "$scan" in
      uc) campaign="${campaign_prefix}uc-fixed-supply" ;;
      ue) campaign="${campaign_prefix}ue-service-scaling" ;;
    esac
    run_output="/root/autodl-tmp/priority_aligned_v2_${deadline_family}_${energy_tag}_${policy}_${scan}_s20261003"
    python3 -u scripts/run_scheduler_load_cross.py \
      --output "$run_output" --seed 20261003 \
      --samples-per-cell 120 --workers 30 \
      --experiment-version "$version" --campaign "$campaign" \
      --priority-policy "$policy" --initial-energy-rule "$energy_rule" \
      --taskset-profile priority-aligned
    python3 scripts/analyze_scheduler_load_cross.py --input "$run_output"
  done
done
```

Use a fresh output directory or the existing `--resume` option with the same
scientific configuration. Choose a worker count supported by the machine.

| Campaign | Independent cells | Supply treatment | Main figure |
| --- | --- | --- | --- |
| UC | 9 `U_C` points × 3 supply levels | Fixed absolute supply within each slice; actual `U_E` varies with demand | `figure_scheduler_uc_slices.png` |
| UE | 3 `U_C` slices × 9 `U_E` points | Service-only scaling at each target `U_E` | `figure_scheduler_ue_slices.png` |

Each campaign has 27 cells × 120 tasksets × 9 schedulers = 29,160 requests,
so both policies and scans total 116,640. The existing harvest curve, battery rule,
60,000 ms horizon, nine schedulers and figure styles are unchanged. WholePass
uses the compact simulator path: it does not generate or parse full traces.
The analyzer writes `summary.csv`, figure CSVs, and the relevant PNG, with the
population, deadline mode, policy and initial energy identified in the figure
title and report. This mode reports WholePass; DMR is unavailable. Ordinary
constrained campaigns retain their existing full-analysis path.

For constrained deadlines, RM and DM construct different structured task
populations under their respective rankings. Their figures compare these
policy-specific populations, not a priority-only change on identical tasks.
For implicit deadlines, RM and DM use exactly the same canonical tasksets and
have identical ordering. The loop can rerun both policies explicitly; their
figures are equivalent-policy results on shared material, not independent
task populations. Changing zero to half initial energy also preserves the
tasksets when seed and other generation parameters are unchanged.
The legacy three-root stitcher is for its historical population and is not
needed for these complete V2 campaigns.

This population tests a proposed ASAP-BLOCK advantage. It does not guarantee
that advantage. Earlier pilot pass counts came from a different population
that allowed weaker low-priority load; those counts must not be reused here.
