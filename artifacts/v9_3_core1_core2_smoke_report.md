# ASAP-BLOCK v9.3 CORE-1 / CORE-2 smoke report

Date: 2026-07-13

These are framework smoke tests only. No formal large-scale experiment was run,
the paper grid was not frozen, and the observations below are not paper
conclusions.

## Executed smoke runs

| run | cells | requests | attempts | terminal | per-task rows | status | failures |
|---|---:|---:|---:|---:|---:|---|---:|
| CORE-1 implicit | 2 | 4 | 4 | 4 | 40 | 4 COMPLETED | 0 |
| CORE-2 implicit | 2 | 10 | 10 | 10 | 100 | 10 COMPLETED | 0 |
| CORE-1 constrained | 1 | 2 | 2 | 2 | 20 | 2 COMPLETED | 0 |

Commands:

```bash
python3 scripts/run_v9_3_core1.py --config configs/v9_3_core1_smoke.yaml
python3 scripts/run_v9_3_core2.py --config configs/v9_3_core2_smoke.yaml
python3 scripts/run_v9_3_core1.py --config configs/v9_3_constrained_deadline_smoke.yaml
```

CORE-1 and CORE-2 reused taskset hash
`2292118c3dc7677d27da45e2795999e7df9dbbf93513d3e088512e9aec860b10`
for both E0 cells. The seed and semantic payload are independent of E0 and
variant. An additional automated test regenerates the constrained taskset in
two independent stores with the same seed and verifies equal canonical payload
and semantic hash.

CORE-1 yielded 20 common-candidate task comparisons: 20 equal, 0 tighter, 0
violations. CORE-2 yielded 20 common-candidate tasks in each of the three
dominance relations: all equal and 0 violations. This smoke does not establish
strict response improvement.

The two CORE-2 `LOC_THETA_CW` requests both had `VALID` dependencies. Each
source vector hash exactly matched its target carry-in vector hash, and
`fallback_used` was false.

The constrained generator smoke materialized ten tasks satisfying
`C <= D <= T`; its actual D/T range was `23/82` through `44/47`, and at least
one task had `D < T`. This validates passthrough of the generator's existing
distribution only; no D/T distribution was selected or frozen by this work.

The checked smoke artifacts contain `summary.json`, `summary.csv`, plot-data
CSVs, checkpoint state, dependency/dominance tables, and `file_hashes.sha256`.
