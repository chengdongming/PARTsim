# v9.3 EXT-1B B1 integration audit

Status: `B1_INTEGRATION_AUDITED`

This audit classifies the complete eight-commit range from current
`origin/master` (`84b3e36977d79c9ef80529ab1519c32e50d99637`, tree
`7395ea447e65505269b7508e1ed5da6a1f3c5a2a`) through the authoritative B1
freeze. The integration is selective; the freeze branch was not merged.

## Authoritative identities

- Runtime commit/tree: `efdb06bd9320d6b6693850c1f40fc8fad932b240` /
  `827809cade5d7055e10d8def4703e33151474436`
- Freeze commit/tree: `65ada72ab4e9b41a70f15f6f6b5a2b5e00102ded` /
  `1b290a086db2ccab8064095107a6f3cc21f75061`
- Annotated tag/object: `v9.3-ext1b-b1-confirm-pilot-r2` /
  `feb1f3f875f460998068f01dd6db4ac3d4af408b`
- Archive SHA-256:
  `650e2ccea76a1e2584b12d8caa6a0a34c44decb8d90044b1f09773f638c20416`
- Root-manifest SHA-256:
  `80885be80c0668b8d3c50c1a267f79da4e72e6c7a61c0bf8a397289ea5f998cf`

B1 remains `B1_CONFIRM_PILOT_PASSED`; none of these records authorize or report
a formal run.

## Commit-chain decisions

| Commit | Classification | Decision |
| --- | --- | --- |
| `c1d7692c` framework | A/C/D/E/G | Select B1/common files and minimum trace plumbing |
| `cac8844b` tests/docs | C/D/E | Keep B1/common test coverage; exclude mixed run document |
| `32bc60fe` correctness audit | C/G | Keep B1/common correctness and parser plumbing |
| `1d4a65ea` task accounting | F/G | Keep the one accounting deletion and native proof; exclude CORE test expansion |
| `c5048ff2` active-at-miss | F/G | Keep parser fix and test fixture compatibility; isolate coverage in B1 test |
| `9ead640b` B1 contract | A/B | Include all three B1 files |
| `efdb06bd` resume idempotence | A/C | Include code and tests |
| `65ada72a` R2 freeze | B | Include the two tracked freeze records only |

The machine-readable companion lists every changed file and its A-G category.

## Included scope

- B1 smoke, pilot, and confirm-pilot configurations.
- The non-executable `UNFROZEN_FORMAL_TEMPLATE`. The runner rejects both the
  template and a status-only change to `FROZEN_FOR_FORMAL_EXECUTION`.
- EXT-1B config, generation, engine, aggregation, statistics, runner, and
  analyzer modules required by B1.
- B1 contract, resume, manifest, mechanism, retained-trace, parser, and
  active-at-miss tests.
- B1 confirm contract and R2 Markdown/JSON freeze records.
- Minimum high-risk plumbing described below.

## Excluded scope

- All B2 and B3 smoke/pilot/screening/confirm/formal configuration files.
- B2 commit `5592fd36ab514fe35ebf747ae56651c2b71b800f` and screening/auditor work.
- B3 timing configuration and the B3-only harvest-clipping overflow-guard
  bypass.
- The mixed B1/B2/B3 mechanism-stress run document.
- CORE-2 aggregation-performance work and `experiments/v9_3/aggregation.py`.
- CORE-3 censoring test expansion and EXT-1A pipeline test expansion.
- External archive contents and all formal experiment execution.

## High-risk audit

### `experiments/v9_3/simulation_engine.py`

Kept only trace retention: `retain_trace: true` copies an existing semantic
trace into `retained_traces`; the prior fail-only path remains the default.
This does not change selection, dispatch, preemption, battery, release,
deadline, or completion. Excluding it breaks the B1 confirm retained-trace and
parser contract. The B3-only `allow_harvest_clipping` exception was removed, so
the common overflow guard remains fail-closed for every integrated path.

### `experiments/v9_3/simulation_result.py`

Kept mechanism-event validation/metrics for the nine schedulers and active
interval settlement at a deadline miss. It changes trace interpretation only:
an inconsistent `executed + remaining != WCET` trace now fails closed. It does
not alter native selection, dispatch, preemption, battery, release, deadline,
or completion. Excluding it breaks B1 mechanism metrics and active-at-miss
parser tests.

### `librtsim/task.cpp`

The only code deletion removes
`execdTime += (*actInstr)->getExecTime()` from `Task::onDesched`. The active
instruction already owns cumulative executed time and `Task::getExecTime()`
adds it; the deleted line counted prior slices again after every preemption.
No task is selected, dispatched, preempted, released, or charged differently.
It can correct completion/deadline outcomes for a preempted job and therefore
has a global native lifecycle surface, including any CORE run that exercises
that bug. The paired native regression proves preserved remaining WCET and the
canonical miss payload.

### Test-only high-risk files

`test/scheduler/repair_regressions.cpp` adds the native accounting and
never-scheduled-miss proofs. `test/v9_3_core3_helpers.py` only makes the existing
deadline-miss fixture satisfy `executed + remaining = WCET`; without it the
v9.3 suite fails. `test/test_v9_3_core3_censoring.py` and
`test/test_v9_3_ext1_pipeline.py` are excluded; B1-local active-at-miss tests
provide the necessary coverage without importing their unrelated expansions.

## Scientific invariants

- No RTA file, theory identity, solver, or fixed carry-in interface changed.
- No scheduler selection, dispatch, preemption, or battery rule changed.
- No CORE-1/CORE-2 config, request identity, formal authorization, taskset
  store, aggregation schema, or default runner changed.
- The shared native accounting correction can affect only previously
  double-counted preempted-job completion/deadline accounting; it is not a
  scheduler-policy modification.
- B1 remains explicit-config-only. No default entry point launches it.
- No formal authorization exists and no formal, smoke, pilot, or confirm
  experiment was run during integration.

## Validation and equivalence

- Python compile, YAML/JSON parsing, CLI help, and `git diff --check`: pass.
- Three B1 config dry-runs: pass; the no-native fixture records invocation 0.
- EXT-1B Python: 85 passed.
- All v9.3 Python: 600 passed.
- Repository Python: 1,378 passed, 63 skipped, 32 known warnings.
- Native build: pass.
- Native focused lifecycle tests: 2 passed.
- CTest: 165/168 passed. `Scheduler.FIFO`, `Scheduler.TrueFIFO`, and
  `Scheduler.RM` fail identically on a clean `origin/master` build and are P2
  baseline-only failures; all ASAP/ALAP/ST and new B1-native tests pass.
- Runtime/integration canonical science projection: equal (19,593 bytes),
  covering registry, B1 cell, generation/taskset payload, request IDs, parser
  jobs, and mechanism metrics. Path/version fields and the path-derived missing
  simulator identity were excluded.
- Fresh runtime and integration native builds: the same 11 fixed selection,
  dispatch, preemption, energy, deadline, completion, and family microcases
  pass in both.
- Runtime complete-resume suite: 10 passed; integration coverage is included in
  the 85 EXT-1B tests. Completed resume invokes native simulation zero times and
  preserves checkpoint, cell manifest, root manifest, and result bytes.

Severity: P0 none; P1 none; P2 one known baseline-only native failure set.
