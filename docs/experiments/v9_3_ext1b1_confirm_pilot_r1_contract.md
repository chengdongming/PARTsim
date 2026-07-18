# EXT-1B B1 confirm pilot R1 contract

This document freezes the B1 confirm-pilot design before any confirm native
simulation. It binds the six R2-selected cells, independent taskset domain,
request plan, metrics, gates, current parser/native provenance, and the rules
for later execution. It does not authorize B2, B3, EXT-1A, or a formal run.

The public EXT-1B YAML schema represents one cell per shard: `interpolation_rho`
is scalar and the selected cells are not a Cartesian grid. Therefore
`configs/v9_3_ext1b1_confirm_pilot_r1.yaml` is the first-shard template. A later
outcome-blind orchestrator must expand it into the six ordered shards listed in
the machine contract, changing only the bound cell values, phase-derived seeds,
experiment identity, and per-shard output/store paths. No runner or schema
extension is part of this freeze.

## Machine-readable freeze

`contract_sha256` is SHA-256 of the repository `canonical_json(payload)` bytes.
The hash excludes the envelope field itself and all floating timestamps.

<!-- EXT1B_B1_CONFIRM_CONTRACT_JSON_BEGIN -->
```json
{
  "contract_sha256": "db172726e9ccee628ed932780e85e392ad121d9718971d88ec7092db8db1c263",
  "hash_algorithm": "SHA-256(canonical_json(payload))",
  "payload": {
    "anti_peeking": {
      "bounded_smoke_domain": "B1_CONFIRM_PILOT_R1_SMOKE",
      "bounded_smoke_index_range": [
        9000,
        9001
      ],
      "confirm_output_root_created_after_contract_hash_only": true,
      "contract_fields_frozen_before_native_simulation": true,
      "partial_confirm_trial_before_contract_edit_prohibited": true,
      "post_result_contract_edits_prohibited": true,
      "results_may_not_write_back_to_contract": true
    },
    "build": {
      "build_log_sha256": "e8679f7f0ae158c04bf1d503c5a5b9e4999d7560fd3dba0297e44064993eda15",
      "build_type": "Release",
      "cmake_version": "cmake version 3.16.3",
      "compiler": "c++ (Ubuntu 9.4.0-1ubuntu1~20.04.2) 9.4.0",
      "release_flags": "-O3 -DNDEBUG",
      "repository_clean_at_build": true,
      "simulation_result_py_sha256": "47ee50dadf6867254663d023ce93832b8abe43812e6cce39e564c07dae1c8607",
      "simulator_absolute_path": "/tmp/ext1b_b1_confirm_pilot_contract_r1/build/rtsim/rtsim",
      "simulator_sha256": "80d3da0ed4890a5cd08e786544f2a410d18a6ca478efc69bfa5fd043fe5f60cc",
      "task_cpp_sha256": "e91e4031166610139ba0dff4ede62cc0e4c9b1dbe91cc1b2470147e2af03084f",
      "tracked_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    "candidates": [
      {
        "base_seed": 286444479,
        "bootstrap_seed": 1359235875,
        "cell_id": "u2of5_eta4of5_rho3of4",
        "eta": "4/5",
        "ordinal": 0,
        "plan_config_hash": "63e3b7a839b74fcb778f4d176b995fd3730e1b57edd052eab2b2172f385d5b52",
        "rho": "3/4",
        "stratum": "LOW",
        "utilization": "2/5"
      },
      {
        "base_seed": 1414971527,
        "bootstrap_seed": 1691179649,
        "cell_id": "u3of5_eta1_rho3of4",
        "eta": "1",
        "ordinal": 1,
        "plan_config_hash": "85120b5df6b09b7fb9bd36f2103ebb176fd0a33707bccad4ab3eab87b8255924",
        "rho": "3/4",
        "stratum": "LOW",
        "utilization": "3/5"
      },
      {
        "base_seed": 1495617915,
        "bootstrap_seed": 201313959,
        "cell_id": "u3of5_eta4of5_rho3of4",
        "eta": "4/5",
        "ordinal": 2,
        "plan_config_hash": "3d1d144d2d7c83e6727d2d3259c85bda048c54796c7be0bf23345b88863d649c",
        "rho": "3/4",
        "stratum": "MEDIUM",
        "utilization": "3/5"
      },
      {
        "base_seed": 2133691735,
        "bootstrap_seed": 723195219,
        "cell_id": "u3of5_eta4of5_rho1of4",
        "eta": "4/5",
        "ordinal": 3,
        "plan_config_hash": "b636411b144458d0bbd9016642adbf8e9d5fef09ccc9eaf7090a3d1c6250d33a",
        "rho": "1/4",
        "stratum": "MEDIUM",
        "utilization": "3/5"
      },
      {
        "base_seed": 1836283843,
        "bootstrap_seed": 1476246196,
        "cell_id": "u4of5_eta4of5_rho3of4",
        "eta": "4/5",
        "ordinal": 4,
        "plan_config_hash": "79cf21221600d08e90f5207e2d0543019449a1b4fb057e607dbc3ef8a46a05a3",
        "rho": "3/4",
        "stratum": "HIGH",
        "utilization": "4/5"
      },
      {
        "base_seed": 359775721,
        "bootstrap_seed": 1442799089,
        "cell_id": "u4of5_eta3of5_rho1of2",
        "eta": "3/5",
        "ordinal": 5,
        "plan_config_hash": "a531ac96e59621e57fd5c3a8ae8c917fe1a39f9e1ce82a819f0ee57f6de746b3",
        "rho": "1/2",
        "stratum": "HIGH",
        "utilization": "4/5"
      }
    ],
    "contract_status": "FROZEN",
    "contract_version": "EXT1B_B1_CONFIRM_PILOT_CONTRACT_R1",
    "evidence": {
      "confirm_generation_identity_ledger_sha256": "e6fd834a3605501c340999773acc44be30ee8f868492b1ceb41ce448580e00c1",
      "confirm_plan_sha256": "f2e55900f9805399819b6002f5d8d1484861cbb7b1912016d6cdf643c0b11b21",
      "inherited_parameter_comparison_sha256": "57ae5af356982d89d81f4c45a5154e5964c3939113e676ae5d1a508ff2dfc50b",
      "no_overlap_ledger_sha256": "41cb7955afdcf43c8ce3c60679f4f4c54c23e79f992b8ab3e77748c3e258dc98",
      "r2_generation_identity_ledger_sha256": "4b07647b1733c1f0312dc3b9ee7bc7a99a49457fbb0d3e4962e032b0ab5eab3e"
    },
    "execution_policy": {
      "checkpoint_every_requests_per_shard": 9,
      "fail_fast_on_p0": true,
      "future_output_root_placeholder": "/tmp/ext1b_b1_confirm_pilot_r1",
      "manifest_policy": "exact coverage and SHA-256 for every shard; extra files fail",
      "max_parallel_cell_runners": 4,
      "native_attempts_per_request": 1,
      "native_timeout_seconds": 30,
      "preserve_attempt_history": true,
      "resume_policy": "same canonical config and request plan; validate existing terminal identity; execute only missing terminals; prove byte/id/row-count idempotence",
      "retry_policy": "structural retry only, deterministic attempts 0..15; no outcome-based regeneration; terminal timeout/internal error is retained and fails hard gates",
      "worker_count_per_shard": 1
    },
    "generation": {
      "all_retry_preimage_count": 4800,
      "base_seed_algorithm": "int(domain_hash(B1_CONFIRM_PILOT_R1,{scenario:BYPASS_STRESS:B1,utilization,eta,rho})[:16],16) mod 2147483647",
      "domain": "B1_CONFIRM_PILOT_R1",
      "independent_generation_identities": 300,
      "retry_index_range": [
        0,
        15
      ],
      "scheduler_enters_generation_seed": false,
      "seed_algorithm_identity": "ASAP_BLOCK:V9.3:TASKSET_SEED:v1 / generation_dimensions",
      "seed_chain": "phase/scenario/cell-bound base_seed, then {base_seed,generation_id,source_taskset_index}; source_taskset_index=logical_index*16+attempt_index",
      "taskset_index_range": [
        1000,
        1049
      ]
    },
    "gates": {
      "hard_correctness": {
        "complete_nine_way_pairs": 300,
        "duplicate_generation_identity": 0,
        "duplicate_request_id": 0,
        "fairness_failure": 0,
        "input_mismatch_inside_pair": 0,
        "internal_error": 0,
        "manifest_failure": 0,
        "missing_retained_trace": 0,
        "parse_failure": 0,
        "planned_cells": 6,
        "planned_pairs": 300,
        "planned_requests": 2700,
        "resume_non_idempotence": 0,
        "scheduler_dependent_generation": 0
      },
      "horizon": {
        "SIM_HORIZON_INSUFFICIENT": 0,
        "automatic_horizon_increase_prohibited": true,
        "insufficient_to_miss_rewrite_prohibited": true
      },
      "mechanism_per_cell": {
        "asap_nonblock_native_bypass_activation_minimum": 45,
        "denominator": 50,
        "formal_runtime_activated_minimum": 45,
        "formal_runtime_observable_required": 50,
        "median_bypass_count_per_1000_ticks_strictly_positive": true,
        "structural_activation_required": 50
      },
      "pilot_p_value_gate": false,
      "stratification": {
        "failure_status": "STRATIFICATION_NOT_CONFIRMED",
        "group_pairs": 100,
        "metric_only": "bypass_count_per_1000_ticks",
        "rule": "median_LOW < median_MEDIUM < median_HIGH"
      }
    },
    "metrics": {
      "candidate_selection_inputs": [
        "cell_health",
        "ASAP-NONBLOCK native bypass activation",
        "bypass_count_per_1000_ticks",
        "original deterministic quantiles",
        "exact-rational parameter lexicographic tie-break"
      ],
      "per_cell_reporting": [
        "paired_tasksets",
        "activation_count/50",
        "activation_ratio",
        "95% Wilson interval",
        "bypass intensity min/p25/median/mean/p75/p95/max"
      ],
      "primary": [
        "ASAP-NONBLOCK bypass_count > 0",
        "ASAP-NONBLOCK bypass_count_per_1000_ticks = bypass_count/400*1000",
        "structural_activation",
        "formal runtime_observable",
        "formal runtime_activation"
      ],
      "primary_analysis_unit": "paired_taskset",
      "quantile_algorithm": "linear interpolation at p*(n-1)",
      "secondary": [
        "first_miss_time",
        "missed_jobs",
        "completed_jobs",
        "maximum_observed_response_time",
        "mean_response_time",
        "energy_blocked_ticks",
        "scheduler-specific terminal status",
        "same-timing-family BLOCK/NONBLOCK/SYNC paired descriptive differences"
      ],
      "secondary_outcomes_may_select_candidates": false,
      "stratum_reporting": "pool the two frozen cells into 100 paired tasksets and report activation/Wilson plus intensity min/p25/median/mean/p75/p95/max",
      "wilson_interval": "95%, z=1.959963984540054, no continuity correction"
    },
    "native_confirm_invocations_at_freeze": 0,
    "parameters": {
      "activation_policy": "REPORT_STRUCTURAL_AND_RUNTIME",
      "affordable_prefix_length": 1,
      "battery_capacity": "100",
      "cores": 4,
      "deadline_mode": "constrained",
      "deadline_ratio_range": [
        "0",
        "1"
      ],
      "harvest_phase_policy": "PEAK_SYNTHETIC",
      "horizon": 400,
      "horizon_extension_policy": "none",
      "initial_energy_policy": "STRUCTURAL_MIDPOINT",
      "maximum_horizon": 400,
      "minimum_jobs_per_task": 2,
      "parameter_status": "PILOT",
      "priority_policy": "RM",
      "priority_power_profile": "HIGH_PRIORITY_HIGH_POWER",
      "release_pattern": "SYNCHRONOUS",
      "retain_trace": true,
      "scenario_kind": "BYPASS_STRESS",
      "scenario_subtype": "B1",
      "structural_retry_limit": 16,
      "task_count": 10
    },
    "prohibitions": [
      "candidate replacement/addition",
      "post-result seed/threshold/horizon edits",
      "scheduler outcome based cell selection",
      "B2",
      "B3",
      "EXT-1A",
      "formal execution",
      "native Task/scheduler/parser/RTA/generator/analyzer semantic changes"
    ],
    "research_questions": {
      "primary": [
        "independent-sample NONBLOCK bypass opportunity persistence",
        "native and formal runtime observable consistency",
        "LOW/MEDIUM/HIGH bypass-intensity replication"
      ],
      "secondary": "descriptive BLOCK/NONBLOCK/SYNC downstream differences after all inputs are frozen"
    },
    "scale": {
      "candidate_cells": 6,
      "pairs_per_cell": 50,
      "requests_per_cell": 450,
      "retained_traces_required": 2700,
      "schedulers_per_pair": 9,
      "total_pairs": 300,
      "total_requests": 2700
    },
    "scheduler_order": [
      "gpfp_asap_block",
      "gpfp_asap_nonblock",
      "gpfp_asap_sync",
      "gpfp_alap_block",
      "gpfp_alap_nonblock",
      "gpfp_alap_sync",
      "gpfp_st_block",
      "gpfp_st_nonblock",
      "gpfp_st_sync"
    ],
    "schema": "EXT1B_B1_CONFIRM_PILOT_CONTRACT_V1",
    "source": {
      "candidate_blinding_audit_path": "/tmp/ext1b_active_miss_parser_fix_reparse_r2/candidate_blinding_audit_r2.json",
      "candidate_decision_sha256": "10c9ab93462794879382d38b803969297f63e3ca54f8cd68d5d81244ee206060",
      "git_commit": "c5048ff22e1363a4392d5b5d621cb3ec2e926d4e",
      "git_tree": "366622f8b3d3ba57e6aadae005b9973964434fba",
      "parser_fix_commit": "c5048ff22e1363a4392d5b5d621cb3ec2e926d4e",
      "r2_manifest_entry_count": 5925,
      "r2_manifest_sha256": "20aef74f6f32169c5d58c93d78363754d9f9c2116fdaf9a58fbcec53ba4fa51b",
      "r2_root": "/tmp/ext1b_pilot_screen_b1_parserfix_r2",
      "r2_screening_decision_sha256": "7dd5da37686858bc4d7a6aae71b4865f3af97f4a0514dd0a0323396062b50776"
    },
    "status_enum": [
      "B1_CONFIRM_PILOT_CONFIRMED",
      "B1_CONFIRM_PILOT_MECHANISM_NOT_CONFIRMED",
      "STRATIFICATION_NOT_CONFIRMED",
      "B1_CONFIRM_PILOT_HARD_GATE_FAILED"
    ],
    "tracked_config": {
      "file_sha256": "991852a58eba7a6f1c351bfc4f36f4ff8447d68ecfcf9c00a2f2746c5967438a",
      "path": "configs/v9_3_ext1b1_confirm_pilot_r1.yaml",
      "role": "first frozen shard and strict-schema expansion template",
      "semantic_config_hash": "1326d2ca76c71f7b54751d0541690a5cf298a319261fe065054f50178d519c55"
    }
  },
  "schema": "EXT1B_B1_CONFIRM_PILOT_CONTRACT_ENVELOPE_V1"
}
```
<!-- EXT1B_B1_CONFIRM_CONTRACT_JSON_END -->

