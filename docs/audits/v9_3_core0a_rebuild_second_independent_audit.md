# ASAP-BLOCK v9.3 / v1.3.12 rebuilt CORE-0A second independent audit

## Decision

**FAILED — rebuilt CORE-0A PASS is not independently confirmed and must not be
restored as a formal PASS. Pilot remains unauthorized.**

The numeric workload, processor, envelope, dominance, finite-state, positive-E0,
build-identity, validator, determinism, and regression results replay cleanly.
That is insufficient because four raw gates cannot be evaluated from their raw
rows, and the bundled aggregator has a demonstrated event-trace mutation
survivor. This is a P0 under the supplied classification: raw evidence cannot
recompute every gate.

No PASS audit commit was created.

## Preservation and invalidation record

Both prior CORE-0A evidence packages are invalidated and are not authoritative:

| Evidence commit | ZIP SHA-256 | Status |
|---|---|---|
| `dcb55f6a22f4d772a74f94ac7799b79cf5da8541` | `d56c2f671b8ea201e6e53a4199cba333f3dcc6eb1e09ff06a1bfa8b76db8dd50` | `INVALIDATED` |
| `01f582b094f376a8e00640e22d0d2f25506d0e35` | `a51ceee47c9f0e32a80a23f4c419af1271d35b29522d91b6630812bb362a2995` | `INVALIDATED` |

There is currently no authoritative CORE-0A PASS. `pilot_authorized=false`.
This preservation commit records audit evidence only; it does not modify the
production theory, v1.3.12 contract, or scheduling algorithms.

## Baseline and immutable identities

- branch: `core0a-v9.3-v1.3.12-rebuild`
- HEAD: `01f582b094f376a8e00640e22d0d2f25506d0e35` (commit B)
- commit A: `cccda1ed39993a5540538f3a95b11dcee626a3f0`
- B's sole parent: commit A
- A and B are both ancestors of HEAD
- tracked/staged diff at audit start: empty
- pre-existing untracked files were present; the audit did not use them as
  positive evidence. No tracked production mutation was present.

Commit A changes 22 implementation/test/oracle/producer/aggregator/mutation and
supporting audit files (5,035 insertions, 34 deletions). Commit B adds 11
evidence/report files (including the 21,925,845-byte ZIP). Neither commit changes
the frozen theory file or contract ZIP. A does contain supporting audit Markdown
and replay JSON files; B contains the rebuilt result package and report.

Recomputed hashes:

| Object | SHA-256 | Result |
|---|---|---|
| theory | `524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e` | match |
| v1.3.12 contract ZIP | `b67882290d4d4688a0e81fd98f95e9d998537facfb9f5945d1ec125143959895` | match |
| rebuilt evidence ZIP | `a51ceee47c9f0e32a80a23f4c419af1271d35b29522d91b6630812bb362a2995` | match |
| runtime manifest | `d0512fc90729d2537b57dd43734c7f5e360cd88f1a419893c0fba5e630089bb4` | match |
| raw evidence manifest | `ff8c42ac0fbfbf158b5a74eff5923287f87845625297ce6dd42ad6fd49b1f944` | match |

## Independent replay path

`v9_3_core0a_rebuild_second_audit.py` imports no project module. It does not
import the producer, bundled oracles/aggregator, report builder, mutation
harness, producer summary, or mutation `detected` field. Its flow is raw
CSV/JSON -> encoding/hash/PK/FK checks -> independent formulas/tick state machine
-> counts -> gate decision. Missing replay inputs produce `UNCONFIRMED`, never a
synthetic zero.

ZIP/raw integrity results:

- 87 unique regular ZIP members; no duplicate, absolute, traversal, symlink, or
  non-UTF-8 member; CRC, newline, canonical PK order, member hashes, raw manifest
  hashes/counts, and five raw-table FK relations pass.
- all 17 required raw evidence tables exist and are nonempty; all raw build
  identities equal the recorded build identity.
- fresh read-only extraction replays successfully.

## Eleven-gate comparison

The complete machine-readable comparison is
`v9_3_core0a_rebuild_second_independent_replay.json`.

