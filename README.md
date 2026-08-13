# PARTsim

PARTsim is the research simulator for ASAP-BLOCK power-aware real-time
scheduling.

Current scope:

- the nine scheduler implementations: ASAP-BLOCK, ASAP-NONBLOCK,
  ASAP-SYNC, ALAP-BLOCK, ALAP-NONBLOCK, ALAP-SYNC, ST-BLOCK,
  ST-NONBLOCK, and ST-SYNC;
- the B4-PE unified task-family experiment with exact energy-source and
  observability contracts;
- the current CW, LOC, PH, and SEQ RTA methods, including fixed-D ablations;
- five current RTA experiments: comparison, structural ablation,
  RTA--simulation audit, E0/service/power/deadline sensitivity, and
  algorithm/worker scalability.

## Build

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
cmake --build build -j2
```

The simulator entrypoint is `build/rtsim/rtsim`. The current B4 preflight
entrypoint is:

```bash
python3 -m experiments.b4_priority_energy.generate_manifest_v5 \
  --config configs/b4_pe_exact_service_v5_example_UNAUTHORIZED.yaml \
  --preflight-only
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
