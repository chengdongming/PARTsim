# v9.3 EXT-4 execution contract

EXT-4 uses only source-audited capabilities. The current generator family is UUniFast-Discard; deadline modes are implicit and constrained with the existing full-range `generator_uniform_integer` rule; period bounds are configurable integer ranges; power has only `generator_default_heterogeneous`; priority has only RM. DM and additional power/generator distributions are not implemented or inferred.

The constrained-deadline pair starts from one canonical implicit-deadline sample and changes only D using the existing generator rule with a stable axis seed. Task IDs, C, T, P, workload, arrival offset, and priority rank are checked unchanged. Since changing period-range bounds regenerates periods and dependent task parameters, that axis is explicitly `UNPAIRED_STRATIFIED_COMPARISON`. Priority, power, and generator-family axes are emitted as `UNAVAILABLE` because they have only one registered level.

Run the bounded smoke with:

```bash
python3 scripts/run_v9_3_ext4.py --config configs/v9_3_ext4_smoke.yaml
python3 scripts/run_v9_3_ext4.py --config configs/v9_3_ext4_smoke.yaml --resume
python3 scripts/analyze_v9_3_ext4.py --output-root artifacts/v9_3_ext4_smoke
(cd artifacts/v9_3_ext4_smoke && sha256sum -c file_hashes.sha256)
```

The smoke contains three samples, six CORE-1 analyses (`CW_THETA_CW`, `LOC_THETA_LOC`), and three ASAP-BLOCK simulations. It is below the hard limits of 12 RTA analyses and six simulations. Timeout is not rejection, horizon insufficiency is not pass, and `RTA_PASS_SIM_FAIL` or a local-vs-complete dominance violation is P0 and stops the run after preserving artifacts.