| Gate | Reported | Bundled replay | Second audit | Difference |
|---|---:|---:|---:|---|
| full w/q/h scan | PASS, 102/0 | PASS, 102/0 | **UNCONFIRMED** | injected envelope callback identities/definitions and case labels absent |
| exhaustive envelope | PASS, 160/0 | PASS, 160/0 | PASS, 160/0 | none |
| random envelope | PASS, 50,000/0 | PASS, 50,000/0 | PASS, 50,000/0 | none |
| processor direct scan | PASS, 10,105/0 | PASS, 10,105/0 | PASS, 10,105/0 | none |
| finite state | PASS, 14 complete/0 | PASS, 14 complete/0 | PASS, 14 complete/0 | none |
| event order | PASS, 6/0 | PASS, 6/0 | **UNCONFIRMED** | raw rows omit `M`; actual-event tamper survives bundled replay |
| joint certification | PASS, 17/0 | PASS, 17/0 | **UNCONFIRMED** | API inputs/result/assertions absent |
| LOC dominance | PASS, 200/0 | PASS, 200/0 | PASS, 200/0 | none |
| service curve | PASS, 15/0 | PASS, 15/0 | PASS, 15/0 | none |
| schema/lineage/state | PASS, 53/0/0/0 | PASS, 53/0/0/0 | **UNCONFIRMED** | state and cycle zeros are vacuous |
| non-vacuity | PASS, 14/379/5/200/25 | same | PASS, same | none |

### P0 executable counterexample

One `scheduler_event_order_traces.csv.actual_event` was changed to
`{"tampered":true}` while `assertion_passed=true` was left unchanged. After
refreshing that member's raw-manifest hash, the bundled
`core0a_v9_3_independent_aggregator.py` returned exit 0, overall `PASSED`, and
event-order `N_failures=0`. It only counts `assertion_passed`; it neither compares
expected/actual events nor replays tick semantics. This directly violates the
required audit mutation.

## Service-curve production entry audit

Independent legal cases `[0]`, `[0,0]`, `[0,1,1]`, `[0,1,2,4]`, exact Fraction,
and scaled integers all pass. Illegal beta(0), decreasing, negative, float,
bool, NaN, Inf, nondeterministic callback, throwing callback, and incomplete
horizon all reject directly and fail closed through closure and taskset paths.

Spy call counts prove execution, rather than infer it from call graphs:

| Entry | Calls to `validate_service_curve_v9_3` |
|---|---:|
| single-task closure | 1 |
| taskset analyzer | 2 |
| runner | 3 |
| finite-state checker | 37 |
| formal microcase entry | 27 |

No `invalid -= 1`, clamping, failed-row filtering, or catch-and-continue pattern
was found in the relevant service path.

## Workload, processor, and envelope

- Workload: 35,805 unique cases and 61,775 unique monotonicity checks; independent
  formula mismatches 0 and monotonicity violations 0. Raw coverage includes
  1,155 `L=0`, 8,680 `theta=C`, 8,680 `theta=D`, and 8,680 `D=T` rows. A separate
  large-integer boundary probe through `L=10^18` also matches the definition.
- Processor: all 10,105 rows independently replay (105 exhaustive, 10,000 random),
  mismatches 0. The reference implements workload, truncation, per-hp effective
  workload, full `d` scan, and sum-min directly. It has no production imports;
  removing production truncation changes production behavior but cannot change
  the reference. The frozen theory defines the **maximum** satisfying `d`; the
  audit request's phrase "minimum satisfying delay" conflicts with the frozen
  definition (whose minimum would always be zero), so the frozen maximum was
  audited without changing theory.
- Envelope: 100,320 rows = 160 exhaustive + 50,000 random semantic inputs, each
  with distinct complete/local rows; mismatches 0. hp is nonempty in 66,992
  rows, lp in 66,962, `M=1` in 33,134, `M>1` in 67,186, and capacity saturation
  is feasible in 62,855 rows. Both `y_k=0` and `y_k>0` are legal in all rows;
  693 paired inputs have different complete/local values, disproving a simple
  result-column copy. Deleting production `y_k P_k` is detected while the brute
  oracle remains unaffected.

