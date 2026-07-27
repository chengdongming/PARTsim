# B4-PE freeze candidate v1

This is a candidate freeze. The final commit, tag, formal runtime binary path, and
binary SHA are filled only after I6 RTA integration. The current candidate code
commit is `d0339f40d4dac9277d69878c5d4f57003cbd48c4`; it is not the final paper-code
freeze point.

The machine-readable authority for this candidate is
`b4_pe_freeze_candidate_v1.json`. This document explains that JSON and does not
define a second configuration.

## Frozen identities

| Input | Repository path or role | SHA-256 |
| --- | --- | --- |
| Experiment document | `docs/experiments/ASAP_BLOCK_B4_priority_energy_v5_2_frozen.md` | `0fee308839f2097664a63a21f8806128c868b1016fab2712e67892356961be52` |
| System template | `v9_3_b4_priority_energy_system_template.yml` | `a64181bf9fda8155c5b0b8b0451a160d6c44c2c8fae188a974640a4d2b243510` |
| Identity protocol | `experiments/b4_priority_energy/protocol_resolution_v1.json` | `a201624f98f8bc99572dd7696684cf3e4e757b84bffb616d3483e1cff4911057` |
| Execution protocol | `experiments/b4_priority_energy/execution_protocol_v1.json` | `0ef894c6a00812cae787cc38f6f9c92e2014b47fa7f6742adddf80fe47c335f9` |
| Task generator | `global_task_generator.py` | `25147e8073e55885a035160cbec4fe1d094dfd423a476731790e4cb8bb53bb8e` |
| Verified local binary | local verification only | `735a810188c92f9b60560be42224b47c2564672b539a3567682aef0d24a92243` |

The locally verified binary is
`/home/devcontainers/builds/partsim-b4-release/rtsim/rtsim`. The formal runtime
binary path and SHA are null in the candidate and must be recorded after the
formal environment rebuild.

## Frozen Manifests and algorithm order

| Phase | Cases | SHA-256 |
| --- | ---: | --- |
| Pilot | 2400 | `783c1b7e04ff3ee9634093deb478e5d9e436236f2f90e1f5f00b502c4fadca9c` |
| Formal | 18000 | `cc7b85dfe15c5eca3483194cfc03a0219ffda65ac6bc11bbe16fbf444a02fd3d` |
| Negative | 5400 | `ad1a4d0e3f2f2600ad6d7728b2bf3749bb6a9564c632056bcc42e184abcc47dc` |
| All | 25800 | `ec3d77549ca5c5bdac4fbaf473480b777da7d9051f9d5406e8938fe87bb62255` |

The combined identity set contains 560 unique tasksets and 2240 unique sources.
Formal and Negative share 300 tasksets and 600 sources.

Pilot order is `gpfp_asap_block`, `gpfp_asap_nonblock`,
`gpfp_asap_sync`, `gpfp_alap_block`, `gpfp_st_block`.

Formal and Negative order is `gpfp_asap_block`, `gpfp_asap_nonblock`,
`gpfp_asap_sync`, `gpfp_alap_block`, `gpfp_alap_nonblock`,
`gpfp_alap_sync`, `gpfp_st_block`, `gpfp_st_nonblock`, `gpfp_st_sync`.

## Platform, tasks, and source

The platform has four processors and shared energy storage. Each taskset has ten
constrained-deadline tasks scheduled by migratory, preemptive global
fixed-priority scheduling with RM priorities. Periods are in [40, 200] ms, the
tick is 1 ms, the formal horizon is 30000 ms, and the smoke horizon is 1000 ms.
Formal normalized utilization levels are 0.2, 0.3, 0.4, 0.5, and 0.6.

The top four RM-priority tasks and bottom six tasks use rho_E=2 in the main
experiment and rho_E=1 in the negative control. The runtime source is
`scaled_piecewise` with profile `b4_pe_three_stage_v1`; the Manifest identity
profile is `three-stage-offered-harvest-v1`. Its multipliers are 1.0 on
[0,5000) ms, 0.2 on [5000,15000) ms, and 1.0 on [15000,30000) ms. The formal
trace has 30000 increments and an integral equivalent of 22000 ms. Time zero
uses E0 only; harvest produced during [k,k+1) arrives before the decision at
k+1.

