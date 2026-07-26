# B4-PE I4A deterministic manifest layer

This directory contains the frozen I4A-0 identity protocol and the I4A
planning-only manifest layer. `protocol_resolution_v1.json` remains the sole
machine source for canonical keys, seeds, IDs, phase algorithms, phase counts,
taskset reuse, and deterministic source identity. The manifest protocol adds
only the experiment matrix, public CLI mapping, planned artifact paths,
timeouts, retry policy, and exact JSONL case shape.

The I4A layer does not materialize system configurations, tasksets, source
traces, or results. It never starts the simulator. Planned scheduler-specific
system configurations are represented by relative paths and are a later-stage
materialization responsibility.

Generate a deterministic manifest (the default destination is under `/tmp`):

```text
python3 experiments/b4_priority_energy/generate_manifest.py \
  --phase pilot
```

Validate and audit it:

```text
python3 experiments/b4_priority_energy/validate_manifest.py \
  --manifest /tmp/b4_pe_i4a_manifest_pilot.jsonl
python3 experiments/b4_priority_energy/audit_manifest.py \
  --manifest /tmp/b4_pe_i4a_manifest_pilot.jsonl --json
```

Preview argv arrays without execution:

```text
python3 experiments/b4_priority_energy/run_manifest.py \
  --manifest /tmp/b4_pe_i4a_manifest_pilot.jsonl \
  --dry-run --limit 3 --output-root /tmp/b4_pe_i4a_outputs
```

Omitting `--dry-run` is still preview-only. `--execute` fails explicitly with
`execution is not implemented in I4A`. No command is passed to a shell or to
`subprocess`.

I4B-1 adds a separate, sequential execution kernel without changing the I4A
manifest contract. It requires explicit `--execute`, an absolute executable
simulator path, and an output root outside this repository. It validates the
entire manifest before execution, uses non-blocking per-case POSIX locks,
captures each attempt to files, terminates timed-out process groups, and
publishes results, logs, state, and summaries atomically.

The case lock is the transaction boundary: it covers input snapshotting,
provenance and state validation, subprocess execution, publication recovery,
and the final state commit. All four execution inputs (simulator, system
configuration, taskset, and source) are opened without following symlinks,
checked for stability while being copied, and executed from read-only,
content-addressed files below `.b4pe/snapshots/`. State records both the
original identity and the exact snapshot path and SHA used by the subprocess.
Content addressing freezes the bytes copied from the original inputs. It does
not by itself freeze the snapshot namespace, so immediately before execution
the kernel securely opens each final simulator, system, taskset, and source
snapshot and revalidates its SHA. The subprocess inherits those four final
file descriptors and receives each input only as
`/proc/self/fd/<snapshot-file-fd>`, with no suffix to resolve. Snapshot parent
directory descriptors and the output-root descriptor are not inherited. The
four descriptors remain open through process exit and are then explicitly
closed on success, timeout, and startup failure. If direct `/proc/self/fd`
access is unavailable, execution fails closed without falling back to an
original or snapshot pathname.

The state separates stable snapshot identity from runtime transport:
`snapshot_relpath` and `snapshot_sha256` are used for integrity and resume,
while `execution_transport`, `executed_proc_fd_path`, and
`executed_snapshot_sha256` describe the concrete attempt. File descriptor
numbers are runtime-only and are never part of case or manifest identity.

Output mutation is rooted at one trusted `O_DIRECTORY | O_NOFOLLOW` directory
descriptor. Each path component is opened relative to that descriptor, and
temporary result, log, state, and summary files are created in the same opened
parent directory as their final target. Publication uses `src_dir_fd` and
`dst_dir_fd`, followed by file and directory `fsync`, so renaming a checked
parent and replacing its pathname with a symlink cannot redirect a write.

A successful attempt is committed as an explicit transaction:
`prepared state -> result -> result_published state -> logs -> logs_published
state -> committed succeeded state`. If a state write fails after any file is
published, an explicit `--resume` validates the temporary/final SHA values and
finishes that same attempt without starting another subprocess. Missing or
conflicting publication evidence fails closed; there is no deletion-and-rerun
fallback.

```text
python3 experiments/b4_priority_energy/execute_manifest.py \
  --manifest /tmp/b4_pe_i4a_manifest_pilot.jsonl \
  --output-root /tmp/b4_pe_i4b_output \
  --simulator-binary /absolute/path/to/simulator \
  --execute --limit 1

python3 experiments/b4_priority_energy/inspect_execution.py \
  --output-root /tmp/b4_pe_i4b_output \
  --manifest /tmp/b4_pe_i4a_manifest_pilot.jsonl \
  --simulator-binary /absolute/path/to/simulator --json
```

I4B-1 does not materialize tasksets, sources, or system configurations and is
strictly sequential. Those inputs must already exist under the output root.
There is no force-overwrite or concurrency option. Execution summaries use
canonical JSON bytes and are stored at
`.b4pe/summaries/<summary-sha256>.json`; there is no shared `latest` file.
`inspect_execution.py` is read-only and reports active publication phases,
publication integrity errors, orphan results, missing or damaged snapshots,
snapshot provenance drift, and summary filename/content mismatches. Only
`execute_manifest.py --resume` may complete a prepared publication.
