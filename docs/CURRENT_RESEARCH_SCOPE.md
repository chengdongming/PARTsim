# PARTsim current research scope

## Scientific scope

The new research uses discrete slots with offered energy available in the
same slot. Simulations allow finite storage and clip surplus after debit.
The current ASAP-BLOCK RTA is for unlimited capacity and zero background
power, with an exact any-window harvested-energy lower bound and an
every-release **pre-harvest** floor. A sufficient certificate is not an
exact WCRT; failure to certify is not proof of unschedulability.

| Research line | Supported route |
|---|---|
| Nine strategies: ASAP/ALAP/ST × BLOCK/NONBLOCK/SYNC | `scripts/run_scheduler_comparison.py`, `scripts/analyze_scheduler_load_cross.py` |
| New AB RTA | `experiments/same_slot/ab_rta.py`, `scripts/run_same_slot_rta.py`, `scripts/run_same_slot_rta_campaign.py` |
| HEARTS-1C and global/partitioned comparison | `experiments/hearts_1c/replay.py`, `scripts/run_hearts_1c_comparison.py` |

HEARTS-1C is a serial, one-dedicated-core, zero-static-power specialization
of the original paper; it is not the original parallel DAG scheduler.

## Source layout and shared dependencies

- `libmetasim/`, `librtsim/`, `rtsim/`: native simulator, schedulers, energy
  runtime, and trace contracts. No simulator semantics were changed by
  the layout cleanup.
- `experiments/scheduler_comparison/`: current entrypoint documentation,
  backed by `experiments/v9_3/scheduler_load_cross.py`.
- `experiments/same_slot/`: new RTA and paired experiment orchestration.
- `experiments/hearts_1c/`: exact paired reference replay.
- `experiments/common/`: shared-dependency map and exact service curve.
- `global_task_generator.py` and `experiments/v9_3/`: shared taskset,
  power, energy, simulator, parsing, and result contracts. Keep the module
  paths and generation identities; moving these files would change frozen
  experiment code fingerprints.
- `experiments/asapblock_unlimited/`: frozen V7.2 reproduction and
  optimized AB kernel reused directly by current RTA. Keep its original
  verifier, generator, configuration, and source manifests untouched.
- `test/`: native and Python regression tests, plus historical
  compatibility tests that continue to validate retained routes.

See [historical experiment index](HISTORICAL_EXPERIMENTS.md).
The cleanup is deliberately conservative: only files with no retained
consumers or standalone old snapshots were removed from the current
branch; dependent compatibility files remain at their original paths.
The original source tree is recoverable from
`archive/pre-cleanup-20261128` at `a5218432dccac3a3e61623b758337d99ba74a712`.

## Validation

Build with CMake and run the native test executables or CTest, then run
the three current Python test modules listed in the root README. Keep
V7.2's independent reproduction test as a frozen-input/source invariant.
For retained legacy suites, run the pytest target from the historical
audit before any further deletion. Compare identical paired workload and
energy identities, per-case statuses, bounds, selected jobs and energy
ledger values; runtime measurements need not be bit-identical.
