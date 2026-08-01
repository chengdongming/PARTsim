# RTA4 V3 parameterized campaigns

V3 adds finite, externally configured campaigns without changing the frozen
V1/V2 contracts or any RTA/scheduler implementation. Scientific parameters
are read only from YAML, normalized to exact rational strings, and bound by a
scientific-config SHA-256 and a dynamic plan SHA-256.

## Physical-core slot production revision

The production backend is `PHYSICAL_CORE_PROCESS_SLOTS`.  `--worker-count N`
means exactly N long-lived processes on N different physical cores; it is not a
thread count, logical-CPU count, or advisory pool size.  The runner never
clamps it.  A request larger than the allowed physical-core inventory is an
error.

Process count alone is not evidence of multicore execution.  Linux can move
unbound processes among logical CPUs, and two SMT siblings are still one
physical core.  The runner first reads the container's allowed logical CPUs
from `os.sched_getaffinity(0)`, then reads `physical_package_id` and `core_id`
from CPU sysfs.  It groups by `(package, core)`, chooses the smallest allowed
logical CPU in each group, and sorts deterministically.  Every worker executes
`sched_setaffinity(0, {logical_cpu_id})` and requires an exact singleton
readback before work and around every attempt.  Missing topology, binding
failure, affinity drift, duplicate physical identities, and topology drift all
fail closed.

The prepared config binds the selection policy, allowed-topology fingerprint,
available physical-core count, selected physical identities, selected logical
CPU IDs, worker count, checkpoint policy, and backend.  Preparation and
execution must therefore happen in the same controlled AutoDL cpuset.  The
execution preflight rediscovers the topology and rejects a changed fingerprint;
it never silently chooses replacement CPUs.

The old process-pool runner used fixed `max_in_flight` batches.  Once a fast
future completed, its process could remain idle until the slowest future in the
same batch finished, because the next batch was not submitted.  The new parent
maintains pending, active, retry, and idle-slot state continuously.  Whenever
any slot completes, that same slot immediately receives the next pending
attempt.  Effective concurrency may fall only in the final tail.  A straggler
on one core cannot prevent other cores from consuming later records.

The process-pool runner also had two process layers: a persistent pool worker
spawned another interpreter for every attempt.  For short RTA requests, Python
startup, imports, serialization, and reap cost could dominate computation.
The nested per-attempt spawn implementation has been removed. Each pinned slot
loads the immutable base configuration once. Each command carries only its
record, certificate, minimal one-record energy/service context, attempt index,
and timeout budget; the production manifest and global record registry are not
resent per attempt.

Hard timeout ownership moved to the parent.  Each slot runs at most one attempt
at a time, and the parent records its start and deadline.  At expiry the parent
terminates only that slot, confirms exit, kills if necessary, reaps it, records
`UNIFIED_RTA_ADAPTER_TIMEOUT`, and starts a new generation on the same physical
core.  Other slots continue.  The unchanged timeout contract is one 120-second
attempt followed, only after timeout, by one 240-second attempt.  A second
timeout is terminal `TIMEOUT`; crashes and protocol failures remain
`INTERNAL_ERROR` and never masquerade as mathematical timeouts.

The parent is still the sole formal writer.  Every terminal is atomically
committed immediately and is authoritative recovery evidence.  A full
checkpoint is written after either `max(32, worker_count)` new terminals or
five seconds, whichever comes first, and is forced on normal completion or
interruption.  During formal execution the parent converts `SIGTERM` into a
graceful stop: it forces the checkpoint, terminates/reaps active slots, and
restores the caller's signal handler before exiting.  This removes the prior
quadratic pattern of serializing the
ever-growing completed-ID list after every record.  On resume, valid terminals
rebuild the completed inventory, a lagging checkpoint is repaired, existing
terminals are neither overwritten nor re-executed, and malformed/foreign
evidence fails closed.

Worker PID, logical CPU, physical package/core, singleton affinity, slot,
generation, and monotonic intervals are execution diagnostics only.  They are
reported with active-slot concurrency, replacements, timeout kills, checkpoint
writes, and terminal writes.  None enters task-set, mathematical-request,
mathematical-result, or ordered-stream identity.

This revision uses production-build profile
`ASAP_BLOCK_V9_3_RTA4_FORMAL_V3_PARAMETERIZED_SHARED_ENERGY_PHYSICAL_CORE_SLOTS_R1`
and a structurally new production manifest, prepared config, authorization,
namespace, and checkpoint schema.  The linear lineage anchor remains
`5acde530eb6b68f6e3a5bc2e6c496307690a054d`.  All artifacts must be regenerated
from the clean physical-slot commit into empty output and task-set-store
namespaces.  Results made by commit `0178481095b649be5365509f1c96743e80765ed7`
or any earlier thread/process-pool runner are diagnostic only: do not copy,
resume, aggregate, or mix them with physical-slot results.

## Commands

Inspect the current container topology without reading or writing formal
artifacts:

```bash
python3 scripts/run_v9_3_rta4_formal.py --describe-cpu-topology
```

The output lists allowed logical CPUs, package/core sibling groups, selectable
physical worker count, deterministic selection order, and the topology
fingerprint. Choose 8, 16, or 32 only when at least that many physical groups
are listed. Start at 16 on an otherwise idle AutoDL host with at least 16
allowed physical cores; move to 32 only after the long diagnostic shows higher
throughput and no CPU/cgroup quota saturation. A machine exposing 16 logical
CPUs as 8 SMT pairs can run at most `--worker-count 8`.

