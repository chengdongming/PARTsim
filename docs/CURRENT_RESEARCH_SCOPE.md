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
  runtime and trace contracts.
- `experiments/scheduler_comparison/`: current entrypoint documentation,
  backed by `experiments/v9_3/scheduler_load_cross.py`.
- `experiments/same_slot/`: new RTA and paired experiment orchestration.
- `experiments/hearts_1c/`: exact paired reference replay.
- `experiments/common/`: shared task generator, energy manager, solar data
  loader, system-template resolution, and exact service curve.
- `experiments/rta/`: retained old RTA implementations and interfaces,
  still used by several compatibility and current shared modules.
- `experiments/v9_3/`: shared experiment configuration, energy generation,
  simulation, parsing, and result contracts.
- `experiments/asapblock_unlimited/`: frozen V7.2 reproduction and
  optimized AB kernel reused directly by current RTA. Keep original verifier,
  generator, configuration and source manifests untouched.
- `experiments/legacy_contracts/`: byte-exact historical theory and B4
  validation materials, not current research documentation.
- `configs/templates/`: the two native simulator system configuration
  templates. The old logical template identifiers still resolve here.
- `scripts/`: executable experiment entrypoints and the old acceptance
  ratio runner.
- `test/`: native and Python regression tests; historical validation data
  and old test-only campaign settings live in `test/fixtures/`.

See [historical experiment index](HISTORICAL_EXPERIMENTS.md). Current
root-level Python files and the two system templates were relocated,
without changing versioned filenames or algorithms. The path-only changes
require fresh source-code hashes for new experiment runs, but the previous
physical paths and source identities are preserved on
`archive/pre-root-layout-20261128` at `a5018b0affb9707b6405379e8c9a47f86c48c620`.
The earlier pre-cleanup source is on `archive/pre-cleanup-20261128`.

## Validation

Build with CMake and run the native test executables or CTest, then run
the current Python test modules listed in the root README. Keep V7.2's
independent reproduction test as a frozen-input/source invariant. For
retained legacy suites, run the pytest target from the historical audit
before any further deletion. Compare identical paired workload and
energy identities, per-case statuses, bounds, selected jobs and energy
ledger values; runtime measurements need not be bit-identical.
