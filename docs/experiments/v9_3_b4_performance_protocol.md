# v9.3 B4 overall performance protocol (EXT-1P / PERF-G)

## Scope and interpretation

B4 is one paired overall-performance experiment. Its four paper figures are four analyses of one frozen PERF-G result set; they are not four separately sampled experiments. CAL and the horizon gate are preregistered parameter-selection and observation-window checks, not contribution experiments. B1, B2, and B3 remain separate mechanism experiments and none of their traces, stores, identities, or conclusions is imported by B4.

The workload consists of randomly generated synchronous periodic constrained-deadline task sets. Every real-time task has `arrival_offset=0`, and the generation command records `--no-arrival-offset`. The results do not establish behavior for asynchronous or general sporadic releases.

An observed task-set pass is a finite-horizon empirical outcome, not a formal schedulability proof. B4 does not claim that ASAP-BLOCK is globally optimal. The theoretical interpretation remains limited to the project's proved local prefix property under blocking semantics and the restricted non-improvement property for bypass. Simulation results must not be extrapolated into a general optimality theorem.

## Frozen platform and task generation

- Four global, preemptive, migrative processors.
- Ten independent sequential periodic tasks.
- Fixed-priority RM with deterministic tie breaking.
- Millisecond time unit.
- Integer periods uniformly generated in `[40, 200]` ms.
- `C_i <= D_i <= T_i`, constrained-deadline generator contract.
- Compensated WCET rounding.
- Per-task utilization `[0.01, 0.8]`.
- Absolute total-utilization tolerance `0.01`.
- DAG generation disabled.
- Synchronous release (`arrival_offset=0`) is verified after generation.

The exact non-idle workload contract is the lexical vector `bzip2, control, decrypt, encrypt, hash`. `idle` is reserved for system state. Per-task powers are frozen from the system power model when the taskset is generated; analysis never estimates power from a workload name.

The formal utilization grid is `0.1` through `0.8` in increments of `0.1`, with 200 unique tasksets at each point. The formal store therefore contains 1,600 tasksets. A taskset is reused across all three energy conditions and all nine schedulers. It is never replaced because of activation, pass/fail, an algorithm ranking, a confidence interval, or plot appearance. Only preregistered structural generation failures may retry, and every attempt is retained.

CAL uses a separate seed space and store: 30 tasksets at each of `0.3, 0.5, 0.7`, base seed 981201. PERF-G uses base seed 982201. CAL and PERF-G samples cannot overlap by identity.

## Schedulers and comparisons

The complete frozen registry is:

1. ASAP-BLOCK
2. ASAP-NONBLOCK
3. ASAP-SYNC
4. ALAP-BLOCK
5. ALAP-NONBLOCK
6. ALAP-SYNC
7. ST-BLOCK
8. ST-NONBLOCK
9. ST-SYNC

The five primary schedulers used by CAL and the horizon gate are ASAP-BLOCK, ASAP-NONBLOCK, ASAP-SYNC, ALAP-BLOCK, and ST-BLOCK.

Exactly four confirmatory comparisons are preregistered at transition supply:

- ASAP-BLOCK minus ASAP-NONBLOCK;
- ASAP-BLOCK minus ASAP-SYNC;
- ASAP-BLOCK minus ALAP-BLOCK;
- ASAP-BLOCK minus ST-BLOCK.

No comparison may be added, removed, or exchanged after observing results.

## Fixed-window energy contract

For each frozen taskset, exact rational arithmetic defines

`P_dem = sum_i (C_i / T_i) P_i`

and, for `M=4`,

`E_burst = sum of the four largest P_i`.

For a CAL-selected `kappa`, battery capacity is `B = kappa E_burst` and initial energy is `B/2`.

Solar normalization always uses the first 60,000 ticks of the unscaled real-solar system projection. The source phase includes both the template's `day_of_year` and `time_of_day_ms`, matching the native energy manager. If the resulting `P_raw_ref` is non-positive, the stage fails closed. For an exact offered ratio `eta`,

`solar_scale = eta P_dem / P_raw_ref`.

The exact value is frozen before stable binary64 materialization. A 30-second run uses the first half of that same scaled 60-second projection; it is never renormalized over 30 seconds. Runtime horizon is excluded from energy identity, so paired 30/60 requests share an energy identity while having distinct semantic request identities.

Finite batteries explicitly set `allow_harvest_clipping: true`. Thus `eta` is a nominal/offered harvest ratio. Energy that cannot enter a full battery may be lost, and stored energy can differ across scheduling traces even though offered traces are paired. B4 does not report overflow energy or claim that the three conditions prove complete energy shortage or abundance.

## Q-only calibration

The initial CAL grid is `kappa in {10,50,200}` and `eta in {0.5,0.75,1,1.25,1.5}`. It runs the 90 CAL tasksets with the five primary schedulers for 10 seconds: 6,750 requests.

