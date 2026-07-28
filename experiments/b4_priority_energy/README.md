# B4-PE I4A deterministic manifest layer

This directory contains the frozen identity protocol and the schema3-aware
manifest/execution layer. `protocol_resolution_v1.json` remains the sole
machine source for canonical keys, seeds, IDs, phase algorithms, phase counts,
taskset reuse, and deterministic source identity. The manifest protocol adds
only the experiment matrix, public CLI mapping, planned artifact paths,
timeouts, retry policy, and exact JSONL case shape. The current v2 Manifest
activates schema3 with `--b4-observability-summary
--b4-summary-horizon 30000`; the byte-frozen v1 protocols remain available
only for historical compatibility.

The I4A layer does not materialize system configurations, tasksets, source
traces, or results. It never starts the simulator. Planned scheduler-specific
system configurations are represented by relative paths and are a later-stage
materialization responsibility.

The P1 repair adds an explicit, unauthorized v4 draft for that later stage.
It preserves the v1 identity protocol: `taskset_id` and `source_id` remain
independent of `rho_E`, while the execution taskset path is
`artifacts/tasksets/<taskset_id>/rho-<rho_E>.yml`. The base taskset is stored
once at `artifacts/tasksets/<taskset_id>/base.yml`.

Base generation and acceptance are a separate, non-campaign CPU-only step:
four-core global GFP-RM, unchanged C/T/D/O, priority energy disabled,
30,000 ms horizon, and zero deadline misses over every adjudicable job. It
writes a canonical base-pool admission inventory. The materializer never
generates, skips, replaces, or resamples a base taskset; it accepts only a base
whose admission entry, system configuration, SHA-256, semantic hash, seed,
pool, utilization, and replicate identity all validate. It then writes one
canonical `task_energy_factor` into every task's `params`, records the actual
execution bytes and semantic hash in a canonical inventory, and never starts
the simulator:

```text
python3 experiments/b4_priority_energy/generate_manifest_v4.py \
  --phase pilot --output /tmp/b4_pe_v4_pilot.jsonl
python3 experiments/b4_priority_energy/admit_base_tasksets.py \
  --manifest /tmp/b4_pe_v4_pilot.jsonl \
  --output-root /absolute/outside-repository-root \
  --simulator /absolute/path/to/rtsim
python3 experiments/b4_priority_energy/materialize_manifest.py \
  --manifest /tmp/b4_pe_v4_pilot.jsonl \
  --output-root /absolute/outside-repository-root
```

Base admission, materialization, manifest, execution, and candidate v4 remain
drafts with Pilot, Formal, Negative Control, and paper-result authorization
all set to false. CPU-only admission results are gate evidence, not paper
results. The v1-v3 protocol and candidate bytes remain historical identities.

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

I4B-2A uses a second, fixed validation gateway for exactly one non-campaign
record. `execute_manifest.py --integration-smoke-record /absolute/record.json
--execute` accepts the historical schema2 v1 record and the schema3 v2
record: the record file and its
absolute output root must be outside the repository, its case id is in the
`smoke-` namespace, and its result is below `integration-smoke/results/`.
The record fixes `campaign_started=false`, `campaign_result_count=0`, and
`not_for_paper=true`. It cannot select a validator, name a Python module, use a
formal phase, or target Pilot/Formal/Negative paths.

The formal `--manifest` path still calls only the frozen I4A Manifest validator.
The smoke path calls only `validate_integration_smoke_record()`. After either
fixed validator succeeds, both paths enter the same I4B-1 execution kernel and
therefore share its snapshots, staging, retry, recovery, locks, publication,
state, and execution-summary rules. Both are `not_for_paper`; schema3 v2 uses
the formal summary contract while schema2 v1 is a compatibility check. No
second subprocess or publication state
machine exists. The smoke command remains the sole semantic argv plan; source
descriptor bytes use the existing `B4PE_SOURCE_SNAPSHOT` file-descriptor
transport, and no semantic-hash argument is appended by the gateway.

## I5C deterministic analysis extraction

`extract_analysis.py` converts an already completed and strictly audited
result tree into deterministic case-level and task-level data for I5D. It is
read-only with respect to execution artifacts and does not calculate
statistics, averages, rankings, confidence intervals, or plots. The extractor
never starts Pilot, Formal, Negative, or smoke execution.

```text
python3 experiments/b4_priority_energy/extract_analysis.py \
  --output-root /absolute/executed-output-root \
  --expected-records /absolute/manifest-or-records.jsonl \
  --audit-report /absolute/strict-audit-report.json \
  --analysis-root /absolute/outside-repository-analysis-root \
  --strict
```

The analysis root must be absent or empty and must be outside this repository.
Successful extraction publishes `cases.jsonl`, `tasks.jsonl`, their equivalent
CSV views, `analysis_audit.json`, and finally `analysis_manifest.json`. JSONL
is authoritative. The field order, pairing dimensions, algorithm order,
numeric representation, pass definitions, and self-audit rules are frozen in
`analysis_contract_v1.json`.

