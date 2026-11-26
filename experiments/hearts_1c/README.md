# HEARTS-1C and partitioned/global ASAP-BLOCK comparison

This is a **new, isolated experiment**, not a replacement for the existing
v9.3 experiment suites or C++ schedulers. It uses the same taskset generator,
workload-to-energy model, synthetic harvesting source and LOAD-CROSS energy
normalization as PARTsim. All compared strategies run in a common Python
exact-energy reference replay. Native PARTsim ASAP-BLOCK and ASAP-NONBLOCK
are checked against that reference using the same-slot C++ simulator.

Reference: HEARTS, DOI [10.1007/s11227-024-06685-7](https://doi.org/10.1007/s11227-024-06685-7),
in particular Algorithms 5–7 (PALAP, PASAP, and rolling windows).

## Two separate research questions

| Suite | Core count | Task count | Methods | Question |
|---|---:|---:|---|---|
| `hearts` | 4 | 4 | HEARTS-1C, GLOBAL-AB, GLOBAL-AN | With one core sufficient for every task, how do forecast-based early/late energy allocation and online energy-aware RM compare? |
| `partitioned` | 4 | 10 | PARTITIONED-AB, GLOBAL-AB | With identical ASAP-BLOCK energy rules, how does fixed CPU assignment compare with a shared CPU pool? |

The first suite deliberately avoids CPU contention: at most four sequential
jobs can be ready, and HEARTS-1C has a distinct core for each task. It isolates
energy scheduling; it is **not** evidence about the original HEARTS handling of
parallel DAGs or individual utilization above one. HEARTS-1C is a sequential,
one-dedicated-core, zero-static-power adaptation of the paper's energy planner,
not a line-for-line reproduction of the original or its general core-count
search. The second suite isolates fixed placement versus global placement
under the specified partition heuristic, rather than comparing against HEARTS.

## Common contract

- Identical paired taskset, energy trace, battery capacity, initial charge,
  release interval and observation horizon within each cell. All tasks are
  synchronous, sequential periodic tasks with implicit deadlines (`D=T`).
  RM priority follows the canonical taskset ordering; smaller period means
  higher priority. At equal periods the frozen task priority order is used.
- Four homogeneous cores, one shared finite battery, initial charge zero;
  costs are fixed per execution slot. Static, migration and separately
  modeled scheduler energy are zero in **all** methods.
- With battery `b[s]` before collection, offered energy `h[s]` and selected
  execution cost `p[s]`, require `p[s] <= b[s]+h[s]`; then
  `b[s+1] = min(capacity, b[s]+h[s]-p[s])`. Spill is the surplus above
  capacity after current-slot expenditure, not before it.
- Energy arithmetic is rational: use the existing exact raw trace and its
  exact material scaling; convert every energy value to a common integer
  unit without rounding. This isolates the algorithms from binary64
  threshold artifacts. The scaled reference trace does not claim bitwise
  identity with arbitrary native floating-point configurations.
- To evaluate all methods over the same complete jobs, releases occur at
  `r < horizon - max(T)`. All generated jobs have deadlines strictly before
  `horizon`; the full horizon including the idle tail is replayed. This
  finite-release contract prevents censored trailing jobs from biasing the
  pass rate. Its results are distinct from existing campaigns without this
  release cutoff. Online methods discard a missed job at its deadline.
- Perfect offered-energy forecasts **within each planning window** are
  available to HEARTS-1C; online AB/AN do not forecast. This is a favorable
  strong-information baseline for the adapted HEARTS algorithm, not a
  forecast-error robustness experiment.
- Canonical period range 40–200 ticks, one tick = 1 ms on the configured
  PARTsim model. Per-task utilization 0.01–0.99 in the four-task suite (needed
  for the high aggregate loads), 0.01–0.8 in the ten-task suite. Canonical
  generation tolerance 0.01 total utilization, see actual utilization in
  saved tasksets. The opt-in bounded-simplex UUniFast sampler translates
  or reflects the utilization simplex to sample from its nearer endpoint,
  preserving its uniform conditional distribution while avoiding rare
  acceptance at high aggregate utilization. This option has distinct
  generation identities; the legacy generator and existing experiment
  defaults remain unchanged. Formal cases use 120 tasksets/cell and 60000 ticks.

## Scheduling rules

**HEARTS-1C:** one unique core per task. Window width = largest period;
process tasks in priority order and their complete jobs in release order.
Try placing every unit of execution as late as possible (PALAP); if any
placement fails, discard the entire tentative plan and retry from its original
reservation state as early as possible (PASAP). Accept the first successful
plan. Advance the executing prefix to the earliest next unplanned release,
carry forward still-reserved execution slots into the overlapping next window,
and extend the window by its width. If both fail, report `NO_WINDOW_PLAN`.

For constant sequential job cost there is no DAG-derived per-slot demand
profile. Instead of introducing the paper's parallel-burst `res/ure` arrays
directly, preserve prior allocations by an exact feasibility ledger. Given
already-planned expenditure `used[s]`, the minimal pre-collection battery
needed to honor its suffix is

```
needed[end] = 0
needed[s] = max(0, needed[s+1] + used[s] - h[s])
```

All requirements must fit capacity. Inserting a new cost at slot `s` is
allowed exactly when
`b[s] + h[s] - used[s] - cost >= needed[s+1]`.
Thus the new allocation cannot spend energy needed by previously placed
higher-priority work, even when considering saturation. The ledger updates both
the forward battery and the backward requirement after every successful
insertion. It is tested against exhaustive independent forward feasibility
checks on small finite-battery inputs. This is a specialized constructive
baseline; failure is **not** a proof that no other valid schedule exists.

**GLOBAL-AB:** visit ready jobs in RM order, assign at most four, stop at
the first insufficient-energy job. **GLOBAL-AN:** skip unaffordable jobs and
continue looking for affordable ready jobs. Both allow movement across cores.

**PARTITIONED-AB:** search for any CPU-feasible fixed assignment with
uniprocessor RM response-time analysis (complete backtracking with
symmetry elimination and CPU-load pruning). Pick the first deterministic
CPU-feasible assignment in decreasing-current-core-load order. The
partitioner uses no future energy information; it is not energy-optimal.
No assignment produces `NO_CPU_PARTITION` and remains in the denominator.
At runtime select one highest-priority ready task per assigned core, order
those heads by the common RM order and apply the same ASAP-BLOCK energy
prefix. A cheaper lower-priority local job cannot bypass its local head.

## Run

From the repository root in its normal Python environment:

```bash
# Both suites, two tasksets each, 1000 ticks; checks the full data path.
python scripts/run_hearts_1c_comparison.py --campaign smoke --output runs/hearts-smoke

# Full four-task comparison grid; choose workers appropriate for your machine.
python scripts/run_hearts_1c_comparison.py --suite hearts --campaign all \
    --prepare-workers 4 --workers 4 --output runs/hearts-1c-formal

# Full ten-task comparison grid.
python scripts/run_hearts_1c_comparison.py --suite partitioned --campaign all \
    --prepare-workers 4 --workers 4 --output runs/partition-ab-formal
```

The three campaigns may be run separately using `--campaign uc|ue|capacity`.
`--samples-per-cell`, `--horizon` and `--seed` allow explicitly labeled
smaller studies. Each formal suite has 57 cells:

- UC: 0.1–0.9 per core, 3 fixed absolute supply levels (existing low, medium,
  high LOAD-CROSS sources), battery multiplier kappa=10. Record actual UE.
- UE: 0.1–0.9 using service-only normalization at UC=0.3, 0.5, 0.7,
  kappa=10. The workload is held fixed and only the supply scale changes.
- Capacity: kappa=1, 5, 10 at UC=0.5 and UE=0.6. Capacity is kappa times the
  sum of the largest four task per-tick demands, as in LOAD-CROSS. Initial
  charge stays zero so a capacity increase does not add starting energy.

The raw harvesting trace is normalized over 60000 ticks even in smoke mode,
so a shortened smoke run does not change the input scaling. The tasksets
are reused across supply and capacity conditions at the same UC. Output
directories must be new; a failed or partial run is never silently replaced or
treated as completed.

## Read results

- `config.json`: grid, model, counts, seed, source-code hashes, observation
  contract.
- `material/` and `tasksets.jsonl`: auditable canonical taskset and service
  projections, priority/workload hashes and target/actual UC.
- `results.jsonl`: per-method rows with taskset hash, raw trace identity,
  scale, battery capacity, initial energy, actual UE, finite release cutoff,
  results and execution/energy metrics. Explicit planner window counts,
  placement mapping, or partition failure are included where relevant.
- `summary.csv`: WholePass (all eligible jobs complete by deadline) rate,
  planning/partition failures, observed miss rate, weighted mean response
  time of completed jobs, spill ratio and median reference scheduling time.
- `paired_comparison.csv`: both pass, both fail, only method A/B passes,
  and paired pass-rate difference for each cell/method pair.
- `COMPLETE.json`: written only after all requested pairs and summaries
  finish. Use only completed runs for figures.

A `NO_WINDOW_PLAN` or `NO_CPU_PARTITION` is an unsuccessful taskset for
WholePass, but not an invented deadline miss or execution trajectory.
Trajectory metrics for those cases are null; summary miss/response/spill
rates use only complete trajectories and report their denominators.
Those conditional metrics should not be used alone to claim superiority
across methods with different failure counts. WholePass is the primary
metric; use the paired comparison table rather than comparing unpaired
tasksets. Reference-Python scheduling time includes planner/partition work
but excludes the independent validation pass; it is not native scheduler
latency or an estimate of hardware energy overhead. Use `--workers 1` for
less-confounded timing comparisons.

## Verify

```bash
python -m unittest discover -s test -p test_hearts_1c_comparison.py -v
# To include native C++ conformance after building the same-slot branch:
PARTSIM_TEST_BINARY=/absolute/path/to/build/rtsim/rtsim \
    python -m unittest discover -s test -p test_hearts_1c_comparison.py -v
PARTSIM_TEST_BINARY=/absolute/path/to/build/rtsim/rtsim \
    python -m unittest discover -s test -p test_same_slot_energy.py -v
```

The native test compares **every selected job per tick**, completion/miss
counts and all energy-ledger totals for GLOBAL-AB and GLOBAL-AN with
high/low variable harvest including exact threshold, spill, blocking,
nonblocking bypass and deadlines. New-method tests verify rollback on
PALAP failure, overlapping windows, finite-capacity reservation insertion,
partition completeness on small cases, independent trajectory replay,
release cutoff, conservation and paired-input consistency.