## Interpretation and execution boundary

The confirm sample is new: logical indices 1000–1049 map through the existing
16-attempt rule to source taskset indices 16000–16799. The phase/scenario/cell
preimage first produces a cell-specific `base_seed`; the existing
`ASAP_BLOCK:V9.3:TASKSET_SEED:v1` derivation then consumes that seed, the frozen
generation-dimensions ID, and source taskset index. Scheduler identity enters
only the simulation request ID, after the paired instance is materialized.

The trace-free materialized plan contains 2700 rows and invoked the native
simulation boundary zero times. Full comparison against R2 found zero overlap
for 300 versus 135 accepted generation preimages, 4800 versus 2160 possible
retry preimages, 300 versus 135 semantic taskset hashes, and 2700 versus 1215
request IDs. Every planned pair has the registry's nine schedulers in order and
one shared fairness input hash.

The later run may create `/tmp/ext1b_b1_confirm_pilot_r1` only after this
contract hash is committed. At most four single-worker cell shards may run in
parallel. All 2700 traces must be retained. An interrupted run may resume only
against the same canonical shard config and frozen plan; existing terminal
identities are validated and only missing terminals run. A completed timeout or
internal-error terminal is evidence, not permission to regenerate a taskset.

Secondary scheduler outcomes are descriptive and cannot alter candidates,
strata, parameters, seeds, horizon, metrics, or gates. `SIM_HORIZON_INSUFFICIENT`
fails the pilot horizon gate and may not be repaired in-place by raising the
horizon or rewriting the terminal status. No terminal outcome is assumed in
advance.
