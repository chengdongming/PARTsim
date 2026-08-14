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

## Tests

Run the current target suite with `python3 -m pytest` and the selected RTA,
B4, and scheduler nodes. The complete target definition and repository scope
are documented in `docs/CURRENT_RESEARCH_SCOPE.md`.
