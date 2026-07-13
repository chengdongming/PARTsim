# CORE-0A rebuild2 non-circular identity design

The evidence identity is split into three immutable layers:

1. **A2 implementation identity.** A clean implementation commit is hashed by
   `core0a_v9_3_build_identity.py`. Every raw row and runtime manifest records
   that build identity and the concrete A2 commit.
2. **B2 evidence identity.** The complete generated directory, ZIP, reports,
   manifests, and sidecars are committed without claiming that B2 was their
   runtime. They bind A2, the raw manifest hash, runtime manifest hash, and a
   canonical evidence tree hash. No member contains the future B2 commit ID.
3. **Post-B2 authoritative pointer.** After B2 exists, an external sidecar
   `artifacts/v9_3_v1_3_12_core0a_rebuild2.current_authoritative.json` is
   generated. It is deliberately outside the ZIP and B2 evidence tree and
   contains the concrete A2 commit, B2 commit, ZIP filename/SHA-256, runtime and
   raw manifest SHA-256 values, status, generated-from identity, and evidence
   tree hash. It contains no placeholder. Its own SHA-256 is reported with B2.

This is the same separation as tag-like metadata: changing or recreating the
pointer cannot change B2, the ZIP, or any digest named by the pointer. The
pointer status may authorize submission to a third independent CORE-0A audit;
it cannot authorize pilot execution. Both invalidated predecessor packages are
listed verbatim in the raw/runtime manifests, reports, and pointer.
