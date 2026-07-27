# B4-PE freeze candidate v2

This candidate supersedes `b4_pe_freeze_candidate_v1.json` without changing
the frozen scientific parameters, task generation, seeds, source identity,
case counts, or algorithm order. The v1 identity protocol remains the
scientific authority.

Candidate v2 activates trace schema 3 for Pilot, Formal, and Negative results
with summary contract version 1 and `H_B4=30000`:

```text
--b4-observability-summary --b4-summary-horizon 30000
```

The machine-readable authority is `b4_pe_freeze_candidate_v2.json`. It binds
the byte identities of candidate v1, the observability contract, all three v2
protocols, and the Pilot, Formal, Negative, and combined v2 Manifests.

Schema2 remains available only for explicitly `not_for_paper` integration
smoke compatibility. Schema3 smoke is also `not_for_paper` and validates the
same observability contract used by formal execution.

This remains a candidate: the final code commit, git tag, formal runtime
binary path, and formal runtime binary SHA are null. Formal runs are not
authorized until I6 RTA integration and the final freeze review. Silent
changes are forbidden.
