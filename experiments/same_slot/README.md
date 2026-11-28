# Harvest and use energy in the same slot

This is a **new model**, not a relabelling of the historical results.
In slot `[t,t+1)`, let `E(t)` be stored energy before harvest, `H(t)`
the energy offered during that slot, and `P(t)` the selected work's cost.

* Available execution budget: `A(t) = E(t) + H(t)`.
* A selection is affordable when `P(t) <= A(t)`.
* Stored balance: `E(t+1) = min(B, A(t) - P(t))` (finite storage).
* Overflow is discarded **after** debit, not before. The provisional
  execution budget need not fit inside the battery.
* Only slots 0 through H-1 are credited during a run of duration H.

All nine existing scheduler families keep their native priority, timing
and selection policies. They share the new settlement path. Simulation
metadata, per-slot accounting, request IDs and RTA certificate hashes
identify the new timing. The old model is still the default for historical
commands. Static power, task energy parameters, priority policy and
historical archived results are unchanged.

## Simulation

Build the simulator as described in the root README, then explicitly opt in:

```sh
build/rtsim/rtsim system.yaml taskset.yaml 1000 -t trace.json \
  --semantic-traces --energy-timing same-slot \
  --run-id same-slot-run --taskset-semantic-hash "$TASKSET_HASH"
```

Use the canonical taskset hash from the experiment input manifest for
`$TASKSET_HASH`.

The JSON trace has `energy_timing=HARVEST_BEFORE_DEBIT` and a
`same_slot_energy_summary` ledger. Its stored balance/minimum/maximum are
actual boundary balances. Event-level `current_energy_mJ` can instead be a
provisional decision budget. The ASAP-BLOCK release-cutoff trace records
pre-harvest release floors, not the available post-harvest budget.

To use the existing paired task-set generator and nine-strategy sweep:

```sh
python scripts/run_scheduler_load_cross.py --output runs/same-slot-smoke \
  --seed 20261126 --campaign v6 --cells 1/5:2/5 --samples-per-cell 1 \
  --simulation-horizon 1000 --initial-energy-rule zero --energy-timing same-slot
python scripts/analyze_scheduler_load_cross.py --input runs/same-slot-smoke
```

For the canonical A-implicit grid use the existing formal campaign
arguments with `--energy-timing same-slot` and a fresh output directory.
It uses the trace path rather than the historical compact fast observer.
The analysis publishes `same_slot_summary.csv` (paired whole-taskset pass
ratios and Wilson intervals) and separate same-slot PNGs using Matplotlib.
The filenames identify the scan axis and deadline mode, and the plot titles
identify the priority policy and initial energy. See the
[nine-strategy comparison guide](../scheduler_comparison/README.md).
The old B4 schema3 and fast observer are intentionally not accepted in
same-slot mode until their own result contracts are extended.

## RTA (unlimited capacity, zero background)

### Existing AB implementation

`ab_rta.py` reuses V7.2's accounting, workload envelope and joint
fixed-point machinery. The new origin witness uses `e0+beta[b+1]`;
positive-origin witnesses still use `beta[ell]`. The retained-progress
test uses current-slot `beta[1]` even at release, and the first charging
gap searches `e0+beta[ell+1]`. No beta beyond the deadline/window is
needed; the old debit-before-harvest fallback is disabled. The analytic
and flow variants are both available, as is certificate replay.

Input is a JSON document `{"cases":[{"name":...,"model":{...}}]}` with
the generator's exact scaled integers, task order and beta table, and
explicit `harvest_order=HARVEST_BEFORE_DEBIT`,
`release_floor_stage=PRE_HARVEST`, `capacity=null`,
`background_power=0`. `release_floor` means minimum stored energy before
harvest at **every** release, not just initial battery. Non-certification
does not establish unschedulability.

```sh
python scripts/run_same_slot_rta.py --input same_slot_cases.json \
  --output runs/rta-same-slot --bound analytic
```

Existing generated V7.2 hypothetical inputs can be explicitly reused with
`--reinterpret-generated-floor`. Do not use that flag for measured old
release energies. Optional `--translated-seq` compares a *conservative
translated* SEQ baseline using `A(t)=E(t)+H(t)` as its balance: the same
raw any-window beta contract applies to the shifted harvest sequence and
the every-release floor is at least `e0+beta[1]`. It is not a newly
derived native same-slot SEQ. All frozen V7.2 inputs, code and reproduction
hashes remain intact; the new runner does not reproduce the old full
ablation/timing campaign. The older v9.3 proof-oriented RTA pipelines
remain historical, not silently reinterpreted.

### Integrated AN and unified formal method names

The three AN analyses follow the reviewed integer-window joint F/R theory.
`an_rta_bounds.py` contains the complete integer pattern configuration
and the two analytical upper bounds. `an_rta.py` validates the same input
model as AB, scans all integer candidate windows, and independently
replays each fixed-point certificate. The labels below are used in
new experiment results; historical AB command-line options remain valid.

