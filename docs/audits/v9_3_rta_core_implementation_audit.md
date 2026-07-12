# v9.3 RTA mathematical-core implementation audit

## Scope and authoritative source

The sole mathematical source used by this change is
`docs/asap_block_rta_multicore_complete_and_local_paper_ready_v9_3_fixed_carry_in_interface.md`
(2,264 lines). The document was present before implementation as an untracked
workspace file and was read in full. It was not edited by this work.

This phase is limited to the v9.3 mathematical core and tests. It does not add
the five-configuration joint-certification state machine and does not connect
v9.3 to an experiment runner or a formal output schema.

## Repository and baseline state

- Branch: `implement-v9.3-rta-core`
- Baseline commit: `368652182a7adbdc295a525fc0e46a6592eccbdf`
- Pre-change status: only the authoritative v9.3 document was untracked.
- Existing RTA test entry used for the baseline:
  `python3 -m pytest -q test/test_*rta*.py`
- Baseline result: 203 passed, 0 failed, 0 skipped in 17.25 seconds
  (17.62 seconds wall time from `/usr/bin/time`).

## Pre-existing implementation map

### Frozen v20.4 path

`asap_block_rta.py` identifies itself as `RTA_VERSION = "v20.4"`.

| Theory role | Pre-existing v20.4 function | Audit conclusion |
| --- | --- | --- |
| Parameterized workload | `workload_bound` | Algebraically related workload formula, owned by v20.4 and retained unchanged. |
| Effective hp workload | `_processor_workloads` | Applies the `w-C_k+1` cap. |
| Processor delay | `processor_delay` | Directly scans the defining inequality up to `sum(bar_W)//M`. |
| Energy state/envelope | `_deadline_energy_states_for_z`, `_prefix_energy_upper_bound_exact` | Old `(w,x,z)` Omega/max-cost-flow construction, not the v9.3 `y_k/z` envelope. |
| Energy blocking | `_energy_blocking_bound_result` | Old prefix/blocking computation. |
| Candidate closure | `response_time_bound` | v20.4 outer fixed-point iteration, not the v9.3 pointwise `w/h/q` scan. |
| Version dispatch | `RTA_VERSION`, CLI `main`, `analyze_taskset` | Remains the default historical analyzer. |

### Frozen v21 path

`asap_block_rta_v21_local_window.py` identifies itself as
`RTA_VERSION = "v21-local-window"` and imports v20.4 as `v20`.

| Theory role | Pre-existing v21 function | Audit conclusion |
| --- | --- | --- |
| Workload | `local_workload_bound` | Delegates to frozen v20.4. |
| Processor progress | `processor_reference_length` | Delegates to v20.4 processor delay. |
| Local energy state | `local_omega_extrema_for_z`, `local_g` | Old local Omega projection with `(a_i,b_i,u_i,c_j)`-style bins. |
| Inner closure | `close_delta` | Contains empty-set handling and `delta` jumps. These are explicitly excluded from v9.3. |
| Candidate closure | `local_energy_blocking_bound`, `response_time_bound_v21` | Scans prefix `x` and performs v21 inner closure, not the normative v9.3 `h/q` predicate. |
| Version dispatch | v21 CLI `main`, `analyze_taskset_v21` | Retained unchanged and isolated. |

The ablation module `asap_block_rta_ablation_variants.py` is also based on the
v20.4 objects and policies. It is an experiment-facing historical path and is
outside this phase.

## Differences between the old paths and v9.3

1. v9.3 defines `W_i^theta(L)` with explicit `C <= theta <= D <= T`
   preconditions and uses the exact integer formula as its public core object.
2. v9.3 shares exactly one processor-progress term
   `A_k^Theta(w) = C_k + D_k^{P,Theta}(w)` between complete and local modes.
3. v9.3 complete and local envelopes optimize integer vectors containing the
   target energy term `y_k P_k`; they differ only in workload coverage `w`
   versus `q+h`.
4. v9.3 Section 12.1 reduces the exact envelope to bounded power-sorted prefix
   values while enumerating every legal `y_k` and `z`. The old max-cost-flow
   Omega formulations are not reused.
5. v9.3 closure scans every `w`, every legal `h`, and every `q`, with service
   length `h+q-1`. It does not use the v20.4 fixed point or the v21 inner fixed
   point, empty-set guard, projection, or jump rule.
6. v9.3 theorem-backed comparisons use exact rationals and conservatively
   return an unproven status for timeout, numeric error, or overflow.

## v9.3 implementation map

The new core is isolated in `asap_block_rta_v9_3.py` and identifies itself as
`RTA_VERSION_V9_3 = "v9.3-exact-core"`.

