# ASAP-BLOCK v9.3 / v1.3.12 CORE-0A independent evidence audit

Date: 2026-07-13 (Asia/Shanghai)

Decision: **CORE-0A PASS is not independently confirmed. Pilot is forbidden.**

The current implementation deterministically reproduces several reported
summary counts, but the evidence package does not contain the row-level records
needed to independently rebuild the required gates. More importantly, at least
one gate is a demonstrable false PASS and the acceptance chain accepts four
adversarial, internally rehashed mutations.

## Baseline

- HEAD: `dcb55f6a22f4d772a74f94ac7799b79cf5da8541`
- branch: `core0a-v9.3-v1.3.12`
- both `6de2015e...` and `dcb55f6a...` are HEAD ancestors
- theory SHA-256: `524d4f84...65098e` (match)
- v1.3.12 contract ZIP SHA-256: `b6788229...959895` (match)
- evidence ZIP SHA-256: `d56c2f67...8dd50` (match)
- ZIP manifest SHA-256: `149bbf24...86a0` (match)
- embedded manifest SHA-256: `ab7bebc7...ad17` (match)
- package build/source identity: `6de2015e...`, **not current HEAD**

Pre-existing untracked user paths were preserved. This audit added only the
four replay/report support files named at the end of this report.

## Frozen gate definition

The acceptance template and acceptance validator copied into the package are
byte-identical to the frozen v1.3.12 contract ZIP. Exactly 11 CORE-0A gates are
present, every `required` flag is true, predicate/count-key structures match,
all 14 CORE-0B gates remain `NOT_CHECKED`, and the overall gate is `FAILED`
with `release_authorized=false`.

| gate | predicate SHA-256 | reported | independent result |
|---|---|---:|---|
| `full_w_q_h_scan` | `63897fda506af77cffa3b9205ed01909348770a909e941547979fa4e0db710ff` | PASS | current execution matches; package raw replay unavailable (P0) |
| `exact_exhaustive_domain_zero_mismatch` | `58fe168cd7c7016bea347b214e8f8dee7db736a82b6434b2e3b4f9eb6cf6ab56` | PASS | 160 inputs / 320 complete+local comparisons reproduce; package raw replay unavailable |
| `exact_random_boundary_instances_at_least_10000` | `d1a2d7e7a47120d755774b32416deace05a566cc3118cefb2e19fd5077000671` | PASS | 50,000 inputs and both variants reproduce; package raw replay unavailable |
| `processor_term_direct_scan_zero_mismatch` | `756e27eca6ca77db9b8fdb84cdf50d765e0511c075c01f9d6726e6bf0d853817` | PASS | **not confirmed**: fast and scan share `effective_hp_workloads_v9_3` (P0 oracle) |
| `finite_state_counterexample_search` | `cd20de9155b8c7837cb829544f343902f7b90801384912be2552980de2933f39` | PASS | execution reproduces summary; no taskset/job/tick records and domain description is broader than enumeration |
| `event_order_and_scheduler_semantics_microcases` | `786c0d290b5ca4bcd75365754612d65de28bd801bf2fa58ced49b331d9f265c5` | PASS | **FAIL**: producer returns literal `{6,0}` without executing six cases |
| `joint_certification_state_machine` | `4a11e3939bc6b1486da95627a3fc7fdcde8dfb903c5f5f3d41aa9ae20769b737` | PASS | 17 calls reproduce, but requested per-case evidence is absent |
| `loc_theta_cw_dominance_invariant` | `863c410ea2f9eea36d6310199944e580745b94ea4d25409a236ead2c5dedbf83` | PASS | **FAIL**: 200 cases are only 3 structural tasksets repeated 66/67/67 times; no source-vector records |
| `service_curve_contract_checks` | `6dc9f2f92199b7d6f1b26d952203198f7d4f15bdd378156a26fbcaf97123044c` | PASS | **FAIL / false PASS**: invalid count is manually decremented; invalid curves are accepted |
| `schema_lineage_and_request_state_machine` | `45b2bf58fb992a4a6d9eb5c9e6ff486969e4799687707df9bc0c03adf4aaaa13` | PASS | key/FK/DAG subset passes, but reported `N_rows=1` is a literal and not derived from 101 formal rows |
| `non_vacuity_coverage` | `01610f91e0211c4d4c092f77b5ef537c2255874a9bbf03d9c0f5416f07bf08f3` | PASS | execution reproduces summary, but no event/job records; zero-positive-E0 mutation survives |

