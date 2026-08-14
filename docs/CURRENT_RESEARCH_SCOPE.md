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
- B4-PE: deterministic direct taskset/source materialization, bounded
  execution with resume/retry, schema-3 observability, analysis, and
  statistics validation. The nine-scheduler grid and energy protocol are
  unchanged.
- The nine simulator schedulers and their energy bridge/runtime tests.

## Current entrypoints

```text
scripts/run_b4_priority_energy.py
scripts/analyze_b4_priority_energy.py
experiments/b4_priority_energy/experiment.py
experiments/v9_3/rta4_formal_config_v5.py
experiments/v9_3/rta4_formal_plan_v5.py
experiments/v9_3/rta4_formal_runner_v5.py
experiments/v9_3/rta4_local_execution_v5.py
```

The direct B4 runner has no authorization, freeze, pilot-admission,
manifest, protocol-resolution, or publication-governance dependency. Its
`--plan` path constructs the complete request grid directly from the frozen
scientific constants.

## Build and validation

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON
cmake --build build -j2
```

The current validation scope is the selected RTA CW/LOC/PH/SEQ, RTA4 V5,
direct B4-PE, and simulator scheduler/energy-bridge tests. Historical
artifacts and retired experiment suites are intentionally excluded.
