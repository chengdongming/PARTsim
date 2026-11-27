# Current PARTsim research scope

The maintained research experiments use a shared energy-harvesting battery
and same-slot harvest-before-debit timing. Current ASAP-BLOCK response-time
analysis assumes unlimited storage, no separate background/static power,
an exact any-window harvest lower bound, and an every-release pre-harvest
energy floor. Failure to certify is not proof of unschedulability.

| Research line | Entry points |
|---|---|
| Nine strategies (ASAP/ALAP/ST × BLOCK/NONBLOCK/SYNC) | `scripts/run_scheduler_comparison.py`, `scripts/analyze_scheduler_load_cross.py` |
| ASAP-BLOCK RTA and paired campaigns | `scripts/run_same_slot_rta.py`, `scripts/run_same_slot_rta_campaign.py` |
| HEARTS-1C vs global AB/AN; partitioned vs global AB | `scripts/run_hearts_1c_comparison.py` |

HEARTS-1C is a sequential, one-dedicated-core specialization, not the original
parallel DAG algorithm. See each experiment's README for its model scope.

## Layout

- `libmetasim/`, `librtsim/`, `rtsim/`: native simulation and schedulers.
- `experiments/scheduler_comparison/`: current comparison instructions.
- `experiments/same_slot/`: active RTA and paired campaign implementation.
- `experiments/hearts_1c/`: paired exact-energy reference replay.
- `experiments/common/`: task generation, template resolution, energy and
  solar-data utilities.
- `experiments/pipeline/`: common simulation, task and service models,
  energy validation, taskset identities, comparison materialization, and
  result processing. Old experiment-layer version numbering is removed.
- `experiments/asapblock_unlimited/`: independent frozen V7.2 reproduction
  and solver dependencies that current RTA still calls. Preserve its
  original paths, source hashes, data and validation contracts.
- `configs/templates/`: system templates with descriptive names.
- `test/`: regression tests for maintained source, native simulator tests,
  and shared test fixtures. Full retired suites are available in archive.

The old document-dependent runtime gate is detached from the current
simulation pipeline. Historical numeric/seed identity labels remain
unchanged; they are provenance, not references to a required old document.
New experiments use the actual current source paths and hashes. Historical
runs must use their recorded source commit (see HISTORICAL_EXPERIMENTS.md).