No predicates are `1 == 1`. The defect is instead that the bound gate validator
is also the evidence producer and validates its own self-hashed summaries.

## Independently rerun counts

The complete 50,000-input audit was rerun in three environments. Base metric
objects and gate counts match the stored summary.

- workload: 35,805 cases, 61,775 monotonicity checks, 0 mismatch/violation
- exhaustive envelope: 160 inputs; complete 160 and local 160; 0 mismatch
- random envelope: 50,000 legal inputs; complete/local each 50,000; seed
  `39661998876`; 0 mismatch; no `continue`/filter/skip in the generation loop
- processor: 105 frozen + 10,000 random = 10,105; numeric mismatch 0, but
  oracle independence fails because both sides share the workload/truncation helper
- search re-execution: tasks 102; expected/observed `w=106/106`,
  `h=113/113`, `q=115/115`; omission/duplicate/order violations 0. The
  package stores only a hash over 15 injected q-events, not these records.
- finite-state: enumerated/complete/certified `14/14/14`; checked jobs 25;
  positive-E0 jobs 25; processor-blocking 5; energy-blocking 379; bound
  violation/inconclusive/internal error `0/0/0`
- dominance: common 200 and numeric violations 0, but only 3 structural
  tasksets exist
- state machine: 17 calls, reported failures 0
- service curves: reported `8/0`; actual audit source includes 5 valid local
  arrays and 3 invalid arrays, then subtracts all 3 invalid findings

An independent scheduler implementation replayed three finite-state tasksets:

| case | candidates | checked | positive E0 | processor | energy | violations |
|---|---:|---:|---:|---:|---:|---:|
| one-task structural case | `[1]` | 1 | 1 | 0 | 0 | 0 |
| energy witness | `[2]` | 3 | 3 | 0 | 1 | 0 |
| processor witness | `[1,2]` | 2 | 2 | 1 | 34 | 0 |

The reported finite-state domain string suggests a cross-product over
`D=2..3,T=3..4`; the code instead fixes task rank 0 to `(C,D,T)=(1,2,3)`
and rank 1 to `(1,3,4)`, enumerates powers, then appends two witnesses.

## Raw evidence and lineage

All CORE-0A gate bundles ultimately replay only `core0a_audit.json`. Additional
JSON files are checked for byte hash but never parsed by the bound gate
validator. The formal CSV tables describe a separate 3-taskset diagnostic
microcase, not the 35,805 workload, 10,105 processor, 50,160 envelope, 200
dominance, or 14 finite-state audit objects.

Zero-row formal tables include `simulation_job_results.csv`,
`simulation_taskset_summary.csv`, `simulation_bound_checks.csv`,
`e0_job_certificate_checks.csv`, `e0_trace_certificate_checks.csv`, and
`service_trace_checks.csv`. Consequently, the requested workload/processor/
envelope/search/dominance/finite-state/job/E0 sampling cannot be performed from
the package. This is a critical lineage break, not a sampling failure.

For the diagnostic formal tables that do exist, an independent simplified
check found:

- total formal data rows: 101 across 10 nonempty tables
- PK duplicates: 0
- single/composite FK violations: 0
- canonical header violations: 0
- planned/accounted requests: 13/13
- terminal sequences: 12 `STARTED -> FINISHED`, 1
  `STARTED -> NOT_RUN_DEPENDENCY`
- dependency cycles: 0

The one valid LOC-Theta-cw diagnostic dependency can be followed through its
formal rows, but it is not evidence for the reported 200 dominance cases.

## Oracle independence

