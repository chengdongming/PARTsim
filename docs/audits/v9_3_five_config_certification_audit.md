# v9.3 five-configuration task-set certification audit

## Scope and authorities

- Mathematical baseline commit: `8b1c81984b79374f94426971a7b7aae3deaa8b04`.
- Theory SHA-256: `524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e`.
- Machine-contract commit: `c0e2bac` (`experiment: align fixed-carry-in certification with v9.3`).
- Machine-contract ZIP SHA-256: `84e2103ee4963ff7b6a2dfa7330a823add8f0ba2e38275c7c23da83f2e0e1420`.
- No runner, analyzer, formal CSV, experiment parameters, default RTA selection, generator, theory document, v20.4/v21 implementation, or C++ scheduler was changed.

The implementation follows theory Sections 3.5, 9.3, 9.4, 9.5, and 10.3. A single-task closure candidate is represented by `SingleTaskSolverResult`; it contains no certification field. Task certification is produced only by the task-set finalizer.

## Contract-enum synchronization

Nine Python enum families are synchronized directly against the v1.3.10 YAML schema:

1. analysis variant;
2. analysis method role;
3. task solver status;
4. task certification status;
5. analysis solver status;
6. analysis certification status;
7. dependency-vector status;
8. dominance-invariant status;
9. fixed-carry-in interface status.

The task prompt mentioned `FixedCarryInInterfaceStatus.NOT_ACTIVE`; v1.3.10 instead defines exactly `ACTIVE`, `HASH_MISMATCH`, and `NOT_APPLICABLE`. The implementation uses the contract names. Neither `TASK_LEVEL_CERTIFIED_ONLY` nor `TASK_LEVEL_AUXILIARY` exists in the production enums.

## Five configurations

| Variant | Window | Carry-in source | Role | Joint-certificate condition |
|---|---|---|---|---|
| `CW-D` | complete | frozen `D` vector | `AUXILIARY_ABLATION` | active/matching interface and every `R_i <= D_i` |
| `LOC-D` | local | frozen `D` vector | `AUXILIARY_ABLATION` | active/matching interface and every `R_i <= D_i` |
| `CW-Theta^cw` | complete | same-run provisional CW prefix | `MAIN_METHOD` | every task has a candidate |
| `LOC-Theta^cw` | local | frozen jointly-certified CW vector | `AUXILIARY_ABLATION` | valid identical dependency domain and every local candidate is no larger than CW |
| `LOC-Theta^loc` | local | same-run provisional LOC prefix | `MAIN_METHOD` | every task has a candidate |

`MAIN_METHOD` is therefore exactly `{CW-Theta^cw, LOC-Theta^loc}`.

## State machine and atomic finalizer

Before joint finalization every successful task is `CANDIDATE_FOUND + PROVISIONAL_NOT_CERTIFIED`. The common finalizer validates:

- record count, unique task IDs, and contiguous priority ranks;
- candidate/status consistency and `C_i <= R_i <= D_i`;
- absence of any pre-certified prefix;
- recursive carry-in equality with the provisional prefix;
- deadline-vector equality for CW-D/LOC-D;
- active interface and exact theory/interface hashes for fixed carry-in variants;
- complete, jointly-certified CW source and identical immutable dependency context for LOC-Theta^cw;
- exact equality of every recorded fixed carry-in vector with the frozen vector;
- `R_i <= Gamma_i` for every fixed-carry-in candidate.

Only after all checks pass does it create a new immutable tuple in which all task records are `CERTIFIED`, then creates `COMPLETED + CERTIFIED_TASKSET`. Callers cannot construct a `CERTIFIED` task or a task-set result without the private finalizer token. A failed finalization leaves the original immutable provisional tuple unchanged.

Failure transitions are:

| Event | Failed task | Successful prefix | Remaining suffix | Analysis result |
|---|---|---|---|---|
| ordinary `NO_CANDIDATE` | `NOT_CERTIFIED` | provisional | not evaluated/not applicable | `NO_CANDIDATE + NOT_CERTIFIED` |
| timeout | `NOT_CERTIFIED` | provisional | not evaluated/not applicable | `TIMEOUT + NOT_CERTIFIED` |
| numeric error | `NOT_CERTIFIED` | provisional | not evaluated/not applicable | `NUMERIC_ERROR + NOT_CERTIFIED` |
| LOC-Theta^cw valid-domain no candidate or `R_i > Gamma_i` | `NOT_CERTIFIED` | provisional | not evaluated/not applicable | `INTERNAL_CONFORMANCE_FAILURE + DOMINANCE_INVARIANT_VIOLATION` |
| invalid formal dependency/interface | not applicable | none | not applicable | `NOT_APPLICABLE_DEPENDENCY + NOT_APPLICABLE` |
| explicit diagnostic | diagnostic-only candidates | diagnostic-only | as evaluated | never task-set proven |

## LOC-Theta^cw frozen-dependency argument

The source result and every task record are frozen dataclasses. Before solving the first local task, the orchestrator copies the complete CW candidate vector into a private fixed dictionary. Every solver call receives a fresh copy of that same dictionary. Local results are stored separately and are never written to the fixed dictionary or source object. The finalizer compares each record's recorded carry-in tuple to the complete frozen vector and rejects any feedback or mutation. Missing source entries have no fallback to deadlines, zero, or local candidates.

## Test evidence

- New task-set suite: `46 passed`; includes the 14 required LOC-Theta^cw negative boundaries and two atomicity tests.
- v9.3 mathematical core: `22 passed`.
- RTA-selected Python regression: `353 passed, 354 deselected`.
- Full Python regression: `646 passed, 62 skipped` (warnings are existing legacy-provenance warnings).
- Seeded real-core consistency: 200 task sets, 200 CW sources jointly certified, 200 LOC-Theta^cw vectors jointly certified, zero dominance violations.
- Targeted `py_compile`: passed for the v9.3 core, task-set module, and both v9.3 test files.

Full-repository `compileall` is not clean because of two pre-existing, out-of-scope files: Python-2 syntax in `tools/about.py` and mixed indentation in `tools/taskset_generator/taskgen.py:128`. CTest ran all 166 registered tests: 163 passed and the existing `Scheduler.FIFO`, `Scheduler.TrueFIFO`, and `Scheduler.RM` tests failed. No C++ code was changed.

## Remaining issues

- P0 for this implementation stage: none identified.
- P1: runner/schema serialization, runtime manifests, formal CSV generation, and CORE-0A evidence are intentionally not implemented.
- P2: the two historical `compileall` failures and three historical CTest scheduler failures remain outside this task.

The code is ready to enter the separate runner/schema integration stage. CORE-0A itself must not start until that integration produces v1.3.10-conformant runtime objects and passes its own validation gates.
