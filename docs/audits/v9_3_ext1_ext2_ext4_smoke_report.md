# ASAP-BLOCK v9.3 EXT-1 / EXT-2 / EXT-4 smoke report

## Scope and provenance

- Baseline: `bcc2fc6dbb18489aca444403c7f622721aadc19f`.
- Branch: `experiment-v9.3-ext1-ext2-ext4`.
- Scope: production experiment framework, tests, documentation, and bounded smoke only.
- No formal-scale experiment was run, no publication parameter was frozen, and no paper conclusion is supported by these smoke results.
- EXT-3 finite-battery RTA is not implemented or claimed.

## EXT-1 scheduler comparison

The registry is derived from the C++ factory implementation and contains the nine canonical scheduler IDs:

| Family | BLOCK | NONBLOCK | SYNC |
| --- | --- | --- | --- |
| ASAP | `gpfp_asap_block` | `gpfp_asap_nonblock` | `gpfp_asap_sync` |
| ALAP | `gpfp_alap_block` | `gpfp_alap_nonblock` | `gpfp_alap_sync` |
| ST | `gpfp_st_block` | `gpfp_st_nonblock` | `gpfp_st_sync` |

For each taskset-energy instance, all nine requests share the canonical taskset, release pattern, trace, system template, initial battery, capacity, horizon, deadline semantics, power vectors, and non-scheduler seed material. The request validator verifies identical taskset, trace, and comparison-input hashes and permits only the scheduler ID and scheduler-bearing simulator configuration to differ. Scheduler identity does not enter taskset or trace seed derivation.

Bounded smoke results in `artifacts/v9_3_ext1_smoke`:

- 2 canonical taskset-energy instances × 9 schedulers = 18 requests.
- 18 unique terminal results; all are `SIM_PASS_OBSERVED`.
- 72 unordered taskset-level scheduler-pair rows and 9 scheduler summary rows.
- All comparison instances have one taskset hash, one trace hash, and one scheduler-neutral comparison-input hash across their nine requests.
- Resume retained 18 attempts and 18 terminal results; it did not append duplicate completed work.
- Metrics not emitted reliably by the simulator are recorded as `UNAVAILABLE`, never fabricated as zero.
- These tiny all-pass observations are pipeline checks, not scheduler-performance evidence.

## EXT-2 trace inventory and synthetic smoke

The repository inventory is recorded in `docs/audits/v9_3_ext2_trace_inventory.md` and the smoke artifact `trace_inventory.csv`:

| Repository object | Physical/time interpretation | Publication status |
| --- | --- | --- |
| NASA POWER hourly CSV | hourly LST, `ALLSKY_SFC_SW_DWN` in Wh/m²; `-999` missing marker | `REAL_TRACE_DATA_UNAVAILABLE` |
| NASA POWER hourly JSON | same embedded source metadata and interval | `REAL_TRACE_DATA_UNAVAILABLE` |
| processed Shenyang minute CSV | header says W/m²; transformation and calendar lineage are not recorded | `REAL_TRACE_DATA_UNAVAILABLE` |

The raw files have embedded NASA POWER source metadata, but the repository does not contain a license/use/publication-rights record. The processed minute trace also lacks a reproducible transformation lineage. Consequently, none is admitted as paper-ready real data by the framework.

The loader requires explicit physical and time units, strictly increasing timestamps, finite non-negative values, an explicit missing-data policy, and exact rational scaling. It rejects ambiguous units, disorder, NaN/Inf, and negative values. Resampling integrates interval energy and uses exact rational arithmetic; exact subdivision is conservative by construction.

Bounded smoke results in `artifacts/v9_3_ext2_smoke`:

- Input is the explicitly labelled `SYNTHETIC_TEST_FIXTURE`, not real data.
- Exact scale factor 1: 420 J input, 420 J output, 0 J difference.
- Exact 120 s to 60 s subdivision: 420 J input, 420 J output, 0 J difference.
- Final trace hash: `039c201a3a2c58f5bfc1420907b7532715561f33ecc3e9756753f6ea9d3bf008`.
- 1 ASAP-BLOCK simulation request, 1 unique `SIM_PASS_OBSERVED` terminal result.
- Resume retained one attempt and one terminal result without duplication.

No certified service lower bound was constructed from the synthetic fixture. The explicit states are `NOT_CONSTRUCTED` and `NOT_APPLICABLE_NO_CERTIFIED_SERVICE_BOUND`; no EXT-3 or finite-battery RTA claim is made.

An initial preflight caught invalid flow-style YAML materialization before a simulation terminal could be accepted. The framework now preserves the source template's simulator vectors, a regression test covers the case, and the minimal preflight reproducer is retained under `artifacts/v9_3_ext2_smoke/failure_inputs/preflight_yaml_materialization`.

## EXT-4 robustness

The capability matrix exposes only repository-supported semantics:

- generator family: `UUNIFAST_DISCARD`;
- deadline modes: implicit and constrained;
- period-range configurations: supported, but a range change regenerates task parameters and is therefore stratified rather than paired;
- priority policy: `RM` only;
- power mode: `generator_default_heterogeneous` only.

DM, additional generator families, alternate power models, and priority transformations are marked unavailable; no new mathematical generator or priority semantics were invented. The constrained-deadline derived sample preserves IDs, C, T, and P while changing only D, with 70 unchanged-field checks passing. The period-range cell `[80, 400]` versus `[40, 200]` is labelled `UNPAIRED_STRATIFIED_COMPARISON` rather than a paired single-axis transformation.

Bounded smoke results in `artifacts/v9_3_ext4_smoke`:

- 3 samples, 6 RTA requests/terminals, and 3 ASAP-BLOCK simulation requests/terminals.
- RTA: 4 `COMPLETED` / `CERTIFIED_TASKSET`; 2 `TIMEOUT` / `NOT_CERTIFIED`.
- Simulation: 3 `SIM_PASS_OBSERVED`.
- Soundness: 4 `RTA_PASS_SIM_PASS`; 2 `RTA_FAIL_SIM_PASS`; no `RTA_PASS_SIM_FAIL`.
- Pairing: 2 paired deadline-mode ties and 2 explicitly unpaired period-range rows whose RTA side timed out.
- Resume retained 8 RTA attempt rows (including bounded timeout retries), 6 RTA terminals, and 3 simulation terminals without duplicate terminal results.

## Validation and severity

- Extension plus CORE-3 integration tests: 54 passed.
- Full Python suite: 852 passed, 62 skipped, 32 warnings.
- CMake build: passed.
- Nine-scheduler directed C++ suite: 133 passed from 12 suites.
- Full CTest: 163/166 passed. The three failures are the pre-existing `Scheduler.FIFO`, `Scheduler.TrueFIFO`, and `Scheduler.RM` queue-order tests; this branch does not modify their C++ implementation or tests.
- All Python files added or modified relative to the baseline pass `py_compile`.
- A repository-wide Python 3 `py_compile` additionally encounters two legacy files: Python-2-style `tools/about.py` and an existing indentation error in `taskgen.py`.
- `git diff --check`: passed.
- EXT-1, EXT-2, and EXT-4 `sha256sum -c file_hashes.sha256`: passed.

Final bounded-smoke artifact classification:

- P0: 0.
- P1: 0.
- P2: 2, both explicit EXT-4 RTA runtime timeouts; neither is converted to a deadline miss or a certification failure conclusion.

The extension code is ready for unified code review and deployment rehearsal. Formal execution remains gated on review, approved/frozen formal configurations, and—for EXT-2—an auditable real-trace dataset with publication rights and reproducible preprocessing provenance.