| pair | conclusion |
|---|---|
| workload implementation vs local formula | independent arithmetic body |
| processor fast vs scan | **not independent**; both call `effective_hp_workloads_v9_3` |
| envelope fast vs brute force | independent vector enumeration; shared data model only |
| finite-state Python scheduler vs RTA | separate scheduler body, but not the production C++ scheduler |
| finite-state expected bound vs RTA | separate simulation comparison after RTA candidate production |
| result writer vs result validator | separate modules; shared canonical data utility |
| gate producer vs acceptance validator | **not independent**; producer file validates its own summary and is delegated to by acceptance |

## Mutation replay

The reported ten mutations are not mutations of production code. Each
parameterized test computes a hand-written mutant value or checks current
behavior; no source is changed, no expected test is observed failing, and no
restore is exercised.

Five added adversarial mutations produced:

| mutation | applicable | acceptance rejected |
|---|---:|---:|
| acceptance random count 50,000 -> 49,999, internal hashes refreshed | yes | **no** |
| decorative evidence replaced by `{}`, internal hashes refreshed | yes | **no** |
| positive-E0 count -> 0, internal hashes refreshed | yes | **no** |
| delete one finite-state checked-job row | **no: baseline has zero rows** | no |
| LOC carry-in vector hash replaced without its canonical dependency hash refresh | yes | **no** |

These are validator-sensitivity failures. Refreshing ordinary unkeyed hashes is
part of the test because the frozen validator must derive meaning from evidence,
not merely compare producer-controlled summaries.

## Service-curve minimum counterexample

`canonical_closure_search_v9_3` accepts each sequence below and returns
`CANDIDATE`, because only the accessed scalar is checked for non-negativity:

```text
[1, 1]    # beta(0) != 0
[0, 2, 1] # decreasing
[0, -1]   # negative future value
```

The gate producer never invokes production rejection. At
`core0a_v9_3_audit.py:631-634`, it detects each invalid list and then executes
`invalid -= 1`, forcing the official count back to zero.

## Determinism and environment

Three complete 50,000-input replays were performed:

1. current workspace: 24.90 s
2. detached fresh HEAD worktree: 24.28 s
3. read-only ZIP extraction from `/tmp`, `PYTHONHASHSEED=777`, `LC_ALL=C`:
   25.11 s

All three output files are byte-identical, SHA-256
`284d6d1d07ac16c05dcf91ea1b16b9c021976de6176d1f77855063766d18aa6d`.
Absolute-path acceptance replay from `/tmp` succeeds, so the relative-path
behavior is a CLI constraint rather than this audit's blocker.

Rebuilding the ZIP from the stored summary record reproduces the exact ZIP and
manifest hashes. This proves deterministic packaging of the stored summary; it
does not reconstruct the summary from raw evidence.

## Legacy warnings

Full pytest emits exactly 32 `RuntimeWarning`s with one semantic code:
`legacy result provenance synthesized explicitly ... do not use it for formal
publication output`. They arise only from tests that explicitly pass
`allow_legacy=True`, under temporary pytest fixtures, through
`scripts/experiment_analysis.py` and `scripts/run_scheduler_diversity_audit.py`.
They do not enter the CORE-0A package, its current theory/build identities, gate
evidence, or hashes. Classification: P2.

## ZIP and tamper structure

- 83 members, 83 unique
- duplicate/path-traversal/absolute/case-collision/nonregular members: 0
- CRC failures: 0
- side manifest missing/extra/hash mismatch: 0/0/0
- embedded manifest: 81 declared payload members, all hashes valid; only
  `manifest.json` and `sha256sum.txt` are intentionally outside its file map
- `sha256sum.txt`: 82 entries, all valid
- UTF-8 failures, BOM, CR, missing terminal LF: all 0
- packaged Python files are mode 0644 and invoked explicitly through Python

The package nevertheless fails the semantic tamper requirement because test
scripts and row-level audit inputs are absent, the runner is only named by a
summary hash, and the LOC/source mutation is invisible to acceptance replay.

