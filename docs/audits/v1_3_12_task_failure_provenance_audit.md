# ASAP-BLOCK v1.3.12 structured task-failure provenance audit

Date: 2026-07-13

Baseline: v1.3.11 (`0f64101c0d9baf8885197d34e124a9a528eee0ad`)

Scope: machine contract only; no runner, RTA, task-set state-machine, theory, generator, or C++ changes.

## Finding

`TaskAnalysisRecord.failure_reason: Optional[str]` is not represented by the v1.3.11 `per_task_results.csv` interface. Two formal task records that differ only in their failure provenance therefore collapse to the same canonical row and `task_result_hash`. The loss occurs at the runner/schema boundary; it is not a v9.3 mathematical defect.

The raw Python string cannot itself be made formal. The core deliberately uses `str(exc)` for caught operational exceptions, so an injected envelope, service curve, or clock can supply paths, addresses, timestamps, `repr` output, locale- or Python-version-dependent text, and arbitrary floating-point formatting. v1.3.12 must map audited producer context to a stable code and an approved canonical detail, and must reject unknown raw values fail-closed.

## Complete production-domain audit

| Code location | Solver status | Certification status at task level | Current `failure_reason` | Deterministic | Dynamic-data risk | v1.3.12 code |
|---|---|---|---|---|---|---|
| `asap_block_rta_v9_3.py:613-618`; adapter `:315-342` | `CANDIDATE_FOUND` | provisional, diagnostic-only, or finalized `CERTIFIED` | `None` | yes | none | `NONE` |
| `asap_block_rta_v9_3.py:632-636`; adapter `:317-342` | `NO_CANDIDATE` | `NOT_CERTIFIED` | `no v9.3 closure candidate by the task deadline` | yes | none | `NO_CANDIDATE` |
| `asap_block_rta_v9_3.py:547-552,568-608`; adapter `:319-342` | `TIMEOUT` | `NOT_CERTIFIED` | `v9.3 closure search timed out` | yes | none | `SOLVER_TIMEOUT` |
| `asap_block_rta_v9_3.py:619-622`; adapter `:319-342` | `TIMEOUT` | `NOT_CERTIFIED` | `str(TimeoutError)` | no | arbitrary exception text, including all listed dynamic forms | `SOLVER_TIMEOUT`, only with approved caught-timeout context |
| `asap_block_rta_v9_3.py:623-626`; adapter `:321-342` | `NUMERIC_ERROR` | `NOT_CERTIFIED` | `str(OverflowError)` | no | arbitrary exception text and Python-version wording | `NUMERIC_ERROR`, only with approved caught-overflow context |
| `asap_block_rta_v9_3.py:627-630`; adapter `:321-342` | `NUMERIC_ERROR` | `NOT_CERTIFIED` | `str(V93NumericError/ArithmeticError)` | partly | built-in guards are stable, but injected callables may raise arbitrary `ArithmeticError` text | `NUMERIC_ERROR`, only with approved numeric-exception context |
| `asap_block_rta_v9_3_taskset.py:326-342` | `INTERNAL_CONFORMANCE_FAILURE` | `NOT_CERTIFIED` | propagated core reason, normally `None` for an unknown enum object | context-stable, text not authoritative | unknown object/reason may be dynamic | `UNKNOWN_CORE_STATUS` |
| `asap_block_rta_v9_3_taskset.py:370-380` | `NOT_EVALUATED_AFTER_PREFIX_FAILURE` | `NOT_APPLICABLE` | `not evaluated after prefix failure` | yes | none | `UPSTREAM_PREFIX_FAILURE` |
| `asap_block_rta_v9_3_taskset.py:383-393,702-718` | `NOT_APPLICABLE_DEPENDENCY` | `NOT_APPLICABLE` | `fixed carry-in dependency is not applicable` | yes | none | `DEPENDENCY_NOT_APPLICABLE` |
| `asap_block_rta_v9_3_taskset.py:750-774` candidate exceeds compatibility limit | `CANDIDATE_FOUND` | rewritten to `NOT_CERTIFIED` | normally `None` | structured counterexample is deterministic | raw reason has no dominance semantics | `DOMINANCE_INVARIANT_VIOLATION` |
| `asap_block_rta_v9_3_taskset.py:755-774` valid-domain LOC-Theta-cw no-candidate | `NO_CANDIDATE` | `NOT_CERTIFIED` | core no-candidate literal | yes, but misleading alone | none | `DOMINANCE_INVARIANT_VIOLATION` |
| `asap_block_rta_v9_3_taskset.py:781-784` | `INTERNAL_CONFORMANCE_FAILURE` or an unrecognized task-solver status | `NOT_CERTIFIED` | solver-supplied optional string | no | arbitrary custom-solver text | `INTERNAL_CONFORMANCE_FAILURE` unless the adapter proved `UNKNOWN_CORE_STATUS` |
| `asap_block_rta_v9_3_taskset.py:739-746,825-840` diagnostic mode | any evaluated status | candidate becomes `DIAGNOSTIC_ONLY_NOT_CERTIFIED`; failures remain not certified | unchanged from the underlying solver | unchanged | unchanged | underlying code; diagnostic mode is certification metadata, not a failure code |
| input/dependency checks that raise `CertificationError`, including `:632-699` | no `TaskAnalysisRecord` | no task certification status | exception escapes | message may be stable, but no task record exists | caller/infrastructure may add dynamic context | no task code; execution-level failure |
| uncaught infrastructure exception from `single_task_solver`, serializer, or I/O | no completed `TaskAnalysisRecord` for that call | none | exception escapes | no | arbitrary exception/debug text | no task code; execution-level failure |

