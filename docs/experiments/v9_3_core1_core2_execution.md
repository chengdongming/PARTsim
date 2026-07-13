# ASAP-BLOCK v9.3 CORE-1 / CORE-2 execution guide

This document covers the production experiment framework only. The template
grid is not a frozen paper grid, and the recorded runs are smoke tests rather
than formal large-scale experiments.

## Audited production interfaces

- Production analysis entry: `asap_block_rta_v9_3_taskset.analyze_taskset_v9_3()`.
- Version-explicit dispatch: `asap_block_v9_3_runner.dispatch_rta_version()` with
  `V93_DISPATCH_VERSION`; it does not fall back to v20.4/v21.
- Five-configuration definition: `asap_block_v9_3_runner.VARIANT_ORDER`.
- Current v1.3.12 serializer:
  `asap_block_v9_3_runner.serialize_taskset_analysis_v1_3_12()`.
- Task generator: `global_task_generator.py`.
- Existing generator deadline support: implicit `D=T`, or
  `--constrained-deadlines`, whose existing semantics draw an integer deadline
  satisfying `C <= D <= T` (uniform over the generator's feasible integer
  range). It has no API for a caller-defined D/T value set or clipped D/T
  interval.
- Exact theorem energy: the v9.3 core accepts `int`, `Decimal`, rational/decimal
  strings, and `Fraction`. The experiment config deliberately rejects binary
  floats for E0 and stores canonical numerator/denominator text.

Reusable Pilot mechanisms were the isolated process timeout pattern, exact
service-curve construction, production dispatch adapter, result consistency
checks, and source-vector hashing. The formal framework does not import Pilot
result files and does not change solver, envelope, carry-in, fixed-point, or
certification code.

## Layout and responsibilities

- `experiments/v9_3/config.py`: schema normalization, exact E0, and fail-closed
  validation.
- `experiments/v9_3/cell_model.py`: formal cell, generation, seed, request, and
  analysis identities using domain-separated SHA-256.
- `experiments/v9_3/taskset_store.py`: generate-once canonical taskset JSON and
  verification. E0, variant, timeout, experiment CORE, and retry do not enter
  the generator seed. CORE-1 and CORE-2 can point to one shared store.
- `experiments/v9_3/execution_engine.py`: variant ordering, isolated workers,
  timeout/retry, source dependency, signal handling, checkpoint, and resume.
- `experiments/v9_3/result_writer.py`: fsynced append-only attempt CSV, atomic
  terminal JSON, CSV materialization, and SHA-256 manifest.
- `experiments/v9_3/validation.py`: taskset proof equivalence, certified
  candidate bounds, complete/unique task vector, dependency, and dominance P0
  checks.
- `experiments/v9_3/aggregation.py`, `tightness.py`, and `plotting_data.py`:
  read-only statistics and plot-data generation. They never call a solver.
- `scripts/run_v9_3_core1.py` and `run_v9_3_core2.py`: thin execution wrappers.
- `scripts/analyze_v9_3_core1.py` and `analyze_v9_3_core2.py`: rebuild summaries
  from persisted CSVs.
- `scripts/plot_v9_3_core1.py` and `plot_v9_3_core2.py`: read only the exported
  plot-data CSV plus plot labels from the run config and write PNG/PDF.

CORE-1 always runs `CW_THETA_CW`, then `LOC_THETA_LOC`. CORE-2 always runs
`CW_D`, `LOC_D`, `CW_THETA_CW`, `LOC_THETA_CW`, then `LOC_THETA_LOC` for each
paired taskset. `LOC_THETA_CW` receives only that taskset/E0/numerical-mode's
completed CW source. If it is not jointly certified, production analysis emits
`NOT_APPLICABLE_DEPENDENCY` / `NOT_APPLICABLE`; no fallback is available.

## Configuration and constrained deadlines

Use `configs/v9_3_core1_template.yaml` or
`configs/v9_3_core2_template.yaml` as a starting point. They are templates, not
parameter recommendations or frozen formal settings.