## Validator and regression commands

| command | result |
|---|---|
| `python3 core0a_v9_3_audit.py --random-instances 50000 ...` | exit 0, 24.90 s |
| `python3 -m pytest -q test/test_core0a_v9_3_conformance.py` | 13 passed, 1.16 s |
| `python3 -m pytest -q test/test_asap_block_rta_v9_3.py` | 22 passed, 4.87 s |
| `python3 -m pytest -q test/test_asap_block_rta_v9_3_taskset.py` | 46 passed, 0.64 s |
| `python3 -m pytest -q test/test_asap_block_v9_3_runner_v1_3_12.py` | 10 passed, 4.81 s |
| `python3 -m pytest -q test/test_asap_block_v9_3_v1_3_12_microcases.py` | exit 0, 6 tests |
| `python3 -m pytest -q test/test_*rta*.py` | 271 passed, 21.76 s |
| `python3 -m pytest -q -k rta` | 354 passed, 412 deselected, 42.15 s |
| `python3 -m pytest -q` | 704 passed, 62 skipped, 32 warnings, 137.31 s |
| `./build/test/test_librtsim --gtest_filter=ASAPBlockScheduler.*` | 12 passed, 0.073 s |
| frozen acceptance validator on package | exit 0, 2.35 s |
| frozen result validator on package | **exit 1**, 7 formal-release errors, 3.56 s |
| frozen result validator `--schema-only` | exit 0, 1.12 s |
| frozen artifact validator on frozen contract | exit 0, 19.33 s |
| frozen artifact validator on evidence package | **exit 1** (wrong file set) |

The stored `rta_regression: 361 passed` has no command and was not reproduced by
either natural selection (`test/test_*rta*.py` gives 271; `-k rta` gives 354).
The stored `result_validator: PASSED` is reproducible only with the omitted
`--schema-only` qualifier. The stored artifact PASS applies to the frozen
contract artifact, not the CORE-0A evidence root.

## Findings

### P0

1. False service-curve PASS and concrete invalid-curve counterexamples.
2. Required counts cannot be rebuilt from package row-level evidence.
3. Gate producer and delegated gate validator are the same trust source.
4. Processor fast/scan oracle pair shares the audited workload/truncation helper.
5. Event-order, schema-lineage, failure-provenance, and mutation summaries are literals.
6. Positive-E0 zero, empty evidence, count decrement, and LOC hash mutations survive.
7. Dominance `N_common_cases=200` is manufactured from only 3 structural tasksets.
8. Search/non-vacuity/finite-state critical counts have no packaged event/job records.

### P1

1. Full result validation fails while the stored report says PASS.
2. Artifact PASS refers to the contract package, not the evidence package.
3. Build identity is bound to `6de2015e...`, not current HEAD `dcb55f6a...`.
4. Finite-state frozen-domain prose does not match the actual enumeration.
5. Requested lineage samples cannot be drawn because the relevant records are absent.
6. Original ten mutation claims do not perform mutations.
7. Exact regression command provenance is missing; the claimed 361 RTA count is not reproducible.

### P2

1. The 32 explicit legacy-provenance warnings are confined to legacy test fixtures.
2. The theory filename differs from the request's `(1)` suffix, but content hash matches.

## Required remediation before a new CORE-0A claim

Do not patch the existing PASS in place. Add row-level deterministic evidence
for every gate; make the gate validator independently aggregate those rows;
replace literal auxiliary counts with executable cases; introduce an independent
processor definition oracle; diversify dominance inputs; bind runner/test
sources and exact commands; make all 15 mutations fail; then rerun the complete
suite and generate a new evidence package and new hashes. The old ZIP must remain
failed and must not be used for pilot authorization.

Audit support files (uncommitted):

- `docs/audits/v9_3_core0a_independent_replay.py`
- `docs/audits/v9_3_core0a_independent_replay.json`
- `docs/audits/v9_3_core0a_mutation_replay.py`
- `docs/audits/v9_3_core0a_mutation_replay.json`
