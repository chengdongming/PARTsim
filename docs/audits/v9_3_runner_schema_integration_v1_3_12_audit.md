# ASAP-BLOCK v9.3 runner / v1.3.12 schema integration audit

Date: 2026-07-13

Baseline commit: `40900972a604383a24b840e9cb71127276aba45c`

Contract authority: `docs/ASAP_BLOCK_v1_3_12_机器合同静态冻结候选包/`

Theory SHA-256: `524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e`

This audit precedes implementation. It was produced by directly loading the v1.3.12 experiment schema and data dictionary with the frozen strict loader, and by reading the canonical serialization, interface manifest, formal template, four CSV templates, result validator, artifact validator, acceptance validator, and validation common module.

## 1. Existing experiment and RTA entry points

| Concern | Existing code path | Current behavior |
|---|---|---|
| Primary acceptance experiment | `acceptance_ratio_test.py:main()` | Generates task sets, invokes the simulator and optional RTA, and writes legacy experiment CSV/JSONL artifacts. |
| Higher-level batch runner | `scripts/experiment_runner.py` | Builds `acceptance_ratio_test.py` commands, resumes runs, and writes legacy manifests/attestations. |
| Default RTA | `acceptance_ratio_test.run_asap_block_rta()` | Constant `RTA_VERSION = v20.4`; subprocess calls `asap_block_rta.py`; no version selector. |
| v20.4 implementation | `asap_block_rta.py:analyze_taskset()` | Existing v20.4 analysis and JSON contract. |
| v21 implementation | `asap_block_rta_v21_local_window.py:analyze_taskset_v21()` | Existing v21 local-window analysis. |
| v21 experiment path | `scripts/run_rta_v21_comparison.py:_run_v21()` | Explicit subprocess call to the existing v21 tool; v20.4 comparison still calls the existing acceptance adapter. |
| v9.3 mathematical core | `asap_block_rta_v9_3.py` | Exact single-task core; no experiment writer. |
| v9.3 task-set state machine | `asap_block_rta_v9_3_taskset.py:analyze_taskset_v9_3()` | Five configurations and joint certification; currently called only by tests/direct Python users. |

There is no central explicit RTA version dispatcher and no v9.3-to-v1.3.12 serializer. The legacy default must remain v20.4. The new integration should be a separate explicit runner surface, while dispatching v20.4 and v21 to their unchanged functions.

## 2. Existing data structures

The v20.4/v21 loaders use `RTATask` and YAML system/task files. The v9.3 integration uses immutable objects:

- `V93Task(name, period, wcet, deadline, power)`;
- `DependencyContext`, holding task-set, task-definition, priority, E0, service, power, numeric, theory, interface, and formal-contract identities;
- `TasksetAnalysisInput(tasks, processors, e0, beta, dependency_context, timeout_seconds)`;
- `TaskAnalysisRecord`, containing task/priority status, certification status, candidate, carry-in vector, closure/witness, counters, and raw `failure_reason`;
- `TasksetAnalysisResult`, containing analysis/task statuses, task vector, counts, first failed priority, source vector, dependency/interface/dominance states, diagnostic flag, context, and optional dominance counterexample.

The runner must construct `TasksetAnalysisInput`, invoke `analyze_taskset_v9_3()`, and serialize the returned immutable result without recomputing certification semantics.

## 3. Existing writers, IDs, and manifests

The existing acceptance experiment writes legacy result fields, JSONL and CSV with hand-maintained field lists. `ExperimentRunner.run_id` is a random UUID, while task-set semantic hashes use sorted JSON in a legacy domain-free form. `scripts/experiment_runner.py` writes separate legacy provenance manifests and command hashes.

None of those writers implement the v1.3.12 request DAG, exact 23-table interface, domain-separated IDs, formal-contract hash DAG, request-output bundles, or `task_result_hash`. Reusing their output as v1.3.12 rows would be invalid. Execution UUIDs may remain execution-only, but cannot become semantic IDs.

## 4. v1.3.12 authoritative table set

The interface manifest and schema define exactly 23 tables. `analysis_requests.csv` is not a contract table; an analysis request is a `run_plan_definition.csv` row whose `request_type` is `ANALYSIS`.

