# EXT-1B B1 formal R1 contract

Status: `FROZEN_FOR_FORMAL_EXECUTION`

Current result status: `FORMAL_NOT_RUN`

This contract freezes the B1 formal inputs before any formal native
simulation.  It does not contain or summarize a formal result, does not
authorize B2/B3 or another experiment workflow, and does not permit results to
write back into the design.

The machine-readable authority is
`docs/experiments/v9_3_ext1b1_formal_r1_contract.json`.  Its canonical payload
SHA-256 is:

`6801f3184fcfb47169b2aef5176ff9345b0b10e73f71d2569445a5f1eaac4397`

## Frozen identities

- Exact base commit: `a3c723d5b8870f2b7871a1b72221740575db2495`
- Exact base tree: `4da2b3ef765ffa5ee58aa4bc2fbc1b99ff88f172`
- Simulator SHA-256: `80d3da0ed4890a5cd08e786544f2a410d18a6ca478efc69bfa5fd043fe5f60cc`
- Formal config semantic hash: `f0e8b266e7016dfa429ecc75746005d8fb4d186f41b7f5d703a1981c735293e3`
- Request-plan SHA-256: `0208022c75a5314f9e17ae4807cfd669bfb9fc9ebfca3b23efe06ee1ada1b7c4`
- Seed-ledger SHA-256: `02c70e8f533998a3161d69693219132d55af62c42caa34c89cfec73ab5ff5661`
- Formal domain: `EXT1B_B1_FORMAL_R1`
- Seed space: `EXT1B_FORMAL`

The original `configs/v9_3_ext1b_formal_template.yaml` remains
`UNFROZEN_FORMAL_TEMPLATE`.  Changing only its `parameter_status` cannot match
the frozen config, plan, ledger, or contract hashes and is rejected.

## Ordered design

All cells use `BYPASS_STRESS:B1`, four cores, ten tasks, constrained deadlines,
RM priority, a 400-tick horizon, no horizon extension, and the nine frozen
energy-aware GPFP schedulers.

| # | Stratum | Cell | U | eta | rho | Base seed | Bootstrap seed |
|---:|---|---|---:|---:|---:|---:|---:|
| 0 | LOW | `u2of5_eta4of5_rho3of4` | 2/5 | 4/5 | 3/4 | 971812602 | 1605045777 |
| 1 | LOW | `u3of5_eta1_rho3of4` | 3/5 | 1 | 3/4 | 679148436 | 927139569 |
| 2 | MEDIUM | `u3of5_eta4of5_rho3of4` | 3/5 | 4/5 | 3/4 | 1371499604 | 1537368511 |
| 3 | MEDIUM | `u3of5_eta4of5_rho1of4` | 3/5 | 4/5 | 1/4 | 1758024457 | 665932110 |
| 4 | HIGH | `u4of5_eta4of5_rho3of4` | 4/5 | 4/5 | 3/4 | 546579808 | 873693510 |
| 5 | HIGH | `u4of5_eta3of5_rho1of2` | 4/5 | 3/5 | 1/2 | 1129661266 | 913691139 |

Each cell contains 200 newly generated paired tasksets and 1,800 requests.
The fixed totals are 1,200 pairs, 10,800 requests, and 10,800 required retained
traces.  Each LOW/MEDIUM/HIGH stratum contains exactly 400 pairs.

Logical taskset indices are 10000 through 10199 inclusive.  Sixteen
outcome-independent structural attempts give the complete source-index domain
160000 through 163199 inclusive.  The seed ledger enumerates all 19,200
preimages and all 19,200 derived 31-bit seeds, not merely the accepted attempt
for each taskset.

## Execution policy

The future AutoDL roots are frozen as:

- Output: `/root/autodl-tmp/asap_block_v9_3_ext1b_b1_formal_r1`
- Taskset store: `/root/autodl-tmp/asap_block_v9_3_ext1b_b1_formal_r1_tasksets`

The six ordered cells are six fixed shards.  Each shard has one worker; at most
four shards may execute concurrently.  Each request permits one native attempt
with a 30-second timeout and no native retry.  Structural generation uses only
attempts 0 through 15 with a 120-second generator timeout.  A timeout,
structural exhaustion, or internal error is retained as evidence and is not
permission to replace a taskset.

## Fixed denominators

