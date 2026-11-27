# PARTsim

Research simulator for global fixed-priority scheduling on multicore systems
with a shared energy-harvesting battery.

## Current research experiments

New experiments use **harvest-before-debit (same-slot)** energy timing; the
current slot's harvested energy can support execution in that slot. All
algorithms retain their original scheduling rules. Static power is not
modeled separately.

| Experiment | Entry point |
|---|---|
| Nine scheduling strategies | [scheduler comparison](experiments/scheduler_comparison/README.md): `scripts/run_scheduler_comparison.py` |
| ASAP-BLOCK response-time analysis (unlimited capacity) | [same-slot RTA](experiments/same_slot/README.md): `scripts/run_same_slot_rta.py` and the paired `scripts/run_same_slot_rta_campaign.py` |
| HEARTS-1C vs global AB/AN; partitioned vs global AB | [HEARTS comparison](experiments/hearts_1c/README.md): `scripts/run_hearts_1c_comparison.py` |

See [current research scope](docs/CURRENT_RESEARCH_SCOPE.md) for the dependency
map and validation commands. The [historical experiment index](docs/HISTORICAL_EXPERIMENTS.md)
documents the archived and retained compatibility routes.

## Build

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
cmake --build build -j2
```

The simulator entrypoint is `build/rtsim/rtsim`. For direct simulator calls
pass `--energy-timing same-slot`; the old implicit CLI default is preserved
solely for historical reproducibility. New comparison entrypoints select
same-slot explicitly. Outputs belong in fresh directories; do not combine
old-model and new-model results.

## Regression tests

```bash
PARTSIM_TEST_BINARY=build/rtsim/rtsim python -m unittest discover -s test -p test_same_slot_energy.py
PARTSIM_TEST_BINARY=build/rtsim/rtsim python -m unittest discover -s test -p test_hearts_1c_comparison.py
python -m unittest discover -s test -p test_current_research_campaigns.py
python -m unittest discover -s test -p test_repository_layout.py
python experiments/asapblock_unlimited/test_reproduction.py
```

Run the retained pytest regression tests with `python -m pytest -q test`
and build with `-DBUILD_TESTING=ON` for native GoogleTest coverage.
Full retired experiment suites reside on the archive branch, not master.
