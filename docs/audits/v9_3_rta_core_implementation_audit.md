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