The analysis unit is the paired taskset.  Denominators are unconditionally
fixed at 200 per cell, 400 per stratum, and 1,200 overall.  Deadline outcome,
bypass activation, scheduler performance, effect direction, or a failed
scientific replication criterion may not exclude a taskset or reduce those
planned denominators.

Every report must separately give planned, terminal, observable, comparison,
not-applicable, and technical-failure counts.  A request is mechanically
comparison-eligible exactly when its release-E0 check is valid and its terminal
status is either `SIM_PASS_OBSERVED` or `SIM_DEADLINE_MISS`.  A named paired
contrast additionally requires the two exact frozen input identities to match.
Both pass and deadline miss are eligible; bypass occurrence and effect direction
are not eligibility conditions.  Scheduler-inapplicable metrics are reported
as not-applicable, never as zero.

## Technical validity hard gates

Technical validity requires all of the following:

- 1,200/1,200 complete ordered nine-way pairs;
- 10,800/10,800 identity-matching terminals;
- 10,800/10,800 retained traces;
- zero duplicate preimages, seeds, pair identities, or request identities;
- zero generation, fairness, pair-input, terminal-identity, trace, parser,
  internal, runtime-timeout, or horizon-insufficient failure;
- exact request/terminal/trace/manifest/hash coverage with no missing or extra
  file.

A technical gate failure gives `B1_FORMAL_TECHNICAL_VALIDITY_FAILED`.  It is
reported rather than hidden by exclusion or regeneration.

## Scientific replication criteria

The scientific criteria are deliberately separate from technical validity:

- structural activation is 200/200 in every cell;
- runtime observable is 200/200 in every cell;
- raw native ASAP-NONBLOCK bypass activation is at least 180/200 in every cell;
- formal runtime activation is at least 180/200 in every cell;
- the preregistered stratum intensity order is
  `median_LOW < median_MEDIUM < median_HIGH`, using raw ASAP-NONBLOCK
  `bypass_count / 400 * 1000`.

If these criteria are not met after the technical gates pass, the result is
`B1_FORMAL_SCIENTIFIC_REPLICATION_NOT_CONFIRMED_DATA_VALID`.  The data remain
valid; no taskset is regenerated, no denominator is reduced, and no contract
field changes.

## Estimands and bootstrap

Primary estimands are per-cell and fixed-stratum ASAP-NONBLOCK bypass activation
proportions, bypass-intensity medians with percentile 95% confidence intervals,
and the preregistered LOW/MEDIUM/HIGH median order.

The eight ASAP-BLOCK-versus-comparator contrasts are secondary descriptive
estimands.  A binary effect is the mean of within-pair binary differences.  A
continuous effect first takes one within-taskset difference and then reports
the median of those paired differences.  Effects and 95% intervals are
reported; there is no unadjusted significance decision and no p-value gate.

The deterministic nonparametric percentile bootstrap has 10,000 resamples and
uses paired taskset as its unit.  A cell resample draws 200 identities with
replacement from that cell's fixed 200.  A stratum resample independently
draws 200 from each of its two cells and merges the draws to 400.  Overall
resampling independently draws 200 within each of all six cells.  Percentile
endpoints use linear interpolation at `p*(B-1)`.

## Censoring, stopping, manifests, and archives

Horizon-censored jobs remain censored and are never fabricated as completion or
miss.  Missing values are not imputed.  An incomplete pair remains in planned
and technical-failure counts, fails the technical gate, and is excluded only
from the named pairwise effect that cannot mechanically be computed.

There is no outcome-, effect-, p-value-, or scientific-criterion-based early
stopping.  Only a P0 identity, config, plan, seed, fairness, simulator, or
manifest mismatch stops technical execution while preserving evidence.

Every shard manifest and the root manifest require exact recursive coverage
and SHA-256 for every file; extras fail.  A completed resume must be byte
idempotent and invoke native simulation zero times.  Any later archive is
external to Git, deterministic, identified by name/size/member count/SHA-256,
and verified after a temporary unpack.  Results, tasksets, traces, binaries and
tarballs are prohibited from this commit.

## Freeze boundary

Plan materialization invoked the native simulator zero times.  No formal
terminal, trace, result, statistic, or scientific outcome was read or produced.
AutoDL was not accessed.  Until a separately controlled execution consumes
this exact contract, the only valid status is `FORMAL_NOT_RUN`.
