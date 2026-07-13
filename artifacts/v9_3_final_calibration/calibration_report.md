# ASAP-BLOCK v9.3 final constrained-deadline calibration

This is the final bounded parameter calibration, not a formal paper run.
No further Pilot or parameter search is authorized by this report.

- Actual taskset/E0 instances: 12
- Actual production analyses: 60
- Stage B selected cells: 6
- Production timeout: 30 seconds; TIMEOUT-only retry: 60 seconds

## Stage A classification

| U | exact E0 | class | strict envelope | local-only closure | strict response | certification gain | timeout |
|---:|---:|:---:|---:|---:|---:|---:|---:|
| 1/5 | 21473099401200000281/200000000000000000000 | B | 71 | 0 | 0 | 0 | 0 |
| 1/5 | 1 | B | 71 | 0 | 0 | 0 | 0 |
| 3/10 | 21473099401200000281/200000000000000000000 | B | 262 | 0 | 0 | 0 | 0 |
| 3/10 | 1 | B | 262 | 0 | 0 | 0 | 0 |
| 2/5 | 21473099401200000281/200000000000000000000 | B | 369 | 0 | 0 | 0 | 0 |
| 2/5 | 1 | B | 369 | 0 | 0 | 0 | 0 |

## Final cell metrics

| U | exact E0 | instances | class | certified/requested | runtime p95 (s) | D/T min/median/max | dominance violations |
|---:|---:|---:|:---:|---:|---:|---|---:|
| 1/5 | 21473099401200000281/200000000000000000000 | 2 | B | 10/10 | 0.934844 | 13/48 / 6379/12320 / 80/81 | 0 |
| 1/5 | 1 | 2 | B | 10/10 | 0.989685 | 13/48 / 6379/12320 / 80/81 | 0 |
| 3/10 | 21473099401200000281/200000000000000000000 | 2 | B | 5/10 | 5.290601 | 17/147 / 1201/2303 / 41/43 | 0 |
| 3/10 | 1 | 2 | B | 5/10 | 5.255568 | 17/147 / 1201/2303 / 41/43 | 0 |
| 2/5 | 21473099401200000281/200000000000000000000 | 2 | B | 5/10 | 9.319658 | 9/95 / 1477/2232 / 35/36 | 0 |
| 2/5 | 1 | 2 | B | 5/10 | 8.540878 | 9/95 / 1477/2232 / 35/36 | 0 |

## Proposed formal grid

- Utilizations: ['1/5', '3/10', '2/5']
- Exact E0: ['1', '21473099401200000281/200000000000000000000']
- Tasksets per cell: 50
- Timeout/retry: 30 / 60 seconds
- Formal base seed: 930612
- Worker count: 4
- CORE-1 analyses: 600
- CORE-2 analyses: 1500
- Expected combined wall time: 0.598 hours
- Conservative combined wall time: 1.434 hours

Parameter status: PROPOSED_NOT_YET_FROZEN.
The candidate configurations must be reviewed/frozen before any formal run starts.
