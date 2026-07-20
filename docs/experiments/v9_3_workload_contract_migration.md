# v9.3 real-time task workload contract migration

## Root cause and scope

`idle` is a valid system power state but is not a real-time task workload. The
shared generator's implicit pool nevertheless contained `idle`. EXT-1B3 first
made the defect deterministic because `ACTUAL_GENERATOR_ORDER` preserves the
generated workload and its fail-closed actual-power check rejected every idle
task. With ten tasks per source taskset, the observed candidate rejection rate
was high enough to exhaust the structural retry budget.

The first PR #34 commit (`db72f9d2`) fixed the B3 opt-in path, but callers that
omitted `generation.workload_candidates` still used the legacy generator pool,
and TasksetStore still accepted a V1 optional-contract path. This follow-up
makes the rule repository-wide for v9.3 task generation.

The invariant is now:

- `idle` remains in the system energy model as the system idle-power state;
- the task pool is the stable lexical ordering of
  `system.workload_coefficients - {"idle"}`;
- every common v9.3 config explicitly declares that exact pool;
- every generated or loaded task must name a pool member and have the exact
  model-derived `P`;
- no loader, generator, scenario transform, or retry silently substitutes a
  workload;
- contract membership and exact power-model values participate in config,
  generation-cell, frozen-taskset, pairing, and run provenance identities.

For the repository system template the pool is `[bzip2, control, decrypt,
encrypt, hash]`. Code derives and verifies this list from the configured system
model; it is not used as an unverified runtime constant.

## Identity and schema migration

| Material | Legacy | New executable identity |
|---|---|---|
| workload contract | absent or `NON_IDLE_V1` | `REAL_TIME_TASK_WORKLOAD_CONTRACT_V2` |
| candidate hash domain | `TASK_WORKLOAD_CANDIDATES:v1` | `REAL_TIME_TASK_WORKLOAD_CANDIDATES:v2` |
| power-model hash domain | `TASK_WORKLOAD_POWER_MODEL:v1` | `REAL_TIME_TASK_WORKLOAD_POWER_MODEL:v2` |
| generation cell | `TASKSET_GENERATION_CELL:v1` | `TASKSET_GENERATION_CELL:v2` |
| deterministic seed domain | `TASKSET_SEED:v1` | `TASKSET_SEED:v2` |
| frozen taskset schema | V1 or opt-in V2 | `ASAP_BLOCK_V9_3_FROZEN_TASKSET_V3` |
| taskset semantic domain | v1 or opt-in v2 | `TASKSET_SEMANTIC:v3` |
| pairing manifest | `CORE12_PAIRING_MANIFEST_V1` | `CORE12_PAIRING_MANIFEST_V2` |
| pairing identity | `CORE12_PAIRING_CONTRACT:v1` | `CORE12_PAIRING_CONTRACT:v2` |
| EXT-1B transformed taskset | `EXT1B_TASKSET_V1` | `EXT1B_TASKSET_V2` |
| common/EXT-1B config hash | v1 | v2 |

The new frozen contract contains the ordered candidates, exact
`energy_per_tick` for each workload, candidate identity, power-model identity,
and combined contract identity. A candidate or power-value change therefore
changes generation, seed, taskset, pairing, and request identities. CORE-1 and
CORE-2 still exclude analysis method and experiment label from their shared
generation material, so their intended pairing remains exact.

