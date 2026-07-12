# ASAP-BLOCK v1.3.11 source-certification enum-level audit

Audit date: 2026-07-13

## Baseline

- Starting branch: `implement-v9.3-five-config-certification`.
- Starting HEAD: `3f7edf7338408f731877bc11cd728613b3cddf1e`.
- v9.3 theory SHA-256: `524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e`.
- Historical v1.3.10 ZIP SHA-256: `84e2103ee4963ff7b6a2dfa7330a823add8f0ba2e38275c7c23da83f2e0e1420`.
- The v1.3.10 directory and ZIP are retained unchanged. v1.3.11 is derived into new paths.

## P0 root cause and required correction

`per_task_results.csv.carry_in_source_certification_status` describes the
joint certification status of the complete source analysis. In v1.3.10 the
Data Dictionary incorrectly assigns it `enum_ref: task_certification_status`,
although the result validator and v9.3 fixed-carry-in theorem require the
analysis-level value `CERTIFIED_TASKSET`.

The v1.3.11 correction is exactly:

```text
task_certification_status -> analysis_certification_status
```

The result validator's requirement that an applicable formal
`LOC-Theta^cw` source be `CERTIFIED_TASKSET` is retained. It must not be
downgraded to task-level `CERTIFIED`.

## Coupled status-field level audit

| Structure / field | v1.3.10 enum_ref | Semantic level | Correct enum_ref | Modified? | Reason |
|---|---|---|---|---|---|
| `run_execution_log.execution_status` | `request_execution_status` | request execution | `request_execution_status` | No | Describes request-attempt lifecycle events. |
| `per_taskset_results.analysis_solver_status` | `analysis_solver_status` | analysis | `analysis_solver_status` | No | Describes the whole analysis run. |
| `per_taskset_results.analysis_certification_status` | `analysis_certification_status` | analysis | `analysis_certification_status` | No | Describes task-set joint certification. |
| `per_task_results.task_solver_status` | `task_solver_status` | task | `task_solver_status` | No | Describes one task solver result. |
| `per_task_results.task_certification_status` | `task_certification_status` | task | `task_certification_status` | No | Describes one task's final/provisional state. |
| `per_task_results.carry_in_source_certification_status` | `task_certification_status` | source analysis | `analysis_certification_status` | **Yes** | Copies source analysis joint-certification status; formal success is `CERTIFIED_TASKSET`. |
| `rta_dependency_records.source_task_solver_status` | `task_solver_status` | source task | `task_solver_status` | No | Copies the referenced source task solver state. |
| `rta_dependency_records.source_task_certification_status` | `task_certification_status` | source task | `task_certification_status` | No | Copies the referenced source task certification; formal value is `CERTIFIED`. |
| `rta_dependency_records.source_analysis_solver_status` | `analysis_solver_status` | source analysis | `analysis_solver_status` | No | Copies the source analysis solver state. |
| `rta_dependency_records.source_analysis_certification_status` | `analysis_certification_status` | source analysis | `analysis_certification_status` | No | Copies source joint certification; formal value is `CERTIFIED_TASKSET`. |
| `simulation_bound_checks.analysis_solver_status` | `analysis_solver_status` | analysis | `analysis_solver_status` | No | Copied analysis status used by theorem applicability. |
| `simulation_bound_checks.analysis_certification_status` | `analysis_certification_status` | analysis | `analysis_certification_status` | No | Copied joint-certification status. |
| `simulation_bound_checks.task_solver_status` | `task_solver_status` | task | `task_solver_status` | No | Copied task result status. |
| `simulation_bound_checks.task_certification_status` | `task_certification_status` | task | `task_certification_status` | No | Copied task certification status. |

The queried generic names `carry_in_source_solver_status`,
`carry_in_target_solver_status`, `carry_in_target_certification_status`,
`source_solver_status`, `source_certification_status`, `target_solver_status`,
and `target_certification_status` are not CSV fields in the frozen schema.
The formal-contract policy object uses `source_solver_status` and
`source_certification_status` to describe the source analysis and already uses
`COMPLETED` / `CERTIFIED_TASKSET`, so its level is correct.

## Coupled contract artifacts

- Main Markdown: one contradictory normative example must change from
  `carry_in_source_certification_status = CERTIFIED` to
  `CERTIFIED_TASKSET`; the distinct source-task status remains `CERTIFIED`.
- Experiment schema: field classification and conditional requirement are
  already correct; the scalar enum type is authoritative in the Data
  Dictionary, so only version/reference updates are needed in the schema.
- Data Dictionary: enum reference requires the P0 correction.
- Result validator: retain `CERTIFIED_TASKSET`; add type-aware positive and
  destructive contract-state tests so `CERTIFIED` cannot regress silently.
