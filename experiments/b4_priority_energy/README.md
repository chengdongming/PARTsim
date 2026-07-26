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

The validated Manifest `command_argv` is the sole semantic command plan.
I4B-1 performs only the contracted path substitutions for the simulator,
input snapshots, and attempt staging trace; it has no test callback or other
path that can append semantic arguments. In particular, I4B-1 does not derive
or append `--taskset-semantic-hash`. The upstream artifact/command bridge must
write that argument into the Manifest when it becomes required in I4B-2.

The state separates stable snapshot identity from runtime transport:
`snapshot_relpath` and `snapshot_sha256` are used for integrity and resume,
while `execution_transport`, `executed_proc_fd_path`, and
`executed_snapshot_sha256` describe the concrete attempt. File descriptor
numbers are runtime-only and are never part of case or manifest identity.

Output mutation is rooted at one trusted `O_DIRECTORY | O_NOFOLLOW` directory
descriptor. Each path component is opened relative to that descriptor. Logs,
state, summaries, and publication copies use exclusive temporary files and
trusted directory descriptors. Publication uses `src_dir_fd` and
`dst_dir_fd`, followed by file and directory `fsync`, so renaming a checked
parent and replacing its pathname with a symlink cannot redirect a write.

Each subprocess attempt has a new private `0700` directory below
`.b4pe/attempt-results/<case-id>/`. The child inherits only that directory
descriptor in addition to the four input snapshot file descriptors. Its trace
argument is `/proc/self/fd/<attempt-directory-fd>/trace.txt` or `trace.json`,
chosen from the Manifest result suffix. The basename is one component, has a
real `rtsim` trace suffix, and is verified absent immediately before `Popen`;
the executor never creates, touches, or truncates the child trace target.

Atomic publication has two explicit layers. First, `rtsim` writes its own
private partial, validates it, and atomically publishes the trace inside the
attempt directory. After a zero exit, I4B-1 opens that trace with
`O_NOFOLLOW`, requires one non-empty regular trace and no extra staging
targets, and hashes the opened file. I4B-1 copies those verified bytes to a
unique temporary file in the stable result's own parent, fsyncs that file,
revalidates the retained staging trace, and only then records prepared
publication metadata. Publication uses a same-parent `os.replace` to the
Manifest `result_relpath` followed by a parent-directory fsync. The original
staging trace remains as attempt evidence. Missing, empty, symlink, directory,
extra, or post-exit-mutated output is invalid and is never published.

The replace itself is not the success boundary. Before recording
`result_published`, I4B-1 reopens the final name through its trusted parent
directory descriptor with `O_RDONLY | O_NOFOLLOW`, requires a non-empty
regular file, checks stable metadata and two SHA-256 reads, confirms that the
final namespace still names the opened inode, and requires the observed SHA
to match the prepared SHA. A mismatch is persisted as
`failed/trace_integrity_error`; the expected and observed SHA values and the
staging/final evidence are retained, and the attempt is never committed or
retried.

A successful attempt is committed as an explicit transaction:
`prepared state -> result -> result_published state -> logs -> logs_published
state -> committed succeeded state`. If a state write fails after any file is
published, an explicit `--resume` validates the retained staging trace and the
same-parent temporary/final SHA values and finishes that same attempt without
starting another subprocess. Missing or
conflicting publication evidence fails closed; there is no deletion-and-rerun
fallback.

A timeout or failed attempt retains its private staging directory as evidence.
The only allowed retry creates a different attempt directory and a new absent
trace target. A staging trace without durable `prepared` metadata is an orphan:
inspection reports it, but resume never adopts, deletes, or publishes it.

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
snapshot provenance drift, attempt staging directories, missing/orphaned or
damaged staging traces, illegal suffixes, extra staging targets, retained
timeout/failed evidence, and summary filename/content mismatches. Only
`execute_manifest.py --resume` may complete a prepared publication; it uses
the prepared trace SHA and never adopts an unprepared staging trace.
The inspector always emits its complete report, returning zero only for a
clean integrity report and fixed status 1 when any integrity counter or
unfinished publication transaction is present.

R3B unit tests use only the fake simulator and temporary output roots. They do
not execute any real Pilot, Formal, or Negative artifact/command and do not
call a real `rtsim`.
The earlier bounded suffix/absent-target diagnostic is not automated campaign
evidence. A compliant real-artifact, real-command `rtsim` case, including its
taskset semantic hash and complete provenance assertions, is the first I4B-2
gate.