EXT-1B's `source_taskset_index = logical_taskset_index * structural_retry_limit
+ attempt_index` mapping is unchanged. The seed hash domain is deliberately
versioned to v2 because the generation contract changed; old and new seed
spaces therefore cannot be confused even though logical/source indexing stays
deterministic.

## Configuration inventory

Every config below now declares the exact pool. “New generated identity” means
the config/cell/seed/taskset identities migrate even where the human-readable
`experiment_id` remains unchanged.

### FORMAL and production templates

| Config | Old experiment identity | New experiment identity | Existing/future action | Reason |
|---|---|---|---|---|
| `v9_3_core1_formal_candidate.yaml` | `asap-block-v9.3-core-1-formal-candidate` | suffix `-workload-contract-v2` | old artifacts retained; future run uses new root | shared CORE store contract |
| `v9_3_core2_formal_candidate.yaml` | `asap-block-v9.3-core-2-formal-candidate` | suffix `-workload-contract-v2` | same | paired with CORE-1 under new manifest |
| `v9_3_core1_template.yaml` | `asap-block-v9.3-core1-formal-template` | suffix `-workload-contract-v2` | template was not a result; materialize fresh | future formal contract |
| `v9_3_core2_template.yaml` | `asap-block-v9.3-core2-formal-template` | suffix `-workload-contract-v2` | same | future formal contract |
| `v9_3_core3_template.yaml` | `asap-block-v9.3-core3-template` | suffix `-workload-contract-v2` | materialize fresh if authorized | direct generated workload/P |
| `v9_3_core3_formal_b20.yaml`, `v9_3_core3_formal_b100.yaml` | r1 labels retained | labels and legacy paths retained as audit-only | mandatory workload field permits common-schema inspection, but the energy preflight still rejects both and formal execution remains prohibited | superseded CORE-3 energy tracks |
| `v9_3_core3_formal_b20_r2.yaml`, `v9_3_core3_formal_b100_r2.yaml` | r2 labels and paths from PR #35 | suffix `-workload-contract-v2`; fresh suffixed roots/stores | only authorized CORE-3 formal entry configs; energy parameters unchanged | workload and energy preflights are independently mandatory |
| `v9_3_core4_formal.yaml` | `asap-block-v9.3-core4-formal-sustainability-r1` | suffix `-workload-contract-v2` | future run uses a fresh suffixed root/store | post-fork formal sustainability config |
| `v9_3_core5a_formal_algorithmic.yaml`, `v9_3_core5b_formal_workers.yaml` | r1 formal labels | suffix `-workload-contract-v2` | future runs use distinct fresh suffixed roots/stores | post-fork formal scalability configs |
| `v9_3_core4_template.yaml` | `asap-block-v9.3-core4-template-not-frozen` | suffix `-workload-contract-v2` | no frozen formal result to rerun | base store for sensitivity children |
| `v9_3_core5_template.yaml` | `asap-block-v9.3-core5-template-not-frozen` | suffix `-workload-contract-v2` | no frozen formal result to rerun | base store for scalability children |
| `v9_3_ext1_template.yaml` | `asap-block-v9.3-ext1-template` | suffix `-workload-contract-v2` | materialize fresh if enabled | generated simulation workload |
| `v9_3_ext2_template.yaml` | `asap-block-v9.3-ext2-template` | suffix `-workload-contract-v2` | real-trace formal remains disabled | generated task input |
| `v9_3_ext4_template.yaml` | `asap-block-v9.3-ext4-template` | suffix `-workload-contract-v2` | materialize fresh if enabled | robustness base taskset |
| `v9_3_ext1b1_formal_r1.yaml` | `asap-block-v9.3-ext1b1-formal-r1`; seed/freeze `EXT1B1_FORMAL_R1` | experiment suffix `-workload-contract-v2`; seed/freeze `EXT1B1_FORMAL_R1_WORKLOAD_CONTRACT_V2` | existing B1 result is not modified; future regeneration requires a new formal directory | explicit formal freeze migration |

The CORE-1/2 formal output and shared-store defaults now end in
`workload_contract_v2`. Other production-template output/store defaults were
similarly moved to fresh suffixed paths. No old directory is renamed or
rewritten.

### PILOT

| Config | Old identity | New identity | Rerun policy | Reason |
|---|---|---|---|---|
| `v9_3_ext1b1_pilot.yaml` | experiment unchanged base name; `EXT1B_PILOT` | experiment suffix and `EXT1B_PILOT_WORKLOAD_CONTRACT_V2` | only rerun into the new configured roots | shared source generation |
| `v9_3_ext1b2_pilot.yaml` | same legacy seed space | suffix and V2 seed space | same | shared source generation |
| `v9_3_ext1b3_pilot.yaml` | same legacy seed space | suffix and V2 seed space | same | shared source generation |

### CALIBRATION

| Config | Old identity | New identity | Rerun policy | Reason |
|---|---|---|---|---|
| `v9_3_final_calibration.yaml` | `asap-block-v9.3-final-constrained-calibration` | suffix `-workload-contract-v2` | bounded calibration is not rerun by this PR | candidate CORE identities |
| `v9_3_ext1b1_energy_calibration.yaml` | legacy experiment/seed | experiment suffix; `EXT1B1_ENERGY_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2` | no full calibration here | shared source generation |
| `v9_3_ext1b2_sync_calibration.yaml` | legacy experiment/seed | experiment suffix; `EXT1B2_SYNC_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2` | no full calibration here | shared source generation |
| `v9_3_ext1b3_timing_calibration.yaml` | B3 phase-one V2 identity | experiment suffix; `EXT1B3_TIMING_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2` | plan-only validation only | global contract supersedes opt-in identity |

All calibration output/store defaults were moved to paths containing
`workload_contract_v2`.

### SMOKE and test-only materialization

The following keep their human-readable experiment labels but receive new
config, generation-cell, seed, taskset, and manifest identities:

- `v9_3_constrained_deadline_smoke.yaml`;
- `v9_3_core1_smoke.yaml`, `v9_3_core2_smoke.yaml`,
  `v9_3_core3_smoke.yaml`, `v9_3_core3_pass_micro.yaml`,
  `v9_3_core4_smoke.yaml`, and `v9_3_core5_smoke.yaml`;
- `v9_3_ext1_smoke.yaml`, `v9_3_ext2_smoke.yaml`, and
  `v9_3_ext4_smoke.yaml`;
- `v9_3_ext1b1_smoke.yaml`, `v9_3_ext1b2_smoke.yaml`, and
  `v9_3_ext1b3_smoke.yaml`.

These are test configurations. Existing smoke output is disposable evidence,
but the implementation still fails closed rather than overwriting a legacy
store. A fresh temporary/output root is used for validation.

### REHEARSAL

There is no standalone v9.3 generation rehearsal YAML. The rehearsal entry in
`v9_3_experiment_freeze_manifest.yaml` is historical inventory, does not invoke
TasksetStore, and was not mechanically changed.

### Does not use TasksetStore

`v9_3_pilot.yaml`, `v9_3_pilot2.yaml`, and `v9_3_pilot3.yaml` use older custom
materializers. They now explicitly declare the same pool and contract version;
their runners pass the pool to `global_task_generator.py`, validate workload/P
after generation, and include the full contract in new taskset semantic hashes.
They do not gain TasksetStore resume semantics. Existing pilot artifacts remain
read-only historical inputs and are not rewritten.

`v9_3_experiment_freeze_manifest.yaml` also does not generate tasksets and is
not a generation config.

## Artifact compatibility

- Existing JSON, CSV, retained traces, and sealed archives are not modified.
- A V1 frozen taskset or V1 manifest without the mandatory contract is
  `LEGACY_NON_EXECUTABLE`; execution raises
  `legacy taskset store lacks mandatory non-idle workload contract`.
- The phase-one opt-in V2 frozen schema is also not accepted by the new V3
  execution path, because its generation/seed/pairing domains differ.
- A current-schema task containing `idle` raises
  `stored real-time task uses reserved idle workload`.
- Unknown workloads, exact-P mismatches, contract identity mismatches,
  semantic-hash mismatches, and pairing-manifest mismatches all fail closed.
- There is no auto-upgrade, in-place repair, fallback pool, or workload reroll.
- Legacy artifacts may be examined by the read-only auditor. They may not be
  resumed or copied into a V2/V3 execution store.

“Existing artifact retained” does not mean “future regeneration has the same
semantic hash.” All future regenerated tasksets use new identities. Physical
copies in clones/worktrees are not independent experimental samples.

A bounded read-only audit of the repository-tracked
`artifacts/v9_3_final_calibration/taskset_store` found 6 physical files and 6
unique semantic tasksets, including 8 task records with `workload=idle`; all 6
tasksets lack the mandatory contract and the store has no executable pairing
manifest. These counts describe only that explicit local path, not AutoDL or
any estimate of formal sample cardinality. The directory remains unchanged and
is classified `LEGACY_NON_EXECUTABLE`.

## B1, B2, CORE, and B3 boundary

B1 and B2 apply the existing `HIGH_PRIORITY_HIGH_POWER` mapping after source
generation. Tests show the final tasks remain non-idle, every final `P` comes
from the actual model, and the high/middle/low rank mapping is unchanged. Thus
existing retained B1/B2 simulation evidence is not rewritten, B2 auditor logic
and retained traces are unchanged, and its retained-trace status can remain
`B2_RETAINED_TRACE_REAUDIT_PASSED`. Future regeneration nevertheless receives
new taskset identities and must use a fresh store.

CORE-1 and CORE-2 continue to pair on generation cell/index, complete payload,
priority hash, power hash, and workload contract identity; only the analysis
method differs. CORE-3/4/5 and EXT-1/2/4 directly consume generated workload/P
and therefore require the global contract. Old CORE stores cannot resume. A
formal CORE result needs rerun only when its tasksets must be regenerated or
execution resumed from the legacy store; this code PR does not run those formal
experiments.

B3 continues to preserve `WORKLOAD_NOT_IN_ACTUAL_POWER_MODEL` as a defensive
scenario rejection. The source generator now prevents idle at origin, so the
formal timing-calibration plan can materialize 4 cells, 80 paired instances,
and 240 requests without simulator execution.

## Synchronization with CORE-3 energy preflight

PR #34 was synchronized by ordinary merge with `origin/master` at
`8b4f9762014fc17b24d6424b3e32ef1ab0accdc1`, including PR #35's
`ddaa08ad` CORE-3 energy-headroom repair. The only textual conflicts were in
`experiments/v9_3/config.py` and `experiments/v9_3/taskset_store.py`. The
resolution composes the mandatory workload/P contract with the real-solar,
dyadic-scale, service-projection, and runtime no-overflow checks; neither gate
substitutes for or disables the other.

The merge does not introduce another workload schema or identity version.
`REAL_TIME_TASK_WORKLOAD_CONTRACT_V2`, frozen taskset V3, semantic domain v3,
CORE-1/2 pairing V2, and generation/cell/seed/config identity v2 remain the
current domains. CORE-3 energy preflight retains its independent V1 report
schema and its PR #35 service identity. Migrating the new formal configs does
change their normalized config hashes, generation identities, seeds, taskset
IDs, and artifact paths as required; it does not change the frozen energy
parameters, real-solar data, horizon, or dyadic rule.

## Read-only audit

Audit only explicit paths; the tool never defaults to scanning a home or root
directory:

```bash
python3 scripts/audit_v9_3_taskset_workload_contract.py \
  /path/to/taskset_store /path/to/result_root \
  --verify-hashes --fail-on-idle --fail-on-missing-contract \
  --fail-on-power-mismatch \
  --json-output /path/to/workload_contract_audit.json