Create a campaign template (the command refuses to overwrite an existing
file):

```bash
python3 scripts/create_v9_3_rta4_campaign.py \
  --core CORE-1 --output /tmp/core1-campaign.yaml
```

Validate and describe a campaign without executing it:

```bash
python3 scripts/run_v9_3_rta4_formal.py \
  --campaign-config configs/v9_3_rta4_e1_critical_e0_v1.yaml \
  --dry-run
```

The checked-in E1 example produces 800 task-set skeletons and 9,600 ordered
mathematical requests. This command never starts a formal experiment.

Prepared and authorization artifacts can be generated only while binding an
existing V3 production manifest and explicit operational paths. Build that
manifest from a clean linear V3 execution commit with the explicit profile:

```bash
python3 scripts/build_v9_3_rta4_production_manifest.py \
  --profile v3-parameterized \
  --output /absolute/path/v3-production-manifest.json \
  --simulator-binary /absolute/path/rtsim \
  --verifier-binary /absolute/path/solar-verifier \
  --simulator-build-arg bounded-build-command \
  --verifier-build-arg bounded-build-command
```

Then create the hash-bound execution artifacts:

```bash
python3 scripts/run_v9_3_rta4_formal.py \
  --campaign-config /absolute/path/campaign.yaml \
  --production-manifest /absolute/path/production-manifest.json \
  --output-root /absolute/path/results \
  --taskset-store /absolute/path/tasksets \
  --worker-count 12 --max-in-flight 24 --timeout 120 \
  --write-prepared-config /absolute/path/prepared.json \
  --write-authorization /absolute/path/authorization.json
```

`--max-in-flight` remains an operational authorization setting and must cover
the worker count. The physical-slot parent does not create extra compute
workers when it is larger. If omitted, its default is twice the worker count.

Run non-formal synthetic and real unified-RTA diagnostics before preparation:

```bash
python3 scripts/benchmark_v9_3_rta4_physical_workers.py \
  --worker-count 1 --duration 120 --records 512 --mode real-rta
python3 scripts/benchmark_v9_3_rta4_physical_workers.py \
  --worker-count 16 --duration 120 --records 512 --mode real-rta
```

The script writes no formal result. It reports affinity evidence, wall and CPU
seconds, throughput, effective cores, active-slot mean/max, utilization,
one-worker speedup, and leaked-child count. A formal long canary should then
confirm singleton affinity on every requested core, mostly full active slots,
continuous terminal growth, far fewer checkpoints than terminals, no
`INTERNAL_ERROR`, and no leftover workers. If effective cores or throughput
remain near one, inspect the container CPU quota and workload duration rather
than increasing process count blindly.

Validation performs all live campaign, plan, manifest, authorization, source,
store, and output ownership checks without constructing a solver:

```bash
python3 scripts/run_v9_3_rta4_formal.py \
  --campaign-config /absolute/path/campaign.yaml \
  --prepared-config /absolute/path/prepared.json \
  --authorization /absolute/path/authorization.json \
  --validate-only
```

Use `--execute` for a new output namespace and `--resume` for an existing V3
checkpoint. `--max-records N` bounds either command without changing the
scientific config or plan identity. Terminal JSON and checkpoints are written
atomically; resume validates every completed result against the frozen
task-set store and never rewrites it.

Downstream CORE-2/3/5B preparation additionally requires the observed source
binding and `--source-taskset-store`; the store marker must match the bound
source campaign, plan, core, and store identity.

The prepared identity contains the campaign's absolute path, raw file hash,
normalized scientific material/hash, dynamic counts, plan/digest, production
manifest identity, source binding, and operational settings. Changing only an
output/log/timeout/physical-worker setting preserves scientific and plan
identities but changes the prepared identity.

## Configurable scientific fields

- CORE-1: processors, task count, utilization strata, tasksets per stratum,
  E0, and recursive methods.
- CORE-2: the exact CORE-1 source binding, E0, CORE-2 methods, and referenced
  recursive methods. Independent generation is forbidden.
- CORE-3: the exact CORE-1 source binding, release modes, finite capacities,
  projection methods/E0, and finite horizon values. The scheduler remains
  `gpfp_asap_block`.
- CORE-4: processors, task count, utilization strata, skeleton count,
  baseline, four OFAT axes, and recursive methods. Baseline duplicates are
  removed automatically and every non-baseline condition changes one field.
- CORE-5A: baseline, task-count/processor/integer-time-scale axes and their
  paired sample counts, and recursive methods.
- CORE-5B: the exact CORE-4 baseline source binding, utilization strata,
  candidate/selected counts, recursive methods, and worker-consistency axis.
  Selection is result-independent; workers change execution identity but not
  mathematical-request identity.

All rational scientific values must be YAML strings such as `"21/40"`.
Binary floats, NaN/infinity, duplicate/empty axes, unknown fields/methods,
invalid counts, unsupported semantics, and mismatched source identities fail
closed. CLI options intentionally provide no scientific-parameter overrides.

Campaign YAML is external scientific input and is not added to either code
source closure. The V2 closure and profile remain frozen. The independent V3
closure adds the V3 config, plan, lifecycle, schema, runner, CLI, campaign
template generator, and manifest implementation; code, binaries, compiler,
environment, and fixed model inputs remain independently bound by the V3
production manifest.