## w/h/q and event traces

The raw event rows contain 102 task IDs and observed unique visits
`w=106`, `h=113`, `q=165`. Expected visits cannot be independently derived for
the two injected-callback cases because neither callback definition nor identity
is recorded. Consequently omissions, premature stop, and restart correctness
cannot be confirmed from raw rows. Event-order contains seven tick rows over six
IDs, with expected/actual subsets currently equal, but `M` is absent and the
actual-event mutation survives the bundled aggregator.

## Joint certification cases

There are 17 distinct IDs and the stored expected/actual scalar statuses agree.
However, rows contain no tasks, source/dependency inputs, scripted solver
outcomes, actual result object, per-task prefix/finalization records, or assertion
details. `production_api_used=true` and `passed=true` are producer assertions.
The rows also do not independently establish the requested diagnostic and
unknown-core-status cases. The joint-state gate is therefore not replayable.

## Dominance

All 200 semantic taskset hashes and source/local vector hashes were recomputed;
unique=200, duplicates=0, comparisons=200, violations=0. Ten deterministic random
rows were rerun through the real source and LOC APIs; mismatches=0. All 200
tasksets contain one task. Their relevant task parameters differ, so this is not
request-ID-only uniqueness, but the resulting source/local vectors are all the
same one-task candidate and provide weak stress of multi-task dominance.

## Finite state and positive E0

An independent state machine replayed all 14 tasksets and all 336 ticks without
importing the producer transition model. It reconstructed all 82 jobs, release
energies, completion boundaries, response times, processor blocking (5), energy
blocking (379), 82 certificates, 25 executed bound checks, and zero certified
bound violations/inconclusive/internal errors.

Positive-E0 independent count is 25 distinct jobs. All have a certificate,
`E0>0`, release energy at least E0, joint certification, and a completed bound
check. Independent mutations deleting a certificate, lowering release energy,
making certification provisional, deleting a bound check, and changing the
reported count to zero were all rejected.

## Mutation authenticity

The packaged 15 rows have distinct original/mutated hashes, nonzero exit codes,
and restored hashes equal to originals. They are not sufficient to verify the
failure cause because the schema omits argv, stdout/stderr, and the failing
assertion. The catalog also has no release-certificate deletion/tamper mutation;
it has a summary-count mutation and a bound-check deletion instead.

Nine mutations were therefore independently rerun: beta(0), monotonicity,
processor truncation, `y_k P_k`, and the five positive-E0 mutations above. All
hit real targets, failed with relevant assertions/data invariants, did not fail
from syntax/import errors, and restored original source hashes. This does not
repair the packaged event mutation survivor.

## Lineage 53

The 53 rows are exactly:

- 16 `PK_UNIQUENESS`;
- 16 `INPUT_HASH_COVERAGE`;
- 16 `BUILD_IDENTITY_COVERAGE`;
- 5 `FK_INTEGRITY`.

There are zero rows for request accounting, execution state, downstream failure,
dependency DAG/cycle, analysis/source identity, task-result hash, failure
provenance, theory/contract/build binding, or canonical column order. Thus
`N_state_transition_violations=0` and `N_cycle_violations=0` are produced by
empty sums, not checks. The frozen full-result validator separately validates
the 23 contract tables, but that does not make the raw lineage gate's 53 rows
complete.

## Validators

Fresh extracted absolute paths were used:

| Validator | Command scope | Exit | Errors/warnings |
|---|---|---:|---:|
| full result | `ASAP_BLOCK_result_validator_v1_3_12.py --profile CORE0A <root>` | 0 | 0/0 |
| contract artifact | `ASAP_BLOCK_artifact_validator_v1_3_12.py <contract-root>` | 0 | 0/0 |
| acceptance | `ASAP_BLOCK_acceptance_report_validator_v1_3_12.py <report> --formal-contract <formal>` | 0 | 0/0 |
| CORE-0A package | `core0a_v9_3_package_validator.py <root> --zip <zip>` | 0 | 0/0 |
| ZIP integrity | Python `zipfile.testzip()` plus independent member checks | 0 | 0/0 |
| read-only package replay | package validator from chmod read-only extraction | 0 | 0/0 |