For each `(kappa, eta, u)`, the only selection quantity is the median, over the five primary schedulers, of observed task-set pass ratio. This quantity is `Q(kappa,eta,u)`. Selection cannot inspect ASAP-BLOCK rank, pairwise effects, p-values, EBF, mechanism results, or plots.

A transition candidate has at least two utilization points with `0.2 <= Q <= 0.8`. Candidates are ordered by: maximum qualifying-point count; minimum total distance to 0.5; minimum distance of eta to 1; and minimum kappa. At the chosen kappa, low is the largest smaller eta with `Q(u=0.5) <= 0.2`, and high is the smallest larger eta with `Q(u=0.5) >= 0.8`.

The single preregistered eta extension is `{0.25,2}`. If transition already exists, only missing endpoints at the frozen kappa are added (branch A). If no transition exists, both endpoints are added for all three kappas and the same rule is rerun over 21 cells (branch B). Failure to obtain all three conditions stops the experiment.

The provisional three conditions are confirmed on the same 90 tasksets and five algorithms at 30 seconds (1,350 requests). A failed confirmation triggers one full 30-second CAL-grid fallback using the same Q-only rule. Conditions are sealed before PERF-G results are viewed.

CAL selection never treats CSV as authoritative. Each initial, extension, confirmation, or fallback phase first closes its request-plan IDs exactly against compact terminal JSON and the verified frozen CAL manifest. Missing, duplicate, or extra results; a final timeout; internal/trace error; non-terminal simulation; identity mismatch; nonzero offset; or anything other than exactly 30 paired tasksets per scheduler cell produces `CAL_INVALID`. Branch A freezes its provisional `kappa_star` and transition eta and searches only the preregistered missing endpoint; endpoint results are never fed back into transition selection.

Every provisional or selected CAL control is accepted only after its selection identity, aggregate CAL-audit identity, all `CAL_VALID` phase audits, and stage-environment identity verify. The audited initial-plus-extension cells are frozen in Fraction order as `final_10s_grid_cells` under the independent `ASAP_BLOCK:V9.3:B4:CAL_FINAL_10S_GRID:v1` identity. A 30-second full-grid fallback must use exactly that sealed cell set—never cells reconstructed from mutable `q_values`—with 450 requests per cell. The ordinary confirmation plan is also bound exactly to `low`, `transition`, and `high` at their sealed `(kappa,eta)` pairs.

## Frozen formal store and horizon gate

After the CAL seal, all 1,600 formal tasksets are frozen. The gate selects the lexically smallest 50 taskset semantic hashes at every utilization point. It uses transition energy, the five primary schedulers, and 30/60-second horizons: 4,000 requests.

`SELECT_30S` requires all of the following:

- no utilization-by-primary-scheduler pass-ratio change greater than 0.05;
- all four overall paired pass differences have matching directions, or both horizon effects have magnitude below 0.02;
- the minimum adjudicable-job contract holds;
- all 4,000 requests are complete;
- taskset, energy, source, binary, and request identities close;
- outcome recomputation closes.

If the scientific stability checks fail while the technical checks pass, `SELECT_60S` is the normal preregistered outcome. Technical, identity, completeness, or adjudicability failures produce `INVALID_GATE`.

The 2,000 gate requests at the selected horizon are a strict subset of the formal request identities and are reused. Requests at the unselected horizon are disjoint from the formal plan. The gate only checks the five primary schedulers at transition supply; no horizon-stability claim is made for other algorithms or energy conditions.

## PERF_OUTCOME_V2

The legacy trace parser remains unchanged. B4 ignores its status when determining paper outcomes and recomputes from job records and frozen task payloads.

The observation window is half-open: `[0,H)`. A job is adjudicable if its release is at or after warmup and its absolute deadline is strictly below `H`. A deadline equal to `H` is right-censored. An adjudicable job is on time only when a completion exists and is no later than its absolute deadline; otherwise it is a miss. A completion at `H` is not a completion inside the observation window.

Observed pass requires native completion at `H` with reason `reached_horizon`, no technical error, no adjudicable miss, and the minimum adjudicable-job count for every task. CAL uses a minimum of 30. Gate and formal PERF-G use 100.

The evaluator fails closed on duplicate logical jobs, unknown tasks, missing arrival identity, deadline before release, completion before release, or an explicit miss attached to an on-time completion.

Metrics are first computed within taskset:

- `JMR`: missed adjudicable jobs divided by all adjudicable jobs;
- `Top-M JMR`: the same metric for priority ranks 0 through 3;
- `Top-25% JMR`: ranks 0 through 2;
- `CompletionRatio`: adjudicable jobs completed strictly before `H` divided by all adjudicable jobs.

A zero denominator is `UNAVAILABLE`, never zero and never silently dropped.

## Pairing and inference

The independent unit is the paired taskset, not a job. Single-cell pass ratios use Wilson 95% intervals.