The constrained-deadline block always exists. At present the only executable
mode is:

```yaml
deadline_mode: constrained
constrained_deadline:
  d_over_t_values: []
  d_over_t_min: "0"
  d_over_t_max: "1"
  distribution: generator_uniform_integer
```

The `0..1` bounds denote "do not clip the generator's feasible range"; every
materialized task is still checked against `C <= D <= T`. Supplying explicit
D/T values or a narrower range is rejected because the current generator
cannot implement it without introducing new mathematical generation semantics.
Every generated-taskset row records the actual min/max and complete D/T vector.

Exact E0 examples:

```yaml
initial_energy_values:
  - 0
  - "1.25"
  - "178487996829/2000000000000"
```

These normalize to `0`, `5/4`, and
`178487996829/2000000000000`. Ordinary YAML/Python floats are rejected for E0.

## Execution

Inspect a run without creating tasksets or invoking the solver:

```bash
python3 scripts/run_v9_3_core1.py \
  --config configs/v9_3_core1_smoke.yaml --dry-run
python3 scripts/run_v9_3_core2.py \
  --config configs/v9_3_core2_smoke.yaml --list-cells
```

Run or resume a bounded smoke:

```bash
python3 scripts/run_v9_3_core1.py \
  --config configs/v9_3_core1_smoke.yaml
python3 scripts/run_v9_3_core2.py \
  --config configs/v9_3_core2_smoke.yaml
python3 scripts/run_v9_3_core2.py \
  --config configs/v9_3_core2_smoke.yaml --resume
```

`--max-cells` and `--max-tasksets` restrict an invocation without changing the
configured identities. A run directory with a different config hash is never
continued. Complete terminal results are skipped; attempts survive interruption
and retry attempts explicitly reference their parent. `SIGINT`/`SIGTERM` stop
new requests, allow the active isolated request to finish its boundary, and
write a final checkpoint.

Initial timeout and optional retry timeout are configuration values. Only a
terminal `TIMEOUT` is eligible for retry. `NO_CANDIDATE`, dependency N/A,
numeric failure, and completed requests are not retried. Timeout remains a
separate solver status in every table and is excluded from completed-only
denominators; it is never converted to a negative schedulability sample.

## Outputs and statistical domains

Each run contains the required config/metadata, cells, frozen-taskset index,
requests, attempts, taskset/task results, dependency/dominance/failure tables,
checkpoint, summary JSON/CSV, and file hash manifest. Internal atomic terminal
JSON and pickled analyzer state support idempotent recovery. The shared taskset
store contains canonical taskset JSON before analysis starts.

CORE-1 exports method, tightness, certification, runtime, and plot-data CSVs.
Its summary keeps these denominators distinct:

1. unconditional: every requested taskset/configuration;
2. completed-only: excludes timeout, numeric error, and internal failure;
3. common-candidate: tasks for which both compared variants returned a legal
   candidate.

CORE-2 exports five-variant statistics (including candidate counts, first
failure, checked w/h/q, envelope calls, runtime, timeout), task/taskset
ablations, dependency applicability, and dominance summaries. Dominance is
checked only for the three specified local-versus-complete relations. The two
carry-in ablations are empirical comparisons and do not assert dominance.

Plot-data generation is a pure projection of persisted CSV data. Rendering is
optional and cannot change a statistic or re-run analysis:

```bash
python3 scripts/plot_v9_3_core1.py artifacts/v9_3_core1_smoke
python3 scripts/plot_v9_3_core2.py artifacts/v9_3_core2_smoke
```

## Safety boundary

No CORE-0A evidence ZIP, authoritative pointer, acceptance report, or build
identity wrapper is produced. The framework does not modify v9.3 theory,
candidate search, envelopes, carry-in semantics, certification state machine,
service-curve mathematics, v1.3.12 contracts, C++ schedulers, v20.4/v21, or any
committed Pilot artifact.
