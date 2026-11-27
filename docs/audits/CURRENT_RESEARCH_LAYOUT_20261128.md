# Current research layout cleanup — 2026-11-28

## Baseline and recovery

- Original master: `52800fcac9d788759c6590765a0411307f0b53cd`.
- Integrated same-slot/HEARTS source: `a5218432dccac3a3e61623b758337d99ba74a712`.
- Remote archive: `archive/pre-cleanup-20261128`, at that exact commit.
- PR #163 (same-slot timing) and PR #164 (HEARTS-1C) were merged first.
- Their resulting master tree is `05e887e0758eac4f225f22a59f011967f30a06c1`,
  byte-identical to the validated integrated source.

## Scope

Archived sixteen unreferenced retired entrypoint/schema/grid/validation files,
obsolete documentation and a standalone older AN/AB snapshot. The full
original tree remains recoverable on the archive branch. Retained historical
runners and shared modules are indexed in `docs/HISTORICAL_EXPERIMENTS.md`.
Do not remove their dependency or test paths without a separate migration.

Added the explicit same-slot nine-strategy entrypoint and the new paired RTA
campaign layer. The latter uses the existing solver and translated baseline,
not a new theorem. The original C++ sources, all nine scheduling policies,
`ab_rta.py`, HEARTS replay, shared taskset and energy-generation modules,
frozen V7.2 runtime and the existing experiment scripts are unchanged.

## Validation performed

| Check | Result |
|---|---|
| Fresh native build from all 92 translation units in the CMake source lists (GNU C++17) | PASS |
| Native/Python HEARTS tests | 16/16 PASS |
| Same-slot RTA and native simulation tests | 7/7 PASS |
| Frozen V7.2 reproduction tests | 11/11 PASS |
| New entrypoint/campaign tests | 6/6 PASS |
| Legacy v9.3 RTA unittest suite | 24/24 PASS |
| Direct B4 standalone checks | 3/4 PASS; existing golden payload assertion differs on all three trees |
| All tracked Python source syntax | PASS |
| Nine-strategy before/after smoke (two tasksets, 18 method rows) | All scientific rows identical excluding wall runtime; summary and invariant report byte-identical |
| HEARTS and partitioned before/after smoke (four paired tasksets, ten method rows) | All scientific rows identical excluding timing; task identities and paired CSV unchanged |
| Prior 2026-11-27 formal HEARTS and partitioned run recorded code hashes (eight per suite) | All match the retained files |
| RTA constructed and coverage bounded runs (ten method rows) | Certificates, outcomes and histories identical to original same-slot calls excluding runtime |

All regression runs used fresh output roots. No full scientific experiment
was rerun or historical data modified. The freshly compiled native sources
are identical before and after cleanup, so the same binary was used for
both comparisons.

The B4 golden payload test expects
`3f448f1265d3a84b708ac2939e3d12c1a7ce4437ea3300995c3600e0be38e8ff`.
The original master, integrated pre-cleanup tree and cleanup tree all
produce `2788e35f7d89e2c585b4198ae2770bb327d97e509989accf3739e853a329a4c2`
under this environment. The legacy test and generator were not changed;
the mismatch needs independent historical environment/payload investigation.

CMake, GoogleTest and pytest were not installed in this execution
environment. The production native components were compiled and linked
directly from the CMake source lists, and the binary-dependent Python
integration tests did run. The full separate GoogleTest and pytest suites
were not run; the review therefore does not assert that every historical
suite passes.

## RTA campaign scope

The frozen source inputs were regenerated and checked against their
original identity manifests. All five new plans validated:

| Suite | Cases | Requests |
|---|---:|---:|
| coverage | 1920 | 3840 |
| sensitivity | 720 | 1440 |
| flow | 576 | 1152 |
| scaling | 390 | 780 |
| constructed | 180 | 360 |
| total | 3786 | 7572 |

These are planned requests, not full-run results; only bounded execution
was validated. New-model ablation and WCRT/trace audit need separate
theoretical and model-specific validation. Historical ablations are not
relabeled as new results.