| v9.3 mathematical object | Implementation |
| --- | --- |
| Exact numeric domain | `exact_fraction_v9_3`, `V93Task` |
| `N_i^theta(L)`, `W_i^theta(L)` | `workload_bound_v9_3` |
| `W_i^D(L)` | `deadline_workload_bound_v9_3` |
| `bar W_i,k^{P,Theta}(w)` | `effective_hp_workloads_v9_3` |
| Definition-scan `D_k^{P,Theta}(w)` | `processor_delay_definition_scan_v9_3` |
| Exact optimized `D_k^{P,Theta}(w)` | `processor_delay_v9_3` |
| `A_k^Theta(w)` | `processor_progress_v9_3` |
| Bounded multiset prefix values | `_bounded_prefix_value` |
| `E_k^{Theta,cw}(w,q,h)` | `complete_window_envelope_v9_3` |
| `E_k^{Theta,loc}(w,q,h)` | `local_window_envelope_v9_3` |
| Shared Section 12.1 algorithm | `exact_energy_envelope_v9_3` |
| Section 9.1/12.2 finite scan | `canonical_closure_search_v9_3` |
| Conservative solver outcome | `V93SolverStatus`, `V93SearchResult` |

The independent test-only direct-vector oracles are
`brute_force_complete_envelope` and `brute_force_local_envelope` in
`test/v9_3_bruteforce_oracle.py`. They do not call the specialized exact
envelope or its prefix helper.

## Preserved paths and changed files

The v20.4, v21, ablation, runner, analyzer, schema, task generator, C++
scheduler, and experiment-parameter paths are preserved unchanged.

Files introduced by this phase:

- `asap_block_rta_v9_3.py`
- `test/v9_3_bruteforce_oracle.py`
- `test/test_asap_block_rta_v9_3.py`
- `docs/audits/v9_3_rta_core_implementation_audit.md`

The authoritative v9.3 theory document is deliberately not listed as a
changed implementation file because it pre-existed this work and was not
modified.

## Verification record

- New v9.3 tests: 17 passed, 0 failed, 0 skipped.
- RTA Python regression: 220 passed, 0 failed, 0 skipped.
- Full Python regression: 595 passed, 0 failed, 62 skipped.
- Random seed: `0x93A5B10C`; 10,000 generated envelope instances completed.
- Random fast/direct-vector comparisons: 20,000 (complete and local for every
  instance), with 0 differences.
- Frozen small-domain envelope instances: 160, with 320 fast/direct-vector
  comparisons and 0 differences.
- Random pointwise local/complete dominance instances: 10,000, with 0
  violations. Random closure-implication instances: 1,000, with 0 violations.
- CTest: 163 of 166 passed. The three failures are the existing, unrelated
  `Scheduler.FIFO`, `Scheduler.TrueFIFO`, and `Scheduler.RM` C++ tests. No C++
  source, scheduler, or test was changed in this phase.

## Independent adversarial audit of commit ce7ea484

This section records findings from a fresh parent-to-commit audit.  It does not
rely on the implementation summary above.  Before any repair, the following
minimal counterexamples were reproduced against `ce7ea484`:

1. **P0 -- a negative injected envelope could produce a candidate.**  For
   `k=(C,D,T,P)=(1,1,1,1)`, `M=1`, no hp/lp tasks, `E0=0`, and zero service, an
   envelope callback returning the exact value `Fraction(-1)` produced
   `CANDIDATE(w=1,h=0)`.  A negative energy envelope is outside the mathematical
   codomain and must cause a conservative numeric failure, never a candidate.
2. **P0 -- non-finite timeout clock readings could produce a candidate.**  On
   the same instance with a one-second timeout, clocks returning either `NaN`
   or `+Inf` produced `CANDIDATE(w=1,h=0)`, because the elapsed-time comparison
   was false for `NaN`.  Operational numeric failure must be conservative.
3. **P1 -- invalid public inputs were not rejected consistently.**  The public
   processor-progress path accepted `w=0` and `w=2` for the same `C=D=1` target,
   and a beta callback returning `Fraction(-1)` was accepted and merely led to
   `NO_CANDIDATE`.  Candidate windows must satisfy `C_k <= w <= D_k`, and beta
   values must be non-negative exact numbers.
4. **P1 -- required adversarial coverage was incomplete.**  The committed tests
   did not exercise the two mutation checks, non-finite clocks, negative exact
   envelope/beta values, deterministic repeatability, or a complete `q` break
   and next-`h` restart trace.  The closure-dominance test rejected a vacuous
   run but did not report its actual `N_cw_closed` value.

Regression tests for these counterexamples are added before the corresponding
minimal implementation repair.  Final post-repair commands, counts, mutation
results, and disposition are recorded below after verification.

### Formula-by-formula disposition after repair

- `W_i^theta(L)` is the Section 3 integer formula.  Its shifted numerator is
  non-negative under `L >= 0` and `C <= theta`; it uses only `//`, integer
  multiplication, subtraction, and `min`.  A frozen exhaustive parameter domain
  now checks monotonicity in both `L` and `theta` across multiple period edges.
