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
existing production manifest and explicit operational paths:

```bash
python3 scripts/run_v9_3_rta4_formal.py \
  --campaign-config /absolute/path/campaign.yaml \
  --production-manifest /absolute/path/production-manifest.json \
  --output-root /absolute/path/results \
  --taskset-store /absolute/path/tasksets \
  --write-prepared-config /absolute/path/prepared.json \
  --write-authorization /absolute/path/authorization.json
```

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

Campaign YAML is external scientific input and is not added to the V2
production source closure. Code, binaries, compiler, environment, and fixed
model inputs remain independently bound by the production manifest.
