# ASAP-BLOCK v9.3 Pilot-2 execution plan

This pilot answers only two design questions: whether the 15-second
per-configuration timeout is too short, and where the existing complete/local
formulations produce a non-vacuous strict response-time difference. It is not
a formal experiment or a final statistical analysis.

## Frozen inputs and fail-closed boundary

- Baseline inputs come only from `artifacts/v9_3_pilot/generated_tasksets.csv`.
  They are reconstructed from the saved exact integer/rational task payload;
  task sets are never regenerated for timeout or baseline diagnostics.
- Every analysis is dispatched through the production v9.3 runner and its
  existing `analyze_taskset_v9_3()` call.
- Runs are sequential and single-worker. A numeric/internal error, dominance
  violation, malformed dependency, missing variant, or illegal certified
  candidate is P0 and stops later phases.
- Theory, the fixed carry-in interface, v1.3.12, the task-generator semantics,
  C++ schedulers, v20.4/v21, and first-pilot artifacts are read-only.

## Phase A: timeout sensitivity

The 27 saved 15-second TIMEOUT requests are rerun at 30 seconds. Only requests
still timing out are rerun at 60 seconds. Two saved non-timeout requests per
variant are also rerun at 30 seconds. Candidate vectors and task-set
certification are compared with their saved 15-second outcomes. Timing separates
worker startup, solver wall and CPU time, pickle serialization/deserialization,
transport/worker-exit overhead, and total parent wall time.

The temporary recommendation is deterministic: unresolved 60-second timeouts
mean `FURTHER_EVALUATION`; any completion requiring 60 seconds recommends 60;
otherwise 30 is recommended if every recovered request has at least the
configured ten-percent headroom, and 60 is recommended if it does not.

## Phase B: baseline tightness

For the same-carry pairs `LOC_D/CW_D` and
`LOC_THETA_CW/CW_THETA_CW`, the core trace observer replays the saved inputs.
Exact envelope values are joined on identical task, carry-in vector, and
`w/h/q`. Closure is compared per `w/h`; candidates are compared per task.
`LOC_THETA_LOC/CW_THETA_CW` is candidate-only because its recursive carry-in
may differ.

The baseline generator already assigns one of six workloads and the saved
inputs contain four distinct exact power values. Consequently the repository
has no separate homogeneous baseline mode to contrast with an "existing
heterogeneous" mode. S3 and S4 are therefore predeclared aliases of S1 and S2,
respectively; they remain separately seeded and fully reported rather than
inventing a new generator option.

## Phase C: screening and optional confirmation

Eight cells use four declared structures, normalized utilization 0.4/0.6,
five task sets per cell, `E0=1`, `M=4`, `n=10`, and seed base 930112. The
generator's existing `--constrained-deadlines` option is the only structural
switch. Every cell runs all five variants at the temporary Phase-A timeout.

A differentiating cell must have no dominance/numeric/internal error, at least
ten common candidate tasks, at least one strict candidate improvement, and no
more than 20% timeout unless Phase A resolves the cutoff. At most two cells are
ranked by strict improvements, timeout rate, then common count and confirmed on
20 fresh task sets using seed base 930212.

All tested cells, including aliases and cells without improvements, are kept in
the output. The final hash list covers the simple Pilot-2 artifact set only; it
does not create or update authoritative pointers, CORE-0A evidence, or machine
contracts.