- Artifact validator: add a mechanical invariant that the dictionary field
  references `analysis_certification_status`, plus mutations for the old enum
  and downgraded validator predicate.
- Canonical serialization: the carry-in preimage contains values, not the enum
  type name; no semantic preimage redesign is needed. Domains are versioned to
  v1.3.11 as part of the new contract version.
- CSV templates: no per-task-result CSV template exists; the three supplied
  templates contain headers only and need filename/version reference updates,
  not column changes.
- Machine manifest, formal/acceptance templates, common/acceptance validators,
  child templates, sidecars, embedded hashes, validation report, ZIP manifest,
  and ZIP sidecar must be regenerated because v1.3.11 names and versioned
  domains change their bytes or upstream bindings.

## Scope guard

This repair does not modify the v9.3 theory, RTA core, task-set analyzer,
runner, C++ scheduler, task generator semantics, or formal experiment
parameters. It does not run CORE-0A, pilot, CORE-0B, or formal experiments.

## Validation results

All commands below completed locally; no experiment was started.

| Check | Command / scope | Exit | Result |
|---|---|---:|---|
| Strict YAML | `load_yaml_strict()` over all contract YAML | 0 | 9/9; duplicate keys, aliases, implicit floats rejected mechanically |
| Schema-only | `ASAP_BLOCK_result_validator_v1_3_11.py <root> --schema-only` | 0 | PASSED |
| Artifact baseline | `ASAP_BLOCK_artifact_validator_v1_3_11.py <root>` | 0 | 12/12 reported checks PASSED |
| Artifact self-test | artifact validator `--self-test` | 0 | 18/18 cases PASSED |
| Result self-test | result validator `--self-test` | 0 | 39/39 cases PASSED |
| Acceptance self-test | acceptance validator `--self-test` | 0 | 11/11 cases PASSED |
| Positive LOC dependency | result validator `--contract-state-test positive_certified_vector` | 0 | typed enum, joint certification, copied identities, local dominance and canonical carry-in-prefix hashes PASSED |
| Task-level `CERTIFIED` mutation | `source_certification_task_value` | 1 | rejected by `analysis_certification_status` type and semantic domain |
| Task-level provisional mutation | `source_certification_provisional_value` | 1 | rejected by type and semantic domain |
| Uncertified source with forged certified dependency | `source_uncertified` | 1 | rejected by source-copy and applicability checks |
| Certified source with dependency `NOT_CERTIFIED` | `dependency_source_not_certified` | 1 | rejected by source-copy and applicability checks |
| Provisional source task under certified source analysis | `source_task_provisional` | 1 | rejected |
| Frozen carry-in hash mutation | `carry_in_vector_hash_mismatch` | 1 | rejected by canonical prefix-hash recomputation |
| Historical enum-ref mutation | artifact validator on a fully rebound mutation | 1 | semantic type invariant rejected `task_certification_status` |
| Downgraded validator predicate mutation | artifact validator on a fully rebound mutation | 1 | static predicate invariant and result self-test rejected `CERTIFIED` |
| Sidecars | independent verification | 0 | 19/19 primary-file sidecars valid |
| Embedded hashes | independent verification | 0 | 16/16 formal artifact bindings valid |
| ZIP integrity/manifest | Python `zipfile.testzip()`, exact ordered member/hash comparison | 0 | 38/38 members valid |
| Fresh read-only extraction | artifact validator on `0555` directory / `0444` files | 0 | 12/12 reported checks PASSED |

Positive contract-state values:

- source: `CW-Theta^cw`, `COMPLETED`, `CERTIFIED_TASKSET`, candidates `{0: 4, 1: 7}`;
- source tasks: both `CANDIDATE_FOUND + CERTIFIED`;
- target: `LOC-Theta^cw`, `COMPLETED`, `CERTIFIED_TASKSET`, `ACTIVE`, dominance `SATISFIED`;
- target candidates `{0: 3, 1: 6}`, each no greater than its source CW candidate;
- target task source status: `CERTIFIED_TASKSET`;
- target prefix hashes:
  `b6e28e63c4c10b0bdb47c98c73258090e0f772ba785b9df2b989a79853d0eb5c`
  and
  `f802c7dd10594cfee6bcf63e71ae08310279f7e37df8b8cc622dd3ea80b943e3`;
- source/target task-set, priority, E0, service, power, numeric, formal,
  theory and fixed-interface identities are equal.

Frozen outer hashes:

- v1.3.11 ZIP SHA-256:
  `e406e53ae9c63aaec116b5eade6a581fb7e3e719d059672b2987eaf0f1667399`;
- v1.3.11 ZIP manifest SHA-256:
  `e741048c46745a761d06e96708d512477a5b5759fd15303c3e26a715a60bc2c2`.
