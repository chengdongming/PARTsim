# ASAP-BLOCK v9.3 AutoDL deployment

This directory builds the production simulator, runs bounded smoke requests in
fresh output directories, resumes interrupted formal runs without deleting
checkpoints, verifies hashes/cardinalities, and packages only the selected
configs, logs, commit identity, and experiment results.

Quick start:

```bash
cp deployment/autodl/experiment.env.example experiment.env
# Edit paths/resources, then export the file's variables.
set -a; source experiment.env; set +a
bash deployment/autodl/setup_environment.sh
bash deployment/autodl/verify_environment.sh
bash deployment/autodl/build_simulator.sh
bash deployment/autodl/run_all_smoke.sh
```

Use a new `PARTSIM_OUTPUT_ROOT` for a fresh rehearsal. To continue an
interrupted smoke set, export `PARTSIM_ACTION=resume` and rerun
`run_all_smoke.sh`. Individual `run_core*.sh` and `run_ext*.sh` entrypoints use
the same controls.

Formal CORE-1/CORE-2 execution is deliberately locked. It requires an exact
per-core authorization JSON plus every audited formal config path; legacy
shell-wide `PARTSIM_FORMAL_CONFIRM` authorization is rejected.
Other CORE/EXT formal deployment entrypoints fail closed until they receive
the same file-bound authorization treatment; `run_formal_all.sh` therefore
contains only CORE-1 and CORE-2.
`resume_formal_all.sh` preserves the same output/config identity and supplies
`--resume`. EXT-2 always refuses formal mode while its status is
`REAL_TRACE_DATA_UNAVAILABLE`; only its labeled synthetic fixture smoke is
allowed.

`monitor_progress.sh` reads checkpoints without changing them.
`verify_results.sh` runs the same read-only full CORE-1/CORE-2 run-closure
validator used by aggregation before it trusts summaries, plot inputs, or
`file_hashes.sha256`. Hash verification is deliberately last: a self-consistent
replacement hash manifest is not evidence that result artifacts satisfy the
experiment contract. `package_results.sh` verifies first and excludes
unrelated or legacy files by copying only the verifier-issued package path
allowlist. The package API independently rebuilds the frozen output inventory
from each validated closure and requires every manifest field and entry to
match it exactly before copying. The archive's `package_manifest.json` records every packaged path,
regular-file type, mode, size, category, inventory schema/version, and SHA-256.
The Python package helper opens every source without following links, verifies
and copies through that same file descriptor, and anchors every destination
mkdir/open/link/unlink operation to no-follow directory descriptors. The three
final artifacts and their staging files are created, verified, exclusively
linked, reopened, and cleaned through one pinned publication-directory FD.
The caller's publication pathname must still resolve without symlinks to that
same directory inode before publication and again before success returns.
It constructs an exact-member archive and derives the complete member truth
from the strict embedded `package/package_manifest.json` plus
`package/commit_sha.txt`, rather than trusting freely declared records in the
external `<archive>.manifest.json` (`ASAP_BLOCK_V9_3_ARCHIVE_MANIFEST_V1`).
The commit also equals the packaged `run_metadata.json` `git_head` identity
when that frozen result field is present. Success publishes exactly the tar,
external archive manifest, and SHA-256 sidecar.
Rollback removes only final objects whose device/inode/type and frozen content
identity still belong to that invocation; competing or historical objects are
preserved.
For a separately invoked formal verify/package command, export
`PARTSIM_RUN_MODE=formal`; smoke is the default and requires all eight outputs.

Rendered PNG/PDF figures are non-authoritative presentation artifacts. They are
not required by the CORE-1/CORE-2 scientific output contract and are excluded
from the authoritative `file_hashes.sha256` set. When packaged, the package
manifest records their byte hashes without treating the images as scientific
content. The authoritative plot evidence is the canonical
`core1_plot_data.csv` or `core2_plot_data.csv`, which `verify_outputs.py`
rebuilds in full from the validated raw closure.

## Round 5 result-schema break

The current exact CSV schemas include `failure_origin` in both
`analysis_attempts.csv` and `per_taskset_results.csv`. The frozen values are:

- `ANALYZER_RESULT`
- `OUTER_TIMEOUT_STARTUP`
- `OUTER_TIMEOUT_CONFIGURATION`
- `IPC_RECEIVE_FAILURE`
- `INVALID_WORKER_PAYLOAD_SHAPE`
- `INVALID_WORKER_PAYLOAD_CONTENT`
- `WORKER_ERROR_PAYLOAD`
- `RESULT_VALIDATION_FAILURE`

CSV headers are exact and fail closed. Outputs created before this schema break
cannot be resumed: there is no silent migration, inferred origin, `UNKNOWN`
default, or automatic column insertion. After this fix, formal CORE-1/CORE-2
runs must use a completely new output root and must be frozen against the new
commit after it is merged. In particular, never write a new or resumed run to
`/root/autodl-tmp/asap_block_v9_3_formal_6aa9d719`.

Older campaigns are read-only historical/diagnostic evidence. If an old output
needs auditing, use a separate read-only audit script; do not point `--resume`
or a deployment launcher at it.

## Round 7 derived/output-root contract

CORE-1/CORE-2 runtime aggregation first sorts records by the frozen persisted
identity (`cell_id`, `taskset_id`, variant/analysis ID, and the record-specific
task/relation/dependency key). Finite, nonnegative binary64 runtimes are then
combined with `math.fsum` and divided by the integer sample count. Median, p95,
and maximum use the frozen value ordering. NaN, infinity, negative values,
negative zero, and booleans fail at the builder boundary.

Each CORE-1/CORE-2 output directory has one exact, machine-readable inventory:

- authoritative raw closure files, with terminal JSON and result-state pickle
  names enumerated from the legal analysis IDs;
- authoritative `summary.json`, all seven per-core canonical CSV files, and
  the canonical plot-input CSV;
- optional PNG/PDF presentation files for only the seven frozen per-core plot
  types; and
- the administrative `file_hashes.sha256` evidence.

Aggregation accepts either a fresh, complete raw closure with no derived files,
or a complete byte-valid current-schema output for an idempotent second pass.
Unknown, partial, temporary, hidden, legacy summary/comparison/ablation/plot
files and old canonical headers all fail before a write. They are never deleted
or repaired. Adding such a path to `file_hashes.sha256` cannot legalize it:
inventory validation precedes both derived comparison and hash validation, and
the hash manifest itself must contain exactly the inventory entries marked for
hashing.

`plot_cli.py` consumes only the exact `core1_plot_data.csv` or
`core2_plot_data.csv` that matches the frozen core in `run_config.yaml`. It uses
the same canonical plot-table validator as `verify_outputs.py`, including exact
header/column order, frozen plot types, row order, primary-key uniqueness,
and the physically embedded `ASAP_BLOCK_V9_3_CANONICAL_PLOT_ROWS_V3` / version
`3` per-type semantic matrix for
task ID, relation/right-variant mapping, numeric domains, outcome/status, and
cross-field associations. Each CSV row carries `plot_schema` and
`plot_schema_version`; V1/V2, missing, and unknown identities fail closed.
Runtime plot cells use canonical decimal float text (not exact-rational
fraction syntax), while runtime aggregation remains
`STABLE_IDENTITY_SORTED_BINARY64_MATH_FSUM_V1`. Producers validate their own
generated rows against that same matrix. Invalid input creates no PNG/PDF or
partial plot directory.

Do not re-aggregate or “upgrade” a historical output root in place. Old
summaries, old plot headers, and old derived namespaces cannot coexist with the
current schema. Every current-schema campaign must start in a new output root;
all prior campaigns remain read-only.
