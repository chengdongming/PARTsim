# ASAP-BLOCK v9.3 CORE-4 sensitivity execution

CORE-4 is a finite-sample implementation-consistency experiment. Its
monotonicity output is not a mathematical proof and must not be described as
one. The template is intentionally not a frozen paper parameter set.

## Shared production path

The runner reuses `experiments/v9_3/execution_engine.py`, the frozen
`TasksetStore`, attempt journal, atomic terminal files, checkpoint format, and
the v9.3 production dispatch in `asap_block_v9_3_runner.py`. It never invokes a
separate RTA implementation. A taskset is generated once from dimensions that
exclude the sensitivity target. E0, service values, exact power scale, and
method are then analysis-only paired transforms.

Power scales are parsed as exact positive rational numbers. C, D, T, priority,
M, base power vector, generation seed, numerical mode, and base taskset hash
remain fixed. Service configurations are full predeclared objects. Adjacent
available levels are accepted as weak-to-strong only after every required
discrete service value has been compared exactly. A name never establishes
strength.

The repository currently has one formal service configuration. Consequently,
the smoke config records its second service level as
`DEPENDENCY_UNAVAILABLE`; it does not invent a curve. This is excluded from
violation counts and remains visible in requests and denominators.

## Commands

```text
python3 scripts/run_v9_3_core4.py --config configs/v9_3_core4_smoke.yaml --dry-run
python3 scripts/run_v9_3_core4.py --config configs/v9_3_core4_smoke.yaml --list-cells
python3 scripts/run_v9_3_core4.py --config configs/v9_3_core4_smoke.yaml --max-cells 1 --max-tasksets 1
python3 scripts/run_v9_3_core4.py --config configs/v9_3_core4_smoke.yaml --resume
python3 scripts/analyze_v9_3_core4.py artifacts/v9_3_core4_smoke
```

`--max-cells` limits ordered axis levels and `--max-tasksets` limits frozen
tasksets per cell. Resume checks the semantic configuration hash. Child runs
retain append-only attempts and atomic terminal results. SIGINT/SIGTERM is
handled by the shared engine. Duplicate analysis/attempt/task rows must be
byte-equivalent or materialization fails closed.

## Directions and statuses

- Higher E0 and pointwise stronger service may not lose certification, lose an
  earlier candidate, or increase a common candidate.
- Higher exact power may not gain certification, create a candidate, or reduce
  a common candidate.
- The declared local-vs-complete pair keeps the existing LOC dominance
  direction.
- A timeout is `TIMEOUT_CENSORED`, never a violation.
- A missing formal service dependency is `DEPENDENCY_UNAVAILABLE`.
- Other non-common domains are `NOT_COMPARABLE`.
- Equal vectors are `EQUAL`; strict movement in the expected direction is
  `MONOTONICITY_HOLDS`; a regression is `MONOTONICITY_VIOLATION`.

A violation writes the base taskset, both levels, all task results, counters,
and configuration references to `failure_inputs/`, records P0, and makes the
runner return a stopped outcome.

## Outputs

The run root contains the frozen taskset table, sensitivity requests, common
analysis attempts/taskset/task results, paired results, monotonicity checks,
CSV/JSON summaries, long-form plot data, failures, checkpoint, configuration,
and SHA-256 inventory. Certification ratio and completed-only ratio retain
separate denominators. Candidate mean/median/p95 use candidate rows only.

The committed smoke uses constrained deadlines, M=4, n=10, U=0.2, one frozen
taskset, E0 0/1, exact power scale 1/2, and CW-Theta-cw/LOC-Theta-loc. It is
bounded validation evidence only; it freezes no paper parameters and supports
no paper conclusion.