Each schema3 case produces one case row and ten task rows. Task priority is
recomputed independently from the taskset snapshot using `(period, task
number)` and must match the reported ranks 0 through 9. `task_pass` requires
no deadline miss, termination, or unfinished job and requires completed jobs
to equal released jobs. `whole_pass`, `hp_pass`, and `lp_pass` apply that rule
to all tasks, ranks 0 through 3, and ranks 4 through 9 respectively.

The pairing key is the SHA-256 of the compact, ordered
`pairing_dimensions` object. It excludes all algorithm and scheduler identity
but retains the taskset, source, utilization, energy, horizon, protocol, and
binary/generator identities needed to explain the pairing. Every schema3
pairing group must contain the frozen nine algorithms exactly once. Schema2
is admissible only as explicit `integration_smoke` compatibility evidence
with `not_for_paper=true`; it is excluded from formal pairing and cannot
produce schema3 summary rows.

The input audit must be strict, have `overall_pass=true`, and contain zero
infrastructure and audit failures. Scheduling outcomes remain valid algorithm
outcomes and are retained in the extracted rows. A failed extraction writes
no successful manifest. Successful outputs contain no timestamps, absolute
paths, random identifiers, NaN, or infinity; identical inputs and extractor
identity produce byte-identical files. All generated analysis is explicitly
marked `no_paper_data_generated=true`.

## I5D deterministic statistics, tables, and figures

`run_statistics.py` consumes only the six I5C v2 analysis products. The cases
and tasks JSONL files are authoritative; their CSV views are read solely to
verify parity. It never reads event arrays and never starts a simulator.
Statistics are published to an absent or empty directory outside this
repository through a staging directory and an atomic directory replacement.

```text
python3 experiments/b4_priority_energy/run_statistics.py \
  --analysis-root /absolute/I5C-v2-analysis-root \
  --statistics-root /absolute/outside-repository-statistics-root \
  --mode validation \
  --strict
```

The modes are `validation`, `pilot`, `formal-main`, and `negative-control`.
Validation accepts incomplete, `not_for_paper` inputs and watermarks every
figure `VALIDATION ONLY — NOT FOR PAPER`; its manifest always records
`paper_results_authorized=false`. Pilot emits only neutral gate, cell, and
mechanism outputs and performs no confirmatory testing. Negative Control is
descriptive appendix output and is excluded from the four-comparison HPPass
Holm family. Formal Main fails closed unless the exact grid and a final,
explicitly authorized candidate identity are present and the statistics
worktree is clean.

The statistical unit is the base-taskset cluster. Its compact ordered identity
contains phase, rho, utilization, and taskset identities but excludes lambda,
source, algorithm, scheduler, case, and pairing identities. Pilot canonically
represents its frozen two-rho dimension as `["1","2"]`, keeping the complete
`4 lambda x 2 rho x 5 algorithm` base-taskset cluster together. Ratios are
computed at case/task level first; a zero denominator is `null` in JSON, empty
in CSV, and omitted from plots. Formal estimands average lambda within cluster,
clusters within utilization, and then utilizations equally.

The frozen inference uses 10,000 percentile bootstrap replicates, stratified
cluster resampling, root seed `20260728`, and per-estimand SHA-256-derived RNG
identities. The four direct HPPass comparisons use paired, two-sided sign
flips (exact through 20 nonzero clusters, otherwise 100,000 deterministic
random draws). The Monte Carlo p-value is
`(random_extreme_count + 1) / (random_draws + 1)`; the observed permutation is
not forced into those draws, and the plus-one is its only explicit accounting.
The four raw p-values receive only stable Holm step-down correction. WholePass
is a separate secondary effect without membership in that family.

Successful Formal Main output contains five deterministic PDF/PNG figure
pairs and two CSV/TeX paper tables. PDF date metadata is disabled, PNG metadata
is fixed, Matplotlib uses Agg and DejaVu Sans, and every figure embeds the SHA
of its authoritative source statistics. The output hash DAG is one-way:
numeric/table/figure outputs, then `statistics_audit.json`, then
`statistics_manifest.json`. Generated statistics, figures, tables, logs, and
caches must remain outside the repository.

## I5B observability completion

Protocol v3 explicitly selects trace schema 3, observability summary contract
v2, and analysis contract v2. The runtime activation adds
`--b4-observability-contract-version 2` to the existing summary flag and
30,000 ms horizon. Omitting the version option retains historical summary
contract v1; omitting the summary flag retains default schema2.

Summary contract v2 adds six scheduler-reported opportunity/actual mechanism
counters and per-task `adjudicable_jobs`. A job is adjudicable exactly when
its release offset plus its relative deadline is at or before `H_B4`;
equality is included. The strict audit independently recomputes this count
from the taskset and requires at least 100 adjudicable jobs per task.

Analysis contract v2 emits all 13 mechanism counts and task/All/Top4/Bottom6
adjudicable counts. Its pass rule is `adjudicable_jobs >= 100` with zero
deadline misses. Lifecycle anomalies remain scheduling outcomes. Ratios and
cross-case statistics remain outside I5C, and every zero denominator is
contractually `NA`.
