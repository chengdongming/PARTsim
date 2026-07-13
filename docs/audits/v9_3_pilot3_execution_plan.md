# ASAP-BLOCK v9.3 Pilot-3 exact-E0 execution plan

Pilot-3 changes only the exact initial-energy lower bound. It reuses the
implicit-deadline, heterogeneous-workload generator semantics, `n=10`, `M=4`,
service curve, priority order, numerical mode, five configurations, and WCET
rounding used by the Pilot-2 baseline.

## Exact interval reconstruction and selection

All strict access rows in Pilot-2 are joined with the frozen exact service
curve. For each row the half-open separation interval is reconstructed as
`[max(0, E_loc-beta[h+q-1]), E_cw-beta[h+q-1])`. Exactly 3,675 nonempty
intervals are required. Ordinary float is forbidden in selection and coverage.

Candidates consist of every exact interval midpoint, representatives midway
between adjacent distinct endpoints, exact p05/median/p95 midpoint-neighbour
values, and controls zero/one. Coverage is counted exactly by interval,
taskset, task, relation, and utilization. Up to five non-control values are
chosen greedily by taskset coverage, task coverage, interval coverage, exact
separation from prior choices, and representation simplicity. The declared
minimum separation is `1/1000`.

## Paired screening

Ten tasksets are generated once using seed base 930312. A seed depends only on
the base seed, utilization index, and taskset index. Its canonical task payload,
power vector, semantic hash, and priority hash are reused byte-for-byte at all
selected E0 values. With seven E0 values this yields 70 paired instances and
350 ordered production requests.

Each request starts with a 60-second configuration budget. Only a TIMEOUT is
retried at 90 seconds. `LOC_THETA_CW` is dispatched after the final 60/90 CW
source for that same taskset and exact E0. TIMEOUT remains distinct from
NO_CANDIDATE.

The access-point replay is diagnostic rather than another production request.
It has one declared 60-second per-task trace window, is never retried, and is
skipped when either production task record ended in TIMEOUT. A replay timeout
keeps its observed prefix and is explicitly marked truncated; it is never
converted to a complete diagnostic or to NO_CANDIDATE. This prevents a single
90-second production timeout from expanding into four further 60/90 replays.
Exact envelope values are memoized across the seven paired E0 replays for an
otherwise identical taskset, carry vector, relation, and `w/h/q` key. The
memoized function is the production core's exact envelope function; caching
changes neither scan order nor any energy/closure comparison.

## Tightness and fail-closed checks

For deadline and fixed-CW carry-in pairs, canonical searches are replayed with
the core trace observer. The union of visited legal `w/h/q` points is evaluated
under both exact envelopes and the same exact E0/service value. Local-only
closures and their position relative to the earliest candidates are stored.
The recursive relation is candidate/certification-only.

Any numeric/internal failure, state contradiction, dependency mismatch,
missing/duplicate variant, non-paired task input, partial certification, or
dominance violation is P0 and stops the run without changing theory.

Cells are classified A through E. Only D/E cells may enter confirmation, ranked
by certification gain, strict-response tasksets/tasks, timeout rate, and common
candidate count. At most two cells receive 20 fresh tasksets from seed base
930412. If no D/E cell exists, confirmation is not executed and no extra E0
points are added.
