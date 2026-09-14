# PARTsim

PARTsim is the research simulator for ASAP-BLOCK power-aware real-time
scheduling.

Current scope:

- the nine scheduler implementations: ASAP-BLOCK, ASAP-NONBLOCK,
  ASAP-SYNC, ALAP-BLOCK, ALAP-NONBLOCK, ALAP-SYNC, ST-BLOCK,
  ST-NONBLOCK, and ST-SYNC;
- the direct B4-PE unified task-family experiment with deterministic tasksets,
  exact energy-source materialization, and simulator observability summaries;
- the current CW, LOC, PH, and SEQ RTA methods, including fixed-D ablations;
- five current RTA experiments: comparison, structural ablation,
  RTA--simulation audit, E0/service/power/deadline sensitivity, and
  algorithm/worker scalability.

## Build

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
cmake --build build -j2
```

The simulator entrypoint is `build/rtsim/rtsim`. The current B4 direct
experiment entrypoints are:

```bash
PARTSIM_RTSIM_BIN=build/rtsim/rtsim \
LD_LIBRARY_PATH=build/rtsim/cmdarg:build/libmetasim:build/librtsim \
python3 scripts/run_b4_priority_energy.py --plan

PARTSIM_RTSIM_BIN=build/rtsim/rtsim \
LD_LIBRARY_PATH=build/rtsim/cmdarg:build/libmetasim:build/librtsim \
python3 scripts/run_b4_priority_energy.py --smoke --output /tmp/b4-smoke

python3 scripts/analyze_b4_priority_energy.py --input /tmp/b4-smoke
```

The current RTA entrypoint is:

```bash
python3 -m experiments.v9_3.rta4_formal_runner_v5 \
  --campaign PATH_TO_CURRENT_V5_CAMPAIGN \
  --preflight-only
```

The optional Experiment-A implicit-deadline extension is selected explicitly:

```bash
python3 scripts/run_scheduler_load_cross.py --output OUTPUT --seed 20260906 \
  --experiment-version a-implicit \
  --campaign a-implicit-uc-fixed-supply --samples-per-cell 120

python3 scripts/analyze_scheduler_load_cross.py --input OUTPUT
```

It uses implicit deadlines (`D=T`), with RM as the canonical run because
RM=DM for implicit deadlines.  Formal A-implicit runs use the compact
hard-real-time WholePass path by default and do not retain a semantic trace;
the analyzer therefore publishes Whole-taskset pass CSV/figures only.  DMR
is explicitly unavailable for this fast-path output and is never fabricated.

`a-implicit` is the canonical V2 contract: both standardized scan axes are
exactly 0.1 through 0.9, with 27 cells per campaign. The earlier 24-cell UC
and 30-cell UE formal roots remain readable as V1 data via
`--experiment-version a-implicit-v1`. The only new formal simulation slice
is UC=0.9 for the fixed-supply campaign:

```bash
python3 scripts/run_a_implicit_uc09_supplement.py \
  --output UC09_OUTPUT --seed 20260906 --samples-per-cell 120 \
  --workers 30 --prepare-workers 30 --parse-concurrency 30

python3 scripts/stitch_a_implicit_standardized.py \
  --legacy-uc-root LEGACY_UC_OUTPUT \
  --legacy-ue-root LEGACY_UE_OUTPUT \
  --uc09-supplement-root UC09_OUTPUT \
  --output STANDARDIZED_OUTPUT
```

The stitcher validates all source identities and scientific invariants before
writing composite outputs. Legacy UE=1.0 rows remain in the source root but
are excluded from the standardized composite. The composite is WholePass-only
and does not manufacture DMR values.

## Tests

Run the current target suite with `python3 -m pytest` and the selected RTA,
B4, and scheduler nodes. The complete target definition and repository scope
are documented in `docs/CURRENT_RESEARCH_SCOPE.md`.
