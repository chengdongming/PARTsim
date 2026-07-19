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
the solver-terminal set and violation counts, remains visible in planned
requests and denominators, and is emitted as missing (not zero) in scientific
summary and plot metrics.

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
handled by the shared engine. Any duplicate analysis, attempt, terminal, or
task identity fails closed, including an otherwise byte-equivalent duplicate.

## Directions and statuses

- Higher E0 and pointwise stronger service may not lose certification, lose an
  earlier candidate, or increase a common candidate.
- Higher exact power may not gain certification, create a candidate, or reduce
  a common candidate.
- The declared local-vs-complete pair keeps the existing LOC dominance
  direction.
- A timeout is `TIMEOUT_CENSORED`, never a violation.
- A missing formal service dependency is `DEPENDENCY_UNAVAILABLE`.
- `COMPLETED` and `NO_CANDIDATE` are the only scientific terminal statuses.
- Numeric, internal, invalid, worker, and unknown statuses are
  `TECHNICAL_FAILURE`; the parent run stops before the next sensitivity level.
- The declared `NOT_APPLICABLE_DEPENDENCY` analysis outcome is
  `NOT_COMPARABLE`; it is known and non-scientific, not an internal failure.
- Other non-common scientific input domains are `NOT_COMPARABLE`.
- Equal vectors are `EQUAL`; strict movement in the expected direction is
  `MONOTONICITY_HOLDS`; a regression is `MONOTONICITY_VIOLATION`.

A violation writes the base taskset, both levels, all task results, counters,
and configuration references to `failure_inputs/`, records P0, and makes the
runner return a stopped outcome. The parent also propagates child `stopped`,
request/terminal count mismatch, child P0 failures, and technical terminals as
a top-level P0. Its checkpoint and technical failure witness are durable before
the stopped outcome is returned; no successful scientific summary is retained.

## Outputs

The run root uses `ASAP_BLOCK_V9_3_CORE4_RUN_V2` metadata and
`ASAP_BLOCK_V9_3_CORE4_CHECKPOINT_V2` checkpoints. It contains the frozen taskset table, sensitivity requests, common
analysis attempts/taskset/task results, paired results, monotonicity checks,
CSV/JSON summaries, long-form plot data, failures, checkpoint, configuration,
and SHA-256 inventory. Resume and analyzer entry points validate schemas, core,
config hash, CSV headers, exact request/result/task identity sets, ordered
levels, recomputed sweep/pair/input hashes, and single-axis invariants. A
completed run additionally requires a valid hash inventory. Empty and partial
roots cannot produce a successful summary.

A successful run first writes every scientific artifact and a `FINALIZING`
checkpoint, then atomically writes and immediately validates the exact SHA-256
inventory. Only after that succeeds does it atomically replace the checkpoint
with `COMPLETED`. The top-level `checkpoint.json` is the independent commit
marker and is therefore excluded from the immutable inventory together with
the inventory itself; both documents are mandatory and validated separately
for a completed run. Symbolic links, non-regular files, unsafe or duplicate
paths, missing/extra files, malformed digests, and hash mismatches fail closed.

Describe, outcome, checkpoint, summary, and analyzer use six separate fields:
`planned_sensitivity_row_count`, `available_solver_request_count`,
`expected_terminal_count`, `actual_terminal_count`,
`dependency_unavailable_row_count`, and `technical_failure_count`. For the
committed smoke configuration the static counts are respectively 14, 12, 12,
0 before execution, 2, and 0; a clean completed run has 12 actual terminals.
The legacy `maximum_solver_requests` field is not used.

Certification ratio and completed-only ratio retain separate denominators.
Candidate mean/median/p95 use candidate rows only. The CORE-4 configuration
contract fixes both method lists, including order, to
`CW_THETA_CW -> LOC_THETA_LOC`.

The committed smoke uses constrained deadlines, M=4, n=10, U=0.2, one frozen
taskset, E0 0/1, exact power scale 1/2, and CW-Theta-cw/LOC-Theta-loc. It is
bounded validation evidence only; it freezes no paper parameters and supports
no paper conclusion.
