# Current nine-strategy comparison

All new comparisons use the same-slot energy recurrence. The implementation is
shared with `experiments/v9_3/scheduler_load_cross.py`, the native nine C++
schedulers, and the existing trace validator. The wrapper
`scripts/run_scheduler_comparison.py` fixes the energy timing and defaults to
zero initial energy. It rejects legacy A-implicit V1/supplement modes.

For the standardized implicit-deadline scans, run separate fresh roots:

```bash
python scripts/run_scheduler_comparison.py --output runs/current-uc \
  --seed 20260906 --experiment-version a-implicit \
  --campaign a-implicit-uc-fixed-supply --samples-per-cell 120
python scripts/run_scheduler_comparison.py --output runs/current-ue \
  --seed 20260906 --experiment-version a-implicit \
  --campaign a-implicit-ue-service-scaling --samples-per-cell 120
python scripts/analyze_scheduler_load_cross.py --input runs/current-uc
python scripts/analyze_scheduler_load_cross.py --input runs/current-ue
```

Use the existing `--simulator`, `--workers`, `--prepare-workers`, and
`--parse-concurrency` options as appropriate. The analyzer writes
`same_slot_summary.csv`; do not stitch it into the old debit-before-harvest
results. Historical commands stay compatible for reproducibility.
