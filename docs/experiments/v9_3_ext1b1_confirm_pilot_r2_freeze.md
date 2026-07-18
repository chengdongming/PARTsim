# EXT-1B B1 confirm pilot R2 freeze

Status: `B1_CONFIRM_PILOT_PASSED`

This record freezes the EXT-1B B1 confirm pilot R2 result identity. It is a confirm pilot, not a formal experiment result. The complete result archive is external to Git and is located by the stable archive name and SHA-256 recorded below.

## Frozen identities

- Runtime-code commit: `efdb06bd9320d6b6693850c1f40fc8fad932b240`
- Runtime-code tree: `827809cade5d7055e10d8def4703e33151474436`
- Contract SHA-256: `db172726e9ccee628ed932780e85e392ad121d9718971d88ec7092db8db1c263`
- Plan SHA-256: `f2e55900f9805399819b6002f5d8d1484861cbb7b1912016d6cdf643c0b11b21`
- Simulator SHA-256: `80d3da0ed4890a5cd08e786544f2a410d18a6ca478efc69bfa5fd043fe5f60cc`
- R2 root-manifest SHA-256: `80885be80c0668b8d3c50c1a267f79da4e72e6c7a61c0bf8a397289ea5f998cf`
- Archive: `ext1b_b1_confirm_pilot_r2.tar.gz`
- Archive SHA-256: `650e2ccea76a1e2584b12d8caa6a0a34c44decb8d90044b1f09773f638c20416`
- Archive size: 74,228,514 bytes; deterministic archive member count: 14,902
- Immutable annotated tag: `v9.3-ext1b-b1-confirm-pilot-r2` (must never be moved or replaced)

The unpacked result has 11,833 files including the root manifest, 11,832 root-manifest entries, and six cell manifests with 1,920 entries each. Exact coverage and every listed hash passed both before archiving and after a temporary unpack.

## Why R1 is historical

The original confirm-pilot execution completed its 2,700 requests and passed its numerical, trace/parser, mechanism, and stratification gates. It was not freezeable because a fully completed standard resume still refreshed each `checkpoint.json` timestamp and regenerated each cell manifest. Those twelve metadata changes made the already-frozen root manifest stale. Its historical status is therefore `B1_CONFIRM_PILOT_NEEDS_FIX`, and it is not the authoritative frozen result.

## Why R2 is metadata-only

R2 was derived without native simulation from the current post-resume R1 result tree. The six already-current checkpoints and cell manifests were retained, and only the root manifest was regenerated to bind that exact file set. R1 and R2 have the same file set; their only byte difference is the root manifest.

All 2,700 terminal files, 2,700 retained traces, 96 CSV files, request/result identities, numerical payloads, analyzer inputs/outputs, and mechanism values are byte-identical. A native rerun was neither necessary nor appropriate because the defect was confined to complete-resume metadata writes: no simulation, parser, scheduler, generator, task, parameter, or horizon semantics were changed. Two guarded complete resumes then returned successfully with zero native invocation and zero changed, missing, or extra result files.

## Frozen pilot result

- Cells: 6
- Paired tasksets and complete nine-way pairs: 300 / 300
- Requests, terminals, and retained traces: 2,700 / 2,700 / 2,700
- Mechanism gates: 6/6
- LOW/MEDIUM/HIGH intensity medians: 333.75 / 496.25 / 645.0
- Stratification: `STRATIFICATION_CONFIRMED`
- P0: none
- P1: none
- P2: retained traces lack CPU/core identity

This targeted mechanism-stress confirm pilot must not be presented as a formal population result. Any formal run must use a new seed namespace/domain and newly generated tasksets; it must not reuse the confirm-pilot tasksets as its formal sample.

The full tarball is intentionally excluded from Git history. Consumers must verify `ext1b_b1_confirm_pilot_r2.tar.gz` against `650e2ccea76a1e2584b12d8caa6a0a34c44decb8d90044b1f09773f638c20416` and then verify the unpacked root manifest against `80885be80c0668b8d3c50c1a267f79da4e72e6c7a61c0bf8a397289ea5f998cf`.
