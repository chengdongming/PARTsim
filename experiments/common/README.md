# Common experiment dependencies

| Responsibility | Existing implementation |
|---|---|
| Taskset sampling and canonical generation | `experiments/common/global_task_generator.py`, `experiments/v9_3/perf_g.py`, `taskset_store.py`, `cell_model.py` |
| Configuration and harvesting | `experiments/common/paths.py`, `energy_manager.py`, `solar_data_loader.py`, `configs/templates/` |
| Energy materialization and exact arithmetic | `experiments/v9_3/config.py`, `exact_energy.py`, `scheduler_load_cross.py` |
| Native simulation and trace interpretation | `experiments/v9_3/simulation_engine.py`, `simulation_result.py` |
| Retained legacy RTA interfaces | `experiments/rta/` |
| Same-slot RTA accounting engine | frozen optimized copy in `experiments/asapblock_unlimited/runtime/reproduction/optimized/` |

Prefer qualified package imports over duplicate root-level modules.
Historical experiment input identities retain logical template names and
the original numeric-contract document metadata; physical file paths are
resolved without changing the source documents or template bytes.
Historical code hashes refer to the archived commit, not to renamed paths.
