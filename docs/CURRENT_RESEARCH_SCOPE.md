# Current PARTsim Research Scope

This repository contains the current reproducible scope of the PARTsim
ASAP-BLOCK research simulator. Historical experiment outputs, superseded
protocols, and retired compatibility pipelines are not part of the current
source distribution.

## Current scientific scope

- ASAP-BLOCK RTA methods: CW, LOC, PH, and SEQ, including exact-energy
  support and the current task-set adapters.
- Five current RTA4 experiments: CORE-1, CORE-2, CORE-3 V7, CORE-4, and
  CORE-5A/CORE-5B worker variants.
- B4-PE V5: selectable energy-source manifests, exact service material,
  admission, materialization, execution/resume/retry, observability,
  analysis, statistics, and audit validation.
- The nine simulator schedulers and their energy bridge/runtime tests.

## Current entrypoints

```text
experiments/b4_priority_energy/manifest_v5.py
experiments/b4_priority_energy/generate_manifest_v5.py
experiments/b4_priority_energy/energy_source_v5.py
experiments/b4_priority_energy/materialize_manifest.py
experiments/b4_priority_energy/execute_manifest_v4.py
experiments/b4_priority_energy/extract_analysis.py
experiments/b4_priority_energy/run_statistics.py
experiments/v9_3/rta4_formal_config_v5.py
experiments/v9_3/rta4_formal_plan_v5.py
experiments/v9_3/rta4_formal_runner_v5.py
experiments/v9_3/rta4_local_execution_v5.py
```

`manifest_protocol_v4.json` remains a runtime compatibility input of the
current B4 V5 manifest/materialization path. It is retained deliberately;
the presence of the legacy protocol name does not mean that the retired B4
experiment implementations are retained.

## Build and validation

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON
cmake --build build -j2
```

The current validation scope is the selected RTA CW/LOC/PH/SEQ, RTA4 V5,
B4-PE V5, and simulator scheduler/energy-bridge tests. Historical artifacts
and retired experiment suites are intentionally excluded.
