# v9.3 EXT-1 execution contract

EXT-1 performs a paired comparison of the nine source-audited energy-aware GPFP schedulers. It is an empirical, finite-horizon simulation experiment; `SIM_PASS_OBSERVED` is not a proof of schedulability.

The registry is read from `librtsim/system.cpp` and checked against each implementation source before planning. The registered IDs are `gpfp_asap_block`, `gpfp_asap_nonblock`, `gpfp_asap_sync`, `gpfp_alap_block`, `gpfp_alap_nonblock`, `gpfp_alap_sync`, `gpfp_st_block`, `gpfp_st_nonblock`, and `gpfp_st_sync`.

For each paired instance the canonical taskset, taskset seed/hash, harvesting trace/hash, processor count, initial battery, battery capacity, horizon, deadline semantics, and power vector are fixed. Scheduler ID is absent from taskset and trace seed material. The runner rejects a group unless all nine scheduler IDs occur exactly once and every fairness field agrees.

Run the bounded smoke with:

```bash
python3 scripts/run_v9_3_ext1.py --config configs/v9_3_ext1_smoke.yaml
python3 scripts/run_v9_3_ext1.py --config configs/v9_3_ext1_smoke.yaml --resume
python3 scripts/analyze_v9_3_ext1.py --output-root artifacts/v9_3_ext1_smoke
(cd artifacts/v9_3_ext1_smoke && sha256sum -c file_hashes.sha256)
```

The smoke is fixed to one `M=4`, `task_n=10`, constrained-deadline, `U=0.2`, `E0=1` cell, two canonical tasksets, nine schedulers, and 18 bounded simulations. It is not a formal paper grid.

Terminal status is one of `SIM_PASS_OBSERVED`, `SIM_DEADLINE_MISS`, `SIM_HORIZON_INSUFFICIENT`, `SIM_RUNTIME_TIMEOUT`, or `SIM_INTERNAL_ERROR`. A timeout or scheduler error is never converted into a deadline miss, and insufficient horizon is never counted as pass. Metrics unsupported by trace schema v2 are emitted as `UNAVAILABLE`, not zero. Aggregation reports requested, terminal, valid-terminal, and sufficiently-observed denominators separately and preserves taskset-level pairing in every scheduler comparison.
