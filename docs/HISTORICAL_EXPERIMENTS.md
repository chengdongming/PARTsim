# Historical experiment and compatibility index

The pre-cleanup source is preserved on
[archive/pre-cleanup-20261128](https://github.com/chengdongming/PARTsim/tree/archive/pre-cleanup-20261128)
(commit `a5218432dccac3a3e61623b758337d99ba74a712`).
Check out that branch for the full historical layout. Do not mix the
historical debit-before-harvest results with new same-slot results.

| Historical route | Status on current branch |
|---|---|
| V7.2 unlimited-capacity reproduction | Preserved intact, including inputs and validators; same-slot analysis directly reuses its optimized kernel |
| RTA4 CORE-1–5, CW/LOC/PH/SEQ | Compatibility sources and old entrypoints retained at original paths pending full legacy regression and migration |
| B4-PE | Direct historic runner and schema3 analysis retained; same-slot mode is not silently substituted |
| PERF-G, SENS-SMALL, RTA-LOAD-CROSS, priority-energy/deadline sensitivity | Historic runners retained; taskset generation and related shared modules have current callers |
| A-implicit V1 and UC09/legacy WholePass stitching | Historical only; retained for original result reproduction and existing tests |
| Top-level AN/AB joint proof snapshots (older debit-before-harvest models) | Archived in the pre-cleanup branch; not the frozen optimized kernel inside `experiments/asapblock_unlimited/` |
| Retired v21/v1.3.12 standalone runner and schema binding, orphan formal grid and validation module | Archived; the v1.3.12 contract directory is missing and these files have no retained callers |
| Old CORE-5 execution document referring to removed files | Archived |

The legacy scripts are no longer recommended as entrypoints for current
research. They are deliberately left runnable where they have regression
tests or shared dependency paths. This avoids breaking a valid historical
workflow or changing stored source hashes.

## Legacy commands (for reproduction only)

- `scripts/run_b4_priority_energy.py`, `scripts/analyze_b4_priority_energy.py`
- `python -m experiments.v9_3.rta4_formal_runner_v5 --campaign FILE --preflight-only`
- `scripts/run_v9_3_perf_g.py`, `scripts/analyze_v9_3_perf_g.py`
- `scripts/run_v9_3_sens_small.py`, `scripts/analyze_v9_3_sens_small.py`
- `scripts/run_rta_load_cross.py`, `scripts/run_rta_load_uc_fixed_scale.py`
- `scripts/run_scheduler_priority_energy_load_cross.py`, `scripts/run_deadline_profile_sensitivity.py`
- `scripts/run_a_implicit_uc09_supplement.py`, `scripts/stitch_a_implicit_standardized.py`,
  `scripts/run_v6_implicit_wholepass_fast.py`

Their original instructions and source files are on the archived branch.
The native default energy timing and old CLI defaults remain unchanged.
The current nine-strategy wrapper selects new timing explicitly.

## Root layout archive

The source immediately before the root-file relocation is preserved at
[archive/pre-root-layout-20261128](https://github.com/chengdongming/PARTsim/tree/archive/pre-root-layout-20261128)
(`a5018b0affb9707b6405379e8c9a47f86c48c620`). The older proof and B4 freeze
documents that remain hash-validated have been moved to
`experiments/legacy_contracts/` without changing their bytes. The old
schema/dictionary and test-only RTA4 configurations are under
`test/fixtures/`. Unreferenced outdated theory and V4 experiment
documentation remain available in the archive. The latest source records
actual moved paths in new experiment code-hash manifests.
