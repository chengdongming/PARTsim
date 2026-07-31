# RTA4 V3 parameterized campaigns

V3 adds finite, externally configured campaigns without changing the frozen
V1/V2 contracts or any RTA/scheduler implementation. Scientific parameters
are read only from YAML, normalized to exact rational strings, and bound by a
scientific-config SHA-256 and a dynamic plan SHA-256.

## Commands

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
output/log/timeout/ordinary-worker setting preserves scientific and plan
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
