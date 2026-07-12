# ASAP-BLOCK v9.3 / v1.3.12 CORE-0A invalidation and rebuild record

## Frozen invalidation

The former CORE-0A claim is **INVALIDATED**. Neither its reported PASS nor its
summary counts are an input to this rebuild.

- invalidated implementation/test commit: `6de2015e0fc3404e2c0610e648e763018d1877a6`
- invalidated evidence commit: `dcb55f6a22f4d772a74f94ac7799b79cf5da8541`
- invalidated evidence ZIP SHA-256: `d56c2f671b8ea201e6e53a4199cba333f3dcc6eb1e09ff06a1bfa8b76db8dd50`
- reason: the independent audit found non-independent oracles, summary-first
  evidence, non-real mutations, duplicate dominance samples, an incomplete
  result-validator invocation, and an evidence artifact that did not pass the
  claimed artifact validation scope

The old ZIP is historical failure material only. It is not current-authoritative
evidence and is not an incremental replay baseline.

## Rebuild baseline and authority boundary

The rebuild starts from the last trusted pre-CORE-0A commit:
`038ad9c79261b3b025661c796a65c68b2b39b1e2`.

The frozen theory SHA-256 remains
`524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e`.
The frozen v1.3.12 contract ZIP SHA-256 remains
`b67882290d4d4688a0e81fd98f95e9d998537facfb9f5945d1ec125143959895`.
Neither authority surface is modified by this rebuild.

Pilot execution is not authorized. This rebuild ends at a package suitable for
a new independent CORE-0A audit; it does not authorize progression to pilot.

## Independent audit material preservation

The following invalidation inputs are preserved verbatim as audit records:

- `v9_3_core0a_independent_evidence_audit.md`
- `v9_3_core0a_independent_replay.json`
- `v9_3_core0a_mutation_replay.json`

They contain no absolute workspace path or temporary-directory path. They are
documentation inputs only and are excluded from the formal contract semantic
hash, raw instance input hashes, build-identity preimage, and gate-bundle
preimages.

## Two-commit provenance rule

The implementation commit (commit A) contains the production fix, independent
oracles, row-level evidence producer, independent aggregator, mutation harness,
package validator, and tests. Evidence is generated from a clean worktree at
that exact commit. The evidence commit (commit B) contains the generated package
and records commit A explicitly as `implementation_commit_sha`; it does not
claim that commit B was the executed runtime build.
