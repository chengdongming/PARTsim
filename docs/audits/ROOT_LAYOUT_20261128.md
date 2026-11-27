# Root layout relocation — 2026-11-28

Baseline: master `a5018b0affb9707b6405379e8c9a47f86c48c620`,
preserved in remote branch `archive/pre-root-layout-20261128`.

## Changes

- Moved all 12 root Python/YAML files, without changing their filenames:
  six RTA modules to `experiments/rta/`, three common modules to
  `experiments/common/`, the acceptance CLI to `scripts/`, and two
  system templates to `configs/templates/`.
- Removed two obsolete unreferenced docs (old RTA and RTA4 V4).
- Moved the original hash-validated theory and B4 freeze documents to
  `experiments/legacy_contracts/`; retained their bytes.
- Moved the old schema/dictionary and seven historical test-only RTA4
  configuration files to `test/fixtures/`. They remain test inputs,
  not active research materials.
- Updated package imports, native embedded-Python module names, physical
  YAML paths, tests, and documentation. The historical template names
  remain logical identity values, resolved to the new files.
- No scheduling mathematics, RTA mathematics, frozen V7.2 runtime,
  generation policy or original experiment data was modified. File
  relocation changes source-code hashes/physical paths. Earlier formal
  results are reproducible at their archived source commit.

## Validation

| Check | Result |
|---|---|
| Rebuild native simulator, all 92 C++ source translation units | PASS |
| Native/Python same-slot energy suite | 7/7 |
| HEARTS-1C native/reference suite | 16/16 |
| Current RTA campaign suite | 6/6 |
| Frozen V7.2 reproduction | 11/11 |
| Legacy RTA v9.3 unittest suite | 24/24 |
| Repository layout and legacy template lookup | 3/3 |
| B4 direct plan and reproducibility checks | 3 pass; pre-existing frozen payload hash failure unchanged |
| Legacy CORE-1/3 exact-service preflight | both passed |
| Before/after HEARTS reference comparison | ten scientific rows and paired CSV identical; only execution times/paths differ |
| Before/after native nine-strategy comparison | eighteen scientific rows identical except wall runtime; summary and invariant report identical |
| Frozen numeric-theory document validation | original SHA-256 passes |

The environment has no CMake, GoogleTest or pytest. The native binary was
compiled directly using the CMake source lists and exercised by native
Python integration tests. The full separate GoogleTest and pytest
legacy suites were not run. The old B4 governance loader also refers to
an already missing `test_protocol_resolution.py` before relocation:
the identical missing dependency was checked on the baseline tree.
It is not a new import regression, and further removal of that historical
pipeline is outside this relocation. The active B4 direct plan works.

Version-bearing filenames were deliberately kept unchanged. No full
formal experiment was rerun.