The core's built-in numeric guards include stable literals and parameterized validation messages, but the catch clauses intentionally accept injected `TimeoutError`, `OverflowError`, and `ArithmeticError`. Consequently, no regex over `raw_failure_reason` is sufficient evidence of formal provenance. The normalizer must also receive an approved structured origin from the adapter.

The test-only scripted solver at `test/test_asap_block_rta_v9_3_taskset.py:70` uses `status.value` as arbitrary raw text. This confirms that the protocol admits noncanonical strings; it is not an additional production category.

## Frozen formal categories

The audited categories required in `task_failure_reason_code` are:

1. `NONE`
2. `NO_CANDIDATE`
3. `SOLVER_TIMEOUT`
4. `NUMERIC_ERROR`
5. `UPSTREAM_PREFIX_FAILURE`
6. `DEPENDENCY_NOT_APPLICABLE`
7. `DOMINANCE_INVARIANT_VIOLATION`
8. `UNKNOWN_CORE_STATUS`
9. `INTERNAL_CONFORMANCE_FAILURE`

`DEPENDENCY_INVALID` is not a task outcome: an invalid fixed-vector dependency produces `NOT_APPLICABLE_DEPENDENCY` task rows while the dependency table carries `INVALID`. `INPUT_VALIDATION_FAILURE` and `INFRASTRUCTURE_EXCEPTION` occur before/no task record and remain execution-level. `DIAGNOSTIC_ONLY` is already a certification state. Adding any of those as task failure codes would conflate separate state machines.

## Canonical detail policy

The code is the primary formal semantic. Detail is not free text; each code has exactly one frozen null or literal value:

| Code | Required canonical detail |
|---|---|
| `NONE` | null |
| `NO_CANDIDATE` | `closure exhausted through task deadline` |
| `SOLVER_TIMEOUT` | null |
| `NUMERIC_ERROR` | `numeric guard rejected analysis` |
| `UPSTREAM_PREFIX_FAILURE` | null |
| `DEPENDENCY_NOT_APPLICABLE` | null |
| `DOMINANCE_INVARIANT_VIOLATION` | `local result violated frozen carry-in dominance` |
| `UNKNOWN_CORE_STATUS` | `unrecognized core solver status` |
| `INTERNAL_CONFORMANCE_FAILURE` | `internal analyzer conformance failure` |

Non-null detail is UTF-8 NFC, LF-only, 1--256 Unicode scalar values, and may contain neither NUL nor CR. Absolute POSIX/Windows paths, memory-address forms, timestamp forms, traceback markers, non-finite float tokens, and container `repr` forms are forbidden. CSV empty means null; an empty string is never a canonical detail. The exact per-code table further restricts the current literals.

`normalize_task_failure_reason(solver_status, certification_status, raw_failure_reason, structured_context)` accepts only audited literals or an approved structured exception origin. It never copies raw text. Unknown raw/origin combinations fail closed. Different raw debug messages may therefore normalize to the same formal code/detail, by design.

## Internal-object to formal-table audit

| Internal object field | Formal table | Existing v1.3.11 fields | Lossless now? | v1.3.12 repair |
|---|---|---|---|---|
| `TaskAnalysisRecord.failure_reason` | `per_task_results.csv` | solver/certification statuses only | no | add required `task_failure_reason_code` and conditionally non-null `task_failure_detail` |
| `TaskAnalysisRecord.solver_status` / `certification_status` | `per_task_results.csv` | both present | yes individually, but not provenance | enforce the code/detail state matrix |
| `TasksetAnalysisResult.first_failed_priority` | `per_taskset_results.csv` | `first_failed_priority` | yes | unchanged |
| analysis solver/certification failure class | `per_taskset_results.csv` | analysis solver status, certification status, proven flag, dominance status | yes | unchanged |
| `DominanceCounterexample` | task/taskset/dependency result structures | task candidate/counters, source linkage/vector, dominance status and counterexample fields | yes for formal certification semantics | task code identifies the affected task; no free-text analysis detail added |
| dependency validity/interface status/context | `rta_dependency_records.csv` and taskset rows | source/target hashes, vector checks, applicability/interface status | yes | unchanged; affected task rows use `DEPENDENCY_NOT_APPLICABLE` |
| validation failure before analysis | execution lifecycle | execution status/message | yes at execution layer | unchanged; never place it in generation or task failure fields |
| uncaught infrastructure/I/O failure | execution lifecycle | execution status/message | yes at execution layer | unchanged; raw exception text is not canonical task semantics |

No analysis-level optional failure-reason field exists in `TasksetAnalysisResult`. The existing structured taskset, dependency, dominance, and execution fields are sufficient; adding an analysis detail column would be unrelated contract expansion.

## Hash and identity audit

| Mechanism | Meaning | v1.3.12 action |
|---|---|---|
| `task_result_hash` | complete canonical task-result record integrity | bump preimage domain and include both new columns, with null distinct from the approved literal |
| taskset row/hash and package member hashes | aggregate/result integrity | naturally bind the changed task result member/hash; rebuild all embedded/sidecar hashes |
| canonical CSV row serialization | exact formal row bytes | fix both columns in canonical column order and reject the v1.3.11 header |
| semantic request ID | input/request identity | unchanged payload; failure code/detail are forbidden from the preimage |
| taskset/generation semantic identity | input/taskset identity | unchanged; no result diagnostics added |

This is the complete scope justified by the actual v9.3 producer domain.
