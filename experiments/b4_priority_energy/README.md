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