| Formal method | Solver | Auxiliary condition |
|---|---|---|
| RTA-AB-OPT | Existing `flow` exact region configuration | Unchanged AB |
| RTA-AB-ANA | Existing `analytic` relaxation | Unchanged AB |
| RTA-AB-SEQ | Existing conservative translated stage sequence | Unchanged AB |
| RTA-AN-OPT | Exact integer E-slot patterns and region counts (former PAT) | Frequency-aware |
| RTA-AN-ANA | Frequency-aware analytical bound (former PRICE) | Frequency-aware |
| RTA-AN-MR | Merged-region analytical baseline (former FAST) | Base |

The exact optimization concerns an energy-allocation relaxation, not
the true system's exact worst-case response time. OPT does not discard
legal configurations. A protective DP state/option or subset enumeration
limit (200,000) raises `RESOURCE_LIMIT`; a wall-clock worker limit produces
`TIMEOUT`. Both are unresolved, not a proof of non-certification or a
deadline miss. All three AN methods use full integer q..D searches;
internal concavity search does not skip outer windows. Caches are
bounded and dropped between cases and before certificate replay.

Use the formal names with the same explicit-case runner, optionally
repeating `--method` to request several methods on identical inputs:

```sh
python scripts/run_same_slot_rta.py \
  --input experiments/same_slot/examples/an_cases.json \
  --output runs/an-explicit-smoke --timeout 30 \
  --method RTA-AN-OPT --method RTA-AN-ANA --method RTA-AN-MR
```

The new mode records solver status, input/model identity, F/R bounds,
certificate and verification, and solver CPU/wall time, with one bounded
worker per taskset and method. The exact historical code labels and solver
logic of AB are unchanged. `--an-gap base` fixes the AN auxiliary
condition to the same base upper bound, allowing the main frequency-aware
constraint to be compared without conflating auxiliary improvements.

### Exact finite replay and native simulator audit

`an_replay.py` independently runs the integer scheduling rule on an
explicit finite harvest trace and legal release lists. It checks every
contained beta window and each release's guaranteed pre-harvest balance,
stops at the first actual deadline miss, records its boundary and remaining
execution, and observes first-unit completion and response times. Given a
verified AN certificate it checks every reached F/R boundary (including
unfinished jobs), and reports the ratio of the bound to the observed
maximum response per task where a completion exists. Observed maxima are
not exact WCRT.

```sh
python scripts/run_same_slot_an_replay.py \
  --input experiments/same_slot/examples/an_replay.json \
  --output runs/an-replay-smoke --native-binary build/rtsim/rtsim
```

Inputs may contain explicit sporadic `releases` of the form
`releases[task]=[[release,actual_execution_units],...]`, or periodic WCET
`offsets` and an exclusive `release_horizon`. Energy is given as scaled
integers; one energy unit is 1/energy_scale J for native projection.
An optional `certificate` is the actual AN certificate object. Native
projection currently audits passing finite periodic WCET trajectories
with explicit input-order priorities. It uses a sampled electrical-power
trace, per-task energy factors and a finite battery with capacity above
initial balance plus all offered harvest. A native audit checks
every nonempty-ready decision's exact job identities/order, selected
units, remaining execution and observed budget/cost, arrival and completion
boundaries, and the total no-clipping energy ledger. Idle slots have no
native decision event; their balances are reconstructed from the input
trace and checked against subsequent observed budgets and final totals.
It retains exact reference, input, native trace and binary hashes.

The existing C++ scheduler still uses binary64 and a decision epsilon.
Numeric comparison tolerance is bounded below half an integer energy
unit; exact selected job identities must match. This audit verifies a
specified finite trace, not general floating-point or unlimited-capacity
equivalence. Reference misses are recorded, not misrepresented as a
complete native audit. Positive floors are checked per release; arbitrary
finite battery runs or an invalid trace cannot use the theoretical
certificate automatically. No native scheduler behavior is modified.

## Regression tests

```sh
PARTSIM_TEST_BINARY=build/rtsim/rtsim \
  python -m unittest discover -s test -p test_same_slot_energy.py
PARTSIM_TEST_BINARY=build/rtsim/rtsim \
  python -m unittest discover -s test -p test_an_rta_same_slot.py
```

Includes analytic/flow checks, finite beta boundary cases, refusal of
wrong-model certificates and an all-nine-scheduler finite-capacity
regression. The C++ harvest-runtime unit tests also cover time zero,
overflow after debit and no extra harvest at the horizon.

## Paired formal-campaign infrastructure

The new `scripts/run_same_slot_rta_campaign.py` prepares the original
**hypothetical synthetic inputs**, checks every frozen cohort fingerprint,
then creates fresh same-slot model and case identities. No measured release
floor or historical result is reinterpreted. Each individual method runs in
a separate bounded subprocess; certificates are replayed by the existing
solver. Results, input hashes, source hashes, paired statuses, bounds, and
completed-only timing statistics are persisted. A timeout is unresolved,
not non-certification. A technical error does not produce a COMPLETE marker.