| Table | Columns | Required / conditional / optional | Primary key | FK / composite FK | Nullable fields |
|---|---:|---:|---|---:|---:|
| `run_plan_definition.csv` | 45 | 11 / 31 / 3 | `request_id` | 0 / 0 | 34 |
| `run_plan_dependencies.csv` | 3 | 3 / 0 / 0 | request, dependency, role | 2 / 0 | 0 |
| `run_execution_log.csv` | 18 | 9 / 6 / 3 | request, attempt, event | 1 / 0 | 9 |
| `generation_requests.csv` | 33 | 16 / 7 / 10 | `generation_request_id` | 1 / 1 | 17 |
| `paired_transformations.csv` | 14 | 13 / 1 / 0 | `transformation_id` | 4 / 1 | 1 |
| `tasksets.csv` | 22 | 13 / 1 / 8 | `taskset_id` | 2 / 0 | 9 |
| `task_definitions.csv` | 11 | 9 / 0 / 2 | taskset, task | 1 / 0 | 2 |
| `release_trace_sets.csv` | 13 | 12 / 1 / 0 | `release_trace_id` | 2 / 0 | 1 |
| `release_traces.csv` | 10 | 9 / 0 / 1 | trace, job | 1 / 0 | 1 |
| `harvest_trace_sets.csv` | 13 | 12 / 1 / 0 | `harvest_trace_id` | 1 / 0 | 1 |
| `harvest_traces.csv` | 8 | 8 / 0 / 0 | trace, tick | 1 / 0 | 0 |
| `per_taskset_results.csv` | 93 | 82 / 8 / 3 | `analysis_run_id` | 4 / 1 | 11 |
| `per_task_results.csv` | 41 | 25 / 12 / 4 | analysis, task | 2 / 2 | 16 |
| `rta_dependency_records.csv` | 48 | 38 / 8 / 2 | analysis, target task, HP task | 2 / 2 | 10 |
| `simulation_taskset_summary.csv` | 40 | 29 / 5 / 6 | `simulation_run_id` | 4 / 1 | 11 |
| `simulation_job_results.csv` | 18 | 15 / 0 / 3 | simulation, job | 1 / 3 | 3 |
| `service_trace_checks.csv` | 26 | 21 / 5 / 0 | `service_trace_check_id` | 3 / 0 | 5 |
| `e0_trace_certificate_checks.csv` | 24 | 20 / 4 / 0 | `e0_certificate_check_id` | 3 / 0 | 3 |
| `e0_job_certificate_checks.csv` | 9 | 9 / 0 / 0 | certificate, simulation, analysis, job | 2 / 4 | 0 |
| `analysis_simulation_compatibility_checks.csv` | 28 | 26 / 2 / 0 | `compatibility_check_id` | 3 / 0 | 2 |
| `simulation_bound_checks.csv` | 34 | 28 / 4 / 2 | simulation, job, analysis | 4 / 11 | 6 |
| `request_outputs.csv` | 12 | 11 / 1 / 0 | request, output index | 1 / 0 | 1 |
| `bound_audit_runs.csv` | 14 | 13 / 1 / 0 | `bound_audit_run_id` | 3 / 0 | 1 |

For a DIAGNOSTIC microcase without simulation, all 23 tables must exist. Simulation, trace-check, compatibility and bound-audit tables may contain only their exact header; no simulated results or acceptance passes may be invented.

## 5. Exact `per_task_results.csv` order

The authoritative 41-column order is:

1. `analysis_run_id`
2. `taskset_id`
3. `task_id`
4. `analysis_method_role`
5. `variant`
6. `window_mode`
7. `carry_in_mode`
8. `priority_rank`
9. `C_i`
10. `T_i`
11. `D_i`
12. `P_hat_i_raw`
13. `task_solver_status`
14. `task_certification_status`
15. `task_failure_reason_code`
16. `w_values_checked`
17. `h_values_checked`
18. `q_values_checked`
19. `full_w_scan_conformance`
20. `full_h_scan_conformance`
21. `full_q_scan_conformance`
22. `envelope_call_count`
23. `energy_numeric_mode`
24. `dominance_invariant_status`
25. `task_result_hash`
26. `task_failure_detail`
27. `P_hat_i_scaled`
28. `P_hat_i_rounding`
29. `candidate_response_time`
30. `source_analysis_run_id`
31. `carry_in_vector_hash`
32. `carry_in_source_variant`
33. `carry_in_source_certification_status`
34. `fixed_carry_in_corollary_status`
35. `dependency_vector_check_status`
36. `dependency_input_failure_mask`
37. `closing_w`
38. `witness_h`
39. `critical_q`
40. `minimum_energy_slack`
41. `processor_delay_Dp`

`task_failure_reason_code` is a required enum. `task_failure_detail` is nullable but conditionally required by code. `carry_in_source_certification_status` references the analysis-level `analysis_certification_status` enum; `CERTIFIED_TASKSET` is legal and task-level `CERTIFIED` is illegal.

## 6. Canonical identities and hashes

All IDs use `SHA256(domain_utf8 || 0x00 || canonical_json(preimage))`, with NFC strings, sorted UTF-8 mapping keys, no floats, and JSON null distinct from CSV empty. Relevant domains include:

