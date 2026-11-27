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

## Regression tests

```sh
PARTSIM_TEST_BINARY=build/rtsim/rtsim \
  python -m unittest discover -s test -p test_same_slot_energy.py
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

The old ablation predicates were developed under debit-before-harvest.
They are not reused as if proven for same-slot. New ablation and
RTA-to-simulation WCRT experiments need separate model-specific validation;
a difference between two upper bounds is **not** a measured WCRT error.
