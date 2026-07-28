# B4-PE freeze candidate v3

This candidate supersedes candidate v2 only for the I5B observability and
analysis contracts. It does not change the frozen B4 v5.2 scientific design,
task generation, seeds, case counts, source identity, or algorithm order.

The bound runtime contract is trace schema 3 with observability summary
contract v2. Activation is explicit:

```text
--b4-observability-summary
--b4-summary-horizon 30000
--b4-observability-contract-version 2
```

Each task must have at least 100 independently recomputed adjudicable jobs.
An adjudicable job has absolute deadline less than or equal to `H_B4`.
Deadline-miss denominators and mechanism opportunity denominators use
`NA` when zero; I5C emits only authoritative counts and performs no I5D
statistics.

This remains a candidate and is not final until RTA integration and independent
review: no formal run, final code commit, runtime binary, tag, or I5D analysis
is authorized by this document.
