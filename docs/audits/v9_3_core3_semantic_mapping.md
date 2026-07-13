# ASAP-BLOCK v9.3 CORE-3 RTA / discrete-simulation semantic mapping

Status: implementation gate for CORE-3 (not a soundness proof and not a
large-scale result).

Baseline: `30438c695d0b2dfb1bbebfd97ca5c2094ab95d96`.

## Audited entry points

- Production RTA: `asap_block_rta_v9_3_taskset.analyze_taskset_v9_3`, reached
  through `asap_block_v9_3_runner.dispatch_rta_version` and the shared
  `experiments.v9_3.execution_engine.ExecutionEngine`.
- Shared CORE-1/CORE-2 taskset/config/checkpoint framework:
  `experiments/v9_3/{config,cell_model,taskset_store,execution_engine,result_writer}.py`.
- C++ scheduler factory: `gpfp_asap_block` in `librtsim/system.cpp`; scheduler
  implementation: `librtsim/scheduler/gpfp_asap_block_scheduler.cpp`.
- Simulator executable/trace publisher: `rtsim/main.cpp`; current default
  runner/parser: `acceptance_ratio_test.run_single_simulation_worker` and
  `acceptance_ratio_test.TraceParser`.

Before CORE-3, the repository can run RTA and simulation separately, and the
simulator can emit schema-v2 JSON arrival, schedule, deschedule, completion,
deadline-miss, energy, and optional scheduler-decision events. It cannot run
both analyses from one persisted v9.3 frozen taskset: the shared store keeps an
RTA-oriented canonical JSON after deleting the generator's temporary YAML, and
the existing parser reports only maximum response by task. CORE-3 therefore
adds a projection from that canonical record to simulator YAML and a job-level
parser; it does not change scheduler semantics.

## Tick and job mapping

| Item | v9.3 RTA meaning | ASAP-BLOCK simulation meaning | CORE-3 rule |
|---|---|---|---|
| Numerical unit | Integral `w,h,q,C,D,T` | `MetaSim::Tick`; the general template otherwise applies workload-dependent CPU speed scaling | One tick is one millisecond; the CORE-3 system projection fixes every workload speed parameter to 1 while preserving power parameters, and rejects non-integral timing. |
| Tick boundary | Progress/blocked tick indexed from a job release boundary | At tick `t`, harvest is collected, active jobs are RM-sorted, a prefix is frozen/dispatched, and its one-tick energy is committed | Compare boundary-to-boundary response times. |
| Release | Sporadic job release; theorem covers any legal release sequence | `PeriodicTask` arrival at phase plus multiples of `T`; current-tick arrivals enter the active set | Use the frozen task's phase as one legal periodic release sequence. |
| Deadline | Relative `D`; legal candidate satisfies `C <= R <= D` | Absolute deadline is `release + D`; deadline event at that boundary | Derive and validate the same absolute deadline for every traced job. |
| Same-boundary completion | A job completed by `r + D` meets its deadline | JSON miss writer suppresses a callback with zero remaining work | Completion at the absolute deadline is a pass. |
| Harvest/execute order | Service available through the boundary index used by `beta(h+q-1)` | `collectEnergyAtTickBoundary`, select/freeze/dispatch, then `commitTickEnergy`; selected work executes over the following unit interval | Use the same system harvesting input; record this boundary convention in metadata. |
| Per-tick debit | Selected prefix must be payable | Sum of selected jobs' unit energy is reserved once and subtracted once | Validate traced task unit energy against frozen `P`. |
| Multicore execution | At most `M` progress jobs per tick | Frozen prefix length is bounded by the kernel CPU count | Simulator system `numcpus` must equal frozen `M`. |
| BLOCK wall | First unaffordable job prevents lower-priority bypass | `selectASAPBlockPrefix` breaks at the first unaffordable RM job | Infer energy-blocked ticks from semantic decisions; no lower job may be selected beyond the wall. |
| Priority/tie break | Frozen task vector is in RM priority order | Period ascending, then C++ task number ascending | Materialize simulator YAML in `priority_rank` order so equal-period ties match. |
| Preemption | Global fixed-priority preemptive execution | Each tick, running jobs outside the frozen prefix are suspended | Count trace `descheduled` events whose reason is `preemption`. |
| Battery lower bound | No negative usable energy | Commit clamps residual to zero after affordability check | Reject negative/non-finite trace energy. |
| Battery upper bound | Main theorem is no-overflow, or requires an independently valid usable-energy service curve | Simulator clips `current + harvested` at `max_energy` | Before execution, require capacity to exceed initial energy plus all harvest possible through `maximum_horizon`; otherwise the sample is not comparable. |
| Completion | End boundary of the job's last progress tick | `end_instance.time` | `response_time = completion - release`. |
| Horizon tail | Not a proof observation | Active/released jobs can remain unfinished when the run reaches its horizon | Mark each such job right-censored; never call horizon insufficiency a pass. |

## Energy interface (mandatory distinction)

The four quantities are persisted separately:

1. `rta_release_e0`: the RTA premise `E(r_J) >= E0` for every analyzed job;
2. `simulation_initial_battery`: the actual battery state at simulation time 0;
3. `simulation_harvest_trace`: energy actually offered by the configured
   harvesting source over simulated ticks;
4. `rta_service_lower_bound`: the validated `beta` prefix constructed from
   that source.

CORE-3 does **not** infer item 1 from item 2. It validates every trace arrival's
pre-execution energy snapshot against `E0`. A violation of this premise makes
the RTA/simulation pair ineligible for soundness or tightness comparison; it is
reported as an energy-premise censoring/failure reason, never as
`RTA_PASS_SIM_FAIL`.

The finite simulator battery is also required to be large enough that clipping
cannot occur even if no work consumes energy through `maximum_horizon`. This
is a conservative executable guard that lets the configured raw harvest trace
serve as the no-overflow simulation input. A configuration that fails this
guard is rejected before simulation.

## Trace-derived job fields

The schema-v2 trace already supplies releases, completions, scheduling and
descheduling, misses, energy snapshots, and semantic frozen-prefix decisions.
CORE-3 derives `job_index` by each task's release order; `first_execution` from
the first matching `scheduled` event; preemptions from preemption deschedules;
energy-blocked ticks from distinct frozen-prefix decisions stopped by energy;
and processor-wait ticks from eligible response ticks that were neither
executing nor energy-blocked. Every derived count is checked non-negative and
against the job interval.

Only jobs released at or after `warmup` enter tightness/minimum-observation
counts. Deadline misses anywhere in the run remain fatal observations. Jobs
whose release occurs too near the horizon to expose their full deadline, and
all unfinished jobs at the final horizon, are censored.

## Comparison gate

A task is in the tightness common domain only when its RTA method returned a
legal candidate, the simulation reached a complete horizon without a miss,
the release-time `E0` premise and no-overflow guard hold, and at least
`minimum_jobs_per_task` eligible completed jobs were observed. Simulation is
used to search for counterexamples and estimate empirical tightness only; no
finite pass is described as a proof of RTA soundness.