```

The report separates physical files, unique semantic tasksets, and duplicate
content, and reports schema/contract counts, idle records, unknown workloads,
power mismatches, missing contracts, semantic failures, pairing failures, and
affected config identity. It does not change the audited paths.

## Full structural preflight

`--dry-run` remains a cardinality-only preview. `--plan-only` performs config
and workload-contract validation, generation, structural retries, deadline
transforms, scenario construction, request planning, and artifact hashing, but
does not execute the simulator:

```bash
python3 scripts/run_v9_3_ext1b.py \
  --config configs/v9_3_ext1b3_timing_calibration.yaml \
  --output-root /new/path/b3_workload_contract_v2_plan \
  --taskset-store /new/path/b3_workload_contract_v2_store \
  --simulator-bin ./build/rtsim/rtsim \
  --plan-only
```

In addition to the four planning CSVs, it writes `plan_summary.json`,
`workload_contract_summary.json`, and `file_hashes.sha256`. The summary records
the contract/candidate/model identities, illegal-task counters,
`legacy_taskset_count`, and `simulator_invoked=false`.

## Formal-experiment handoff

1. Preserve every prior result/store byte and retained trace.
2. Audit old stores read-only and distinguish physical duplicates from unique
   tasksets.
3. Never edit legacy JSON/CSV to make it executable.
4. Use a new `workload_contract_v2` output root and store for any regeneration.
5. Re-run only the formally authorized experiment whose tasksets require new
   generation; do not mix legacy and new identities.
6. Record the new commit, normalized config, workload identities, plan hashes,
   and audit report before starting a formal batch.

This generation defect does not by itself invalidate the theoretical proof,
RTA mathematics, or C++ scheduler semantics. This migration changes Python
input generation and artifact identity only; it makes no C++ scheduler change.