The full validator is not schema-only and reports the CORE0A complete-package
scope. The report keeps every CORE-0B gate `NOT_CHECKED`, overall release
`FAILED`, and `release_authorized=false`. These validator passes do not detect
the separate raw-gate P0 above.

## Build identity

Removing `build_identity_hash` and hashing the sorted canonical JSON with domain
`CORE0A:BUILD_IDENTITY:v1\0` recomputes
`b420d0244d8409e6e92dd856d35722df39bd2238201fb60cd9479049f3931662`.
The preimage binds commit A, clean-tree=true, relevant source hashes, Python
identity, theory, and contract; it does not bind B, an absolute path, time, or
dict insertion order. Replacing one production source hash changes the identity
to `a84f84d09f462e317a5de3936f4fd34ad519a2067c6c47876cf91709c934379a`.

## Old-evidence invalidation pointers

`current_authoritative.json` and the raw evidence manifest record old commit
`dcb55f6a...`, old ZIP `d56c2f...`, and `INVALIDATED`; the authoritative ZIP path
points only to the rebuild, and no ambiguous `latest` selector was found.
However:

- runtime `manifest.json` does not record the old commit/ZIP/status;
- the rebuild report says only "prior CORE-0A remains INVALIDATED" and omits the
  old commit and ZIP identifiers;
- `current_authoritative.json.evidence_commit` is the placeholder
  `CONTAINING_COMMIT_B`, not commit B's SHA.

## Three-environment determinism

Two clean local clones at commit A independently reran all 15 mutations, raw
generation, aggregation, package build, and ZIP build. They used different cwd,
`PYTHONHASHSEED` (271/991), and locale (`C`/`en_US.utf8`). A third fresh
chmod-read-only extraction used `C.UTF-8` and replayed validators.

- `N_environments=3`
- `N_files_compared=87`
- `N_raw_files=17`
- `N_raw_differences=0`
- `N_package_differences=0`
- `N_gate_differences=0`
- `N_zip_differences=0`

Both regenerated ZIPs are byte-identical to each other and authoritative SHA
`a51ceee...`. The stored determinism report's conclusion is correct, though its
JSON omits the requested explicit `N_*` summary fields.

## Regression

| Scope | Result | Time |
|---|---:|---:|
| v9.3 core | 24 passed | 6.09 s |
| v9.3 taskset | 47 passed | 0.94 s |
| runner/schema | 11 passed | 5.31 s |
| microcases | 35 passed | 63.80 s |
| rebuilt CORE-0A tests | 5 passed | 4.01 s |
| all RTA tests | 274 passed | 22.84 s |
| full repository | 700 passed, 62 skipped, 32 warnings | 136.68 s |
| targeted py_compile | passed | <1 s |

The full count exactly matches the producer report's `700 passed, 62 skipped`;
the 32 warnings are expected legacy-provenance RuntimeWarnings in analysis tests.

## Findings and authorization

### P0

1. Event-order actual-event mutation survives bundled aggregation.
2. Full w/h/q injected-callback cases lack enough raw input to derive expected
   visits.
3. Joint certification raw cases are producer assertions, not replayable API
   cases.
4. Lineage state/cycle zeros are vacuous; required check classes are absent.

### P1

1. Mutation rows omit commands/transcripts/failing assertions and the required
   positive-E0 certificate mutation.
2. Old-evidence invalidation is incomplete in runtime manifest/report, and the
   current pointer uses a commit placeholder.
3. Event rows omit processors, preventing raw semantic replay.

### P2

1. Stored determinism JSON omits the requested explicit `N_*` fields although
   independent regeneration confirms zero differences.
2. The audit request's processor "minimum" wording conflicts with the frozen
   theory's maximum-satisfying definition; this audit followed the frozen theory.

Final state:

- rebuilt CORE-0A independently confirmed PASS: **no**
- CORE-0A formal PASS restored: **no**
- audit commit SHA: **none (failure report only)**
- authoritative ZIP SHA-256: `a51ceee47c9f0e32a80a23f4c419af1271d35b29522d91b6630812bb362a2995`
- pilot allowed: **no**