- `bar W`, the definition scan and optimized `D_k^P`, and `A_k` match Section 4.
  The definition upper bound is `floor(sum(bar_W)/M)`, which covers every
  feasible positive `d`; the optimized discrete-concave predicate matched the
  definition scan on 2,000 seeded instances.  No-hp returns zero.  Candidate
  windows outside `C_k..D_k` are now rejected by both public progress paths.
- The complete and local envelopes share all constraints and differ only in the
  workload coverage (`w` versus `q+h`).  Every `y_k` and `z`, including both
  endpoints, is enumerated; lp capacities are rebuilt for every `y_k`; target,
  lp, hp, BLOCK, and total-capacity terms match Section 12.1.  Powers are exact
  positive `Fraction` values, and prefix sorting uses exact comparisons only.
- The canonical search scans `w=C_k..D_k`, skips only a current `w` when `A>w`,
  scans every legal `h`, scans `q=1..A`, uses service index `h+q-1`, stops only
  the current `q` loop on failure, and returns the first closing `w`.  Trace tests
  cover a failed `q2` with no `q3`, restart at `q1` for the next `h`, a failed
  middle `h`, later success, multiple failed `w`, `A>w`, `D=C`, and the service
  off-by-one.  Since `A=C+D^P` and `C>=1`, `A=0` is impossible and is asserted
  through the lower bound `A>=C`.
- No cache, mutable default, approximate comparison, binary/jump search over
  `w/h/q`, old fixed point, v20.4/v21 import, or shared mutable state exists.
  Repeating the same complete input produces an equal result including the
  candidate, witness, and all four counters.

### Oracle independence and adversarial tests

`test/v9_3_bruteforce_oracle.py` directly enumerates every target, hp, and lp
integer component and independently checks the lp BLOCK and total-capacity
constraints.  It shares only `EnvelopeKind`, task records, and a separately
written workload formula; it does not call the fast capacity builder, sort,
prefix helper, or selection loop.  Two direct hand cases yield 14 (target only)
and 22 (lp saturation), and a patched-fast-path guard confirms that the oracle
still runs when the fast envelope and prefix helper are made unusable.

The temporary mutations were process-local and left no worktree changes:

- deleting `y_k * P_k` made the named fast/brute test fail (`0 != 14`, pytest
  exit 1);
- changing local coverage from `q+h` to `w` made the local index test fail
  (`{5} != {3}`, pytest exit 1).

Thus both required mutations are detected; neither test is a false negative.

### Final verification record

- `python3 -m pytest -q -s test/test_asap_block_rta_v9_3.py`: exit 0, 22 passed,
  0 failed, 0 skipped in 4.72 seconds (4.87 seconds wall).
- `python3 -m pytest -q test/test_*rta*.py`: exit 0, 225 passed, 0 failed,
  0 skipped in 20.34 seconds (20.63 seconds wall).
- `python3 -m pytest -q`: exit 0, 600 passed, 0 failed, 62 skipped, 32 unrelated
  legacy-provenance warnings in 62.92 seconds (63.25 seconds wall).
- `python3 -m compileall -q asap_block_rta_v9_3.py
  test/test_asap_block_rta_v9_3.py test/v9_3_bruteforce_oracle.py`: exit 0.
- Random seed `0x93A5B10C`: exactly 10,000 legal inputs completed, producing
  10,000 complete and 10,000 local fast/direct-vector comparisons; mismatches
  0.  Assertions require single/multicore, absent/present hp and lp, both hp/lp,
  tied/heterogeneous powers, `q+h=w`, and `q+h<w` to occur.  Named cases cover
  target-only, `y_k=0` optimum, `y_k>0` optimum, and capacity saturation.
- Frozen domain: exactly 160 inputs and 320 complete/local comparisons;
  mismatches 0.
- Pointwise local/complete checks: 10,000, dominance violations 0.  Closure
  implication checks: 1,000, `N_cw_closed=185`, violations 0; zero closures is a
  hard test failure.

The module implements optional conservative timeout handling and maps a raised
`OverflowError` to `UNPROVEN_OVERFLOW`.  Python integers are arbitrary precision,
so there is no internal fixed-width integer overflow mechanism to claim.  The
future runner remains responsible for orchestration and any outer operational
limits; no runner, schema, five-configuration logic, joint certification, or
formal experiment support is implemented here.

### Final classification and disposition

- Confirmed and repaired: two P0 conservative-failure defects and two P1 groups
  listed above.
- Remaining P0/P1/P2 defects in the audited mathematical core: none reproduced.
- Modified files are limited to `asap_block_rta_v9_3.py`,
  `test/test_asap_block_rta_v9_3.py`, and this audit document.  The independent
  oracle required no repair.
- After these repairs, the isolated v9.3 exact mathematical core is independently
  confirmed against the authoritative formulas and may be used as the basis for
  the separately scoped five-configuration and joint-certification phase.  A
  solver `CANDIDATE` remains only a per-task closure candidate and is not named or
  represented as a task-set `CERTIFIED` outcome.