- `ASAP_BLOCK:REQUEST_PAYLOAD:<TYPE>:v1.3.12`;
- `ASAP_BLOCK:REQUEST:v1.3.12`;
- `ASAP_BLOCK:EXPECTED_OUTPUT:v1.3.12`;
- `ASAP_BLOCK:TASKSET_SEMANTIC:v1.3.12`;
- `ASAP_BLOCK:CARRY_IN_VECTOR:v1.3.12`;
- `ASAP_BLOCK:DEPENDENCY_RECORD:v1.3.12`;
- `ASAP_BLOCK:TASK_RESULT:v1.3.12`.

`task_result_hash` includes every per-task field except itself, including the structured failure code and normalized detail. Request and task-set semantic identities exclude result failure fields and raw debug text.

## 7. Failure provenance contract

| Task state/context | Formal code | Exact detail |
|---|---|---|
| candidate, no dominance counterexample | `NONE` | null |
| ordinary no candidate | `NO_CANDIDATE` | `closure exhausted through task deadline` |
| timeout | `SOLVER_TIMEOUT` | null |
| numeric/overflow | `NUMERIC_ERROR` | `numeric guard rejected analysis` |
| skipped after prefix failure | `UPSTREAM_PREFIX_FAILURE` | null |
| fixed dependency not applicable | `DEPENDENCY_NOT_APPLICABLE` | null |
| candidate/no-candidate dominance counterexample | `DOMINANCE_INVARIANT_VIOLATION` | `local result violated frozen carry-in dominance` |
| adapter saw unknown core status | `UNKNOWN_CORE_STATUS` | `unrecognized core solver status` |
| analyzer internal conformance failure | `INTERNAL_CONFORMANCE_FAILURE` | `internal analyzer conformance failure` |

Raw `failure_reason`, exception strings, repr and tracebacks are never formal. A mapping is accepted only when solver status, certification status, variant, dominance/dependency state, audited raw literal and structured origin agree. Unknown combinations are serializer failures.

## 8. LOC-Theta-cw dependency requirements

The target must consume a completed, immutable `CW-Theta^cw` result whose analysis certification is `CERTIFIED_TASKSET` and whose tasks are all `CANDIDATE_FOUND + CERTIFIED`. The dependency row and target task fields copy the analysis-level value `CERTIFIED_TASKSET`. The source candidate vector and target frozen carry-in vector must be generated from the same priority-sorted entries and have equal canonical hashes. No fallback to deadlines, zero, local candidates, partial results, or mutable shared dictionaries is legal.

## 9. Result-package and acceptance behavior

The result validator always requires a filled, self-hashed formal contract, filled/self-hashed child contracts, immutable artifact bindings, all required files, runtime manifest, SHA-256 manifest, acceptance report, and all 23 tables. For non-release microcases, the result profile may be `DIAGNOSTIC`; `FORMAL_RELEASE` non-vacuity is not claimed.

The acceptance validator has no microcase/template bypass for a runtime package. A legal pre-CORE0A package must therefore use an executed, self-hashed report with every required gate `NOT_CHECKED`, `overall_release_gate = FAILED`, and all gate IDs in `failed_gate_ids`. It must not report a passed CORE-0A/CORE-0B gate.

The artifact validator validates the immutable 40-member v1.3.12 contract artifact, not an arbitrary runtime result directory. Runtime result validation is performed by the result validator; artifact validation remains pointed at the frozen contract directory copied into/bound by the result package.

## 10. Integration gaps

1. No explicit common dispatcher for `v20.4`, `v21`, and `v9.3`.
2. No five-configuration v9.3 runner orchestration or immutable CW source handoff.
3. No schema-driven table binding; existing writers use hand-maintained legacy columns.
4. No serializer/deserializer for `TasksetAnalysisResult`.
5. No fail-closed runtime failure-provenance mapper.
6. No v1.3.12 request DAG, semantic IDs, output bundles, dependency rows, runtime manifest, or task hashes.
7. Existing execution UUID and timestamps are mixed into legacy provenance and cannot be reused as semantic identity.
8. Existing numeric output paths commonly use floats and are not theorem-backed canonical serialization.
9. No frozen A/B/C microcases, validator-complete result package, 30 mutation suite, or deterministic rerun comparison.

No schema conflict has been found at audit time: the DIAGNOSTIC profile permits simulation/audit tables to remain empty, while the failed/NOT_CHECKED acceptance report avoids pretending that CORE-0A or CORE-0B ran.

## 11. Modification boundary

Allowed in this round: new/updated Python runner dispatch, v9.3 adapter, schema binding, serializers, result-package and manifest builders, dependency/failure mapping, tests, audit, and frozen microcase fixtures.

Forbidden: v9.3 theory, `asap_block_rta_v9_3.py`, `asap_block_rta_v9_3_taskset.py`, every v1.3.12 contract artifact/validator/sidecar/ZIP, v20.4/v21 mathematics, C++, generator semantics, formal parameters, default RTA version, and CORE-0A/pilot configuration.