Paired confidence intervals use 10,000 stratified paired percentile-bootstrap draws. Tasksets are resampled with replacement within each utilization stratum, the original stratum size is retained, and the eight stratum means are equally weighted. Per-utilization intervals resample within that utilization only.

The four confirmatory transition-supply comparisons use paired label-swap permutation tests with 10,000 draws, two-sided plus-one p-values, and Holm correction across exactly four p-values. B4 does not create a 96-test confirmatory family.

## Figures

Figures are generated only after `FORMAL_COMPLETE`:

1. Three panels (low, transition, high) with the five primary algorithms and Wilson intervals.
2. Transition-supply nine-algorithm matrix grouped into ASAP, ALAP, and ST panels.
3. Four paired ASAP-BLOCK advantages with zero line, percentile-bootstrap intervals, and effective paired sample sizes.
4. Transition-supply ASAP-BLOCK versus ASAP-NONBLOCK differences for Top-M JMR, Top-25% JMR, and overall completion ratio.

Positive JMR differences mean BLOCK has the lower high-priority miss ratio. Each figure has vector PDF, 300-dpi PNG, and an underlying CSV. Matplotlib is used without seaborn; color, markers, and line styles all distinguish algorithms for grayscale reproduction.

## Trace and execution policy

`trace_mode: job` deliberately omits `--semantic-traces`, avoiding B1/B2/B3 per-tick mechanism diagnostics while retaining arrival, schedule/deschedule, completion, explicit miss, and simulation-completion events. Every request produces compact terminal JSON. Full job traces are retained for a deterministic identity-based sample with nominal rate 5% and, when possible, internal/trace errors. Ordinary deadline misses do not cause bulk trace retention. Every identity-selected retained trace is independently reparsed against the frozen taskset and recomputed with `PERF_OUTCOME_V2` before `FORMAL_COMPLETE`.

B4 does not use the shared runner's RTA release-energy certificate. Its runner-side certificate threshold is fixed to zero and terminal JSON records `rta_release_e0_certificate=NOT_APPLICABLE`. This does not change the simulated initial battery, which remains the planned `B/2`; it only prevents normal later releases below `B/2` from causing bulk failure-trace retention.

`--plan-only` validates configuration and static counts without simulator, generator, output root, or store creation. `--freeze-tasksets` may call only the generator. `--execute` requires a frozen, hash-closed store and required stage seals; it never generates missing tasksets. `--analyze-only` cannot fill missing requests or generate figures before the terminal gate.

Formal timeouts are 300 seconds with one 600-second retry. Resume preserves semantic request identities and deterministic ordering.

Execution parallelism is operational only. The parent process scans and validates all current-plan terminal JSON before scheduling, selects `--max-requests` from the still-pending frozen order, and is the only writer of final terminal JSON. Independent simulator requests may run through a bounded thread pool using `execution.worker_count`; completion order, worker count, checkpoints, and progress paths are excluded from every scientific and request identity. The checked-in AutoDL profiles use 24 workers for CAL/gate/formal, reserving eight logical cores on a 32-core host, while the tiny smoke uses four. `worker_count=1` retains the serial path.

With `resume=true`, only readable terminal results whose semantic request ID, execution identity, and terminal marker match the current request are skipped. Corrupt, partial, non-terminal, or mismatched evidence fails closed. With `resume=false`, any current-request terminal collision fails closed. On a worker failure, no new requests are submitted; queued work is cancelled where possible, running work is awaited, and already atomically written terminals remain valid for a later resume.

Every executable or analysis stage carries `B4_STAGE_ENVIRONMENT_V1`: exact clean source commit, simulator hash, system-template and solar hashes, workload/power identity, outcome source/version, energy/request contract versions, and a path-independent scientific stage-config hash. CAL, gate, formal, and analysis fail closed if their shared environment provenance differs. Frozen stores are also rebound to the current seed/grid/generator/template/workload contract, so a same-sized stale store is inadmissible.

## Request counts and prohibited runs

- CAL initial: 90 tasksets, 15 energy cells, five schedulers, 6,750 requests.
- Three-condition 30-second confirmation: 1,350 requests.
- Horizon gate: 400 tasksets, one energy condition, five schedulers, two horizons, 4,000 requests.
- Formal PERF-G: 1,600 tasksets, three energy conditions, nine schedulers, 43,200 requests.
- Selected gate requests reused by formal: 2,000.
- Requests remaining after gate: 41,200.
- Base successful path including the unselected gate controls: 53,300 requests.

Implementation and review may run plan-only, tiny taskset freezes, unit/integration tests, and a nine-scheduler short smoke. They must not run complete CAL, complete confirmation, complete gate, or the 43,200-request formal experiment. No paper result is claimed by implementation smoke.

B4 intentionally has no EBF counter, priority-weighted miss panel, best-other heatmap, random-offset robustness track, new scalability track, or duplicated mechanism trace. Scheduler semantics, RTA mathematics, CORE-1 through CORE-5 contracts, and all B1/B2/B3 artifacts remain unchanged.
