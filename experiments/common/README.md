# Common experiment dependencies

The shared exact service curve is implemented here. Existing generation
and execution modules are intentionally reused at their stable paths:

| Responsibility | Existing implementation |
|---|---|
| Taskset sampling and canonical generation | `global_task_generator.py`, `experiments/v9_3/perf_g.py`, `taskset_store.py`, `cell_model.py` |
| Energy materialization and exact arithmetic | `experiments/v9_3/config.py`, `exact_energy.py`, `scheduler_load_cross.py` |
| Native simulation and trace interpretation | `experiments/v9_3/simulation_engine.py`, `simulation_result.py` |
| Same-slot RTA accounting engine | frozen optimized copy in `experiments/asapblock_unlimited/runtime/reproduction/optimized/` |

Prefer importing the existing shared implementation over creating copies.
The historical V7.2 source fingerprints and prior HEARTS-1C code hashes
must remain reproducible. Moving these files would require a separate,
fully tested dependency/fingerprint migration, not just a directory rename.