E0, Emax, and alpha are rules, not values copied from a smoke case. Sections
5-8 of the frozen experiment document define p0, q0, the released-job W_H/W_L
demand, rho normalization, reference burst, battery, and harvest equations.
`protocol_resolution_v1.json` freezes those identities, and the named helpers
in `integration_smoke_case.py` are the current executable reference for the
synchronous smoke bridge. The actual task YAML mapping is C_i=`task.runtime`,
T_i=`task.iat`, and O_i=the integer parsed from
`task.params.arrival_offset`. The task_id is the integer suffix in the frozen
`task.name` form `task_<task_id>`. RM priority sorts ascending by the exact key
`(T_i, task_id)`: T_i ascending first, then task_id ascending when periods are
equal. No other tie-break key is used. The JSON records the same actual fields
and complete formula chain: release count N_i(H), W_H/W_L, rho-reference
factors, E_burst_ref, E0, Emax, nominal demand, and alpha. A negative alpha
rejects the taskset instead of being clamped. These rules are therefore
uniquely recoverable from the frozen inputs.

## Portable directory contract

```text
<experiment-root>/
  manifests/
  inputs/
    tasksets/
    systems/
    sources/
  outputs/
    pilot/
    formal/
    negative/
  reports/
  logs/
  environment/
  checksums/
```

Formal output must be outside the repository source tree. Pilot, Formal, and
Negative outputs remain separate. An audit report is written outside the output
directory it audits.

## Supported commands

These commands are the argv sequences frozen in the JSON and use only current
CLI options.

```text
python3 <repo-root>/experiments/b4_priority_energy/generate_manifest.py --phase all --output <manifest-path>
python3 <repo-root>/experiments/b4_priority_energy/validate_manifest.py --manifest <manifest-path>
python3 <repo-root>/experiments/b4_priority_energy/audit_manifest.py --manifest <manifest-path> --json
python3 <repo-root>/experiments/b4_priority_energy/run_manifest.py --manifest <manifest-path> --dry-run --output-root <output-root>
python3 <repo-root>/experiments/b4_priority_energy/inspect_execution.py --output-root <output-root> --manifest <manifest-path> --simulator-binary <rtsim-binary> --json
python3 <repo-root>/experiments/b4_priority_energy/execute_manifest.py --manifest <manifest-path> --output-root <output-root> --simulator-binary <rtsim-binary> --execute
python3 <repo-root>/experiments/b4_priority_energy/execute_manifest.py --manifest <manifest-path> --output-root <output-root> --simulator-binary <rtsim-binary> --execute --resume
python3 <repo-root>/experiments/b4_priority_energy/audit_results.py --output-root <output-root> --report <report-path> --expected-records <manifest-path> --strict
sha256sum <repo-root>/docs/experiments/ASAP_BLOCK_B4_priority_energy_v5_2_frozen.md <repo-root>/v9_3_b4_priority_energy_system_template.yml <repo-root>/experiments/b4_priority_energy/protocol_resolution_v1.json <repo-root>/experiments/b4_priority_energy/execution_protocol_v1.json <repo-root>/global_task_generator.py <manifest-path> <rtsim-binary>
```

## Formal run gate and governance

The ordered gate is: after I6 RTA integration, checkout the final commit/tag;
require a clean worktree; rebuild rtsim from that checkout in the AutoDL formal
run environment; record the AutoDL compiler, dependencies, and environment;
verify document, template, protocol, and Manifest SHA values; record the formal
runtime binary path and SHA; run Python and C++ tests; and run and audit 9-18
real smoke cases. Then run the 2400-case Pilot and complete both result inspect
and result audit. The Pilot gate passes only when
`infrastructure_failure_count=0`, `audit_failure_count=0`, and
`overall_pass=true`. If the Pilot gate does not pass, stop and do not start
Formal. Only after it passes may the Pilot report be frozen and the 18000-case
Formal phase start. After Formal completes, run the 5400-case Negative phase in
the frozen Formal-then-Negative order; audit all results; then produce
statistics, figures, and paper tables.

Manifest, binary, protocol, template, or frozen-document SHA mismatch blocks a
formal run. Infrastructure and audit failures are excluded from statistics;
scheduling outcomes remain algorithm results.

Integration-smoke output is not paper data. The existing 18-case smoke proves
only interoperability and pairing fairness. Deadline misses in that smoke do
not support any performance conclusion.

After I4D, frozen parameters must not change silently. Any parameter, Manifest,
task-generation, source, or result-schema change requires a new freeze version
and rerunning every affected experiment.
