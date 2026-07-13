# v9.3 EXT-2 execution contract

EXT-2 is trace-driven simulation. It is not finite-battery RTA. Unknown units, timestamps without timezone, duplicate/reversed timestamps, zero intervals, negative/NaN/Inf values, and undeclared missing-value handling fail closed.

Canonical interval energy and accumulated conservation checks use `fractions.Fraction`. Exact aggregation, exact subdivision, and declared piecewise-constant interpolation are supported. Scaling is an exact rational transform and records input hash, scale, output hash, and total-energy change.

The current repository status is `REAL_TRACE_DATA_UNAVAILABLE`; see `docs/audits/v9_3_ext2_trace_inventory.md`. Run the fixture smoke with:

```bash
python3 scripts/run_v9_3_ext2.py --config configs/v9_3_ext2_smoke.yaml
python3 scripts/run_v9_3_ext2.py --config configs/v9_3_ext2_smoke.yaml --resume
python3 scripts/analyze_v9_3_ext2.py --output-root artifacts/v9_3_ext2_smoke
(cd artifacts/v9_3_ext2_smoke && sha256sum -c file_hashes.sha256)
```

The segment rule is configured before execution. The smoke source is explicitly `SYNTHETIC_TEST_FIXTURE` and must not be reported as real experimental evidence. RTA is disabled with `NOT_APPLICABLE_NO_CERTIFIED_SERVICE_BOUND`. The finite-segment window-minimum constructor and interval validator are tested, but an actual future trace is never passed off as an a priori RTA service curve.
