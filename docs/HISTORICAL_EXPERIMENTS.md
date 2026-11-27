# Archived experiments

The exact repository before removing obsolete families is preserved at
[archive/pre-minimal-research-20261128](https://github.com/chengdongming/PARTsim/tree/archive/pre-minimal-research-20261128)
(commit `3c5e3902eea45a880b0a4dc9593ec83746f9d64a`). It contains the full
old RTA4 CORE-1–5, B4-PE, PERF-G, RTA-LOAD-CROSS, SENS-SMALL, deadline and
priority/energy sensitivity studies, historical validators, old theories,
contract YAML, test fixtures, original runners and their tests. The earlier
archives are also retained.

The current master is intentionally focused on the nine-strategy comparison,
same-slot RTA, HEARTS-1C and partitioned/global comparisons. Common numerical
and configuration functions were extracted from the retired modules to
preserve behavior. The old complete RTA solvers and experimental pipelines
are no longer in the current source tree.

Old debit-before-harvest results cannot be relabeled as same-slot results.
Use the recorded historical commit to reproduce sealed results.
The V7.2 standalone frozen reproduction remains in master because the
new RTA depends on its reviewed analysis engine and verified input generator.
Its internal version names/paths are part of that reproducibility contract
and are not renamed.