```sh
python scripts/run_same_slot_rta_campaign.py prepare --output runs/rta-inputs
python scripts/run_same_slot_rta_campaign.py plan --source-dir runs/rta-inputs --suite coverage
python scripts/run_same_slot_rta_campaign.py run --source-dir runs/rta-inputs \
  --suite constructed --limit 3 --output runs/current-rta-smoke
python scripts/run_same_slot_rta_campaign.py run --source-dir runs/rta-inputs \
  --suite coverage --workers 4 --output runs/current-rta-coverage
```

| Suite | Full input count | Paired methods |
|---|---:|---|
| coverage | 1920 | same-slot analytic, conservative translated SEQ |
| sensitivity | 720 | same-slot analytic, conservative translated SEQ |
| flow | 576 | same-slot analytic, same-slot flow |
| scaling | 390 | same-slot analytic, conservative translated SEQ (serial only) |
| constructed | 180 | same-slot analytic, same-slot flow |

These are reproducible full-cohort plans for new runs, not yet full new
experimental results. A run with `--limit` is explicitly a bounded check.
Resume requires the same source, code, budget and configuration. Complete
outputs include `config.json`, `inputs.json`, `results.jsonl`,
`summary.csv`, `paired.csv` and `COMPLETE.json`. Common-certified R/D
statistics use paired inputs only; paired completed CPU timings omit
timeouts and errors. The flow suite reports disagreements rather than
assuming global equivalence.

### New AN and six-method paired suites

All new suites reuse the verified hypothetical cohorts. They do not
import old results, and do not claim the hypothetical release floor holds
on an arbitrary native trajectory.

| Suffix | Cohort | Full input count |
|---|---|---:|
| coverage | main | 1920 |
| sensitivity | latest | 720 |
| reference | flow | 576 |
| scaling | scale_all | 390 |
| constructed | constructed | 180 |

Prefix a suffix with `an_` for all three AN methods, or `joint_` for all
six AB/AN methods. Scaling suites require serial measurements. The
`an_frequency` suite compares RTA-AN-ANA and RTA-AN-MR on the main cohort
while fixing the base auxiliary condition. A complete six-method result
has 15 pair comparisons per cell; the summary marks same-scheduler
versus cross-scheduler pairs, so a cross-policy bound difference is not
misinterpreted as pure RTA tightness.

```sh
python scripts/run_same_slot_rta_campaign.py plan --source-dir runs/rta-inputs \
  --suite joint_coverage
python scripts/run_same_slot_rta_campaign.py run --source-dir runs/rta-inputs \
  --suite joint_constructed --limit 3 --timeout 30 --workers 2 \
  --output runs/joint-smoke
python scripts/run_same_slot_rta_campaign.py run --source-dir runs/rta-inputs \
  --suite an_coverage --timeout 30 --workers 4 --output runs/an-coverage
```

New manifests include the full-window contract, canonical method names,
AN auxiliary setting and source hashes. The summary separates certified,
completed without certificate, timeout, resource limit, technical error
and missing requests; the certification fraction uses all planned
inputs. Pair timing omits unresolved requests. The formal full cohorts
and large-scale native observations still need to be executed; the
bounded checks are integration tests rather than publication results.

### Target four-core/ten-task study (new, separate task family)

The old frozen cohorts do not contain 4-core/10-task inputs. A separate
`prepare-fourcore10` command builds them using the maintained UUniFast
sampler and compensated integer WCET generation, strict RM order,
D=ceil(0.9T), zero every-release floor and unlimited storage. The
same CPU skeleton and workload weights are reused across all UE levels.
UC is sum(C/T)/4 and UE is mean task energy demand divided by mean
harvest. Each target UE is achieved by exact rational scaling, with a
recorded common integer denominator. Input generation is independent of
any solver or simulation outcome, and the loaded cohort is regenerated
and checked before planning. Defaults are period 40–200, UC 0.1–0.9,
UE 0.4/0.6/0.8, mean harvest 3, constant supply (gap 1); all are explicit
configuration parameters except core count, task count and deadline
ratio. The sample count is required rather than starting large runs
implicitly.

```sh
python scripts/run_same_slot_rta_campaign.py prepare-fourcore10 \
  --output runs/rta-4c10-inputs --samples-per-cell 120 --seed 20261128
python scripts/run_same_slot_rta_campaign.py plan \
  --source-dir runs/rta-4c10-inputs --suite joint_fourcore10
python scripts/run_same_slot_rta_campaign.py run \
  --source-dir runs/rta-4c10-inputs --suite joint_fourcore10 \
  --limit 1 --timeout 30 --workers 2 --output runs/4c10-smoke
```

Use `an_fourcore10` for the three AN methods and
`an_frequency_fourcore10` for the controlled frequency comparison.
Omit `--limit` only to run the entire configured cohort. These are newly
generated hypothetical analysis inputs; there are no full formal results
yet. Native trace auditing requires independently valid finite harvest
and release sequences; the generator does not assume a native trace
satisfies its beta or pre-harvest floor.

The old ablation predicates were developed under debit-before-harvest.
They are not reused as if proven for same-slot. The new controlled
AN frequency comparison and finite replay do not replace large-scale
RTA-to-simulation validation;
a difference between two upper bounds is **not** a measured WCRT error.
