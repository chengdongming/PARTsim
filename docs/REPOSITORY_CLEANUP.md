# Current research repository cleanup

The exact pre-cleanup state is available on
`archive/pre-minimal-research-20261128` (commit
`3c5e3902eea45a880b0a4dc9593ec83746f9d64a`).
The current tree drops obsolete experiment families, their old contracts,
proof drafts, fixtures, runners, and dedicated tests rather than retaining
duplicate versions in master.

Shared live routines were extracted into `experiments/pipeline/` under
descriptive names. This is a layout cleanup, not a change in scheduling,
harvesting order, or the mathematical RTA. The obsolete document-dependent
runtime gate was removed; the historical numeric and input identity labels
remain for provenance. The frozen unlimited-battery proof/analysis bundle
is intentionally unchanged because the current RTA uses its engine and
verified input generator. Its internal filenames and hashes must stay intact.

## Verification

- The native simulator was rebuilt from the 92 translation units in the
  project build lists, including the updated embedded streaming-validator
  import.
- 44 maintained Python unittest cases passed: same-slot energy, HEARTS-1C,
  current research campaigns, repository layout, and frozen reproduction.
- For identical small baseline and cleaned runs, all 18 nine-strategy
  scientific result rows matched (excluding runtime); the summary and
  invariant reports were byte-identical.
- All 10 HEARTS-1C comparison result rows matched (excluding the three
  runtime fields); the paired comparison table was byte-identical.
- All six constructed and four coverage same-slot RTA result rows matched
  (excluding runtime and source-provenance hashes).
- Extracted live function/class bodies were compared by Python AST to their
  pre-cleanup versions. Stable input/seed identities were preserved.

These are targeted regression and smoke checks, not a rerun of the full
publication campaigns. Pytest and GoogleTest were not installed in the
verification environment; their complete suites were not executed.
