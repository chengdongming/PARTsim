# ASAP-BLOCK v9.3 AutoDL deployment

This directory builds the production simulator, runs bounded smoke requests in
fresh output directories, resumes interrupted formal runs without deleting
checkpoints, verifies hashes/cardinalities, and packages only the selected
configs, logs, commit identity, and experiment results.

Quick start:

```bash
cp deployment/autodl/experiment.env.example experiment.env
# Edit paths/resources, then export the file's variables.
set -a; source experiment.env; set +a
bash deployment/autodl/setup_environment.sh
bash deployment/autodl/verify_environment.sh
bash deployment/autodl/build_simulator.sh
bash deployment/autodl/run_all_smoke.sh
```

Use a new `PARTSIM_OUTPUT_ROOT` for a fresh rehearsal. To continue an
interrupted smoke set, export `PARTSIM_ACTION=resume` and rerun
`run_all_smoke.sh`. Individual `run_core*.sh` and `run_ext*.sh` entrypoints use
the same controls.

Formal execution is deliberately locked. It requires
`PARTSIM_FORMAL_CONFIRM=RUN_V9_3_FORMAL` plus every audited formal config path.
`resume_formal_all.sh` preserves the same output/config identity and supplies
`--resume`. EXT-2 always refuses formal mode while its status is
`REAL_TRACE_DATA_UNAVAILABLE`; only its labeled synthetic fixture smoke is
allowed.

`monitor_progress.sh` reads checkpoints without changing them.
`verify_results.sh` checks requested/terminal closure, duplicate IDs, and every
`file_hashes.sha256`. `package_results.sh` verifies first and excludes unrelated
worktree files by copying only the deployment directory and selected output
root. For a separately invoked formal verify/package command, export
`PARTSIM_RUN_MODE=formal`; smoke is the default and requires all eight outputs.
