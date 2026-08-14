# PARTsim Legacy RTA/RTA4 Infrastructure Simplification Audit

Cleanup type: de-engineering of transitional RTA/RTA4 experiment
infrastructure in an isolated worktree. Scientific RTA code, scheduler code,
simulator code, and the B4-PE experiment were out of scope.

## Baseline and worktree

```text
BASE_COMMIT=0ebe27669eb3c798e45f6acc038076c79ff58c3f
BASE_TREE=8e366a28a8dea2a108ea08e56c51a017f294b3a5
CLEANUP_BRANCH=cleanup/simplify-rta-experiments
CLEANUP_WORKTREE=/tmp/PARTsim-simplify-rta-experiments
CLEANUP_COMMIT=4986f1ade58536be0a532fc42ea8ee3945c54e34
```

The original worktree was not modified. No remote operation, merge, AutoDL
operation, parameter tuning, or formal experiment was performed.

## Scope and deletion

```text
BASE_TRACKED_FILES=524
DELETED_FILES=39
MODIFIED_FILES=20
ADDED_FILES=1
TRACKED_FILES_AFTER_CLEANUP_COMMIT=486
B4_FILES_CHANGED=0
```

Removed material was limited to retired authorization, freeze, pilot,
lifecycle, repository-lineage, production-manifest, legacy V1/V2 pipeline,
publication/plotting, and obsolete execution-engine infrastructure, together
with its superseded governance configurations and documentation.

The retained direct path is:

```text
scientific configuration -> deterministic tasksets -> exact plan
-> CW/LOC/PH/SEQ execution -> timeout/workers -> persistence/resume -> analysis
```

The current V5 runner no longer requires authorization, freeze, pilot,
lifecycle, production-manifest, paper-result, or not-for-paper acknowledgement
arguments. Runtime material is still explicitly bound and resume configuration
is still checked.

## Protocol preservation

```text
DETERMINISTIC_SEED_RETAINED=true
TASKSET_SOURCE_IDENTITY_RETAINED=true
MATHEMATICAL_REQUEST_ID_RETAINED=true
EXECUTION_ID_RETAINED=true
CONFIG_HASH_RETAINED=true
COUNT_DUPLICATE_MISSING_PAIRING_CHECKS_RETAINED=true
RESUME_CONFIGURATION_CHECK_RETAINED=true
CW_LOC_PH_SEQ_RETAINED=true
FIXED_D_RETAINED=true
CORE3_MODEL_ENERGY_UNIT=1mJ
CORE3_PHYSICAL_PROJECTION=model_value/1000
CORE5A_LEGACY_GRID_RETAINED=true
CORE5A_NEW_GRID_RETAINED=true
CORE5A_LEGACY_POINTS=11
CORE5A_LEGACY_EXECUTIONS=4400
CORE5A_NEW_POINTS=16
CORE5A_NEW_EXECUTIONS=1920
```

Plan-only gates did not invoke a simulator:

```text
PERF_G_CAL_REQUESTS=6750
PERF_G_FORMAL_REQUESTS=43200
SENS_SMALL_REQUESTS=12000
CORE5A_MATHEMATICAL_REQUESTS=640
CORE5A_EXECUTIONS=1920
RTA_LOAD_CROSS_BOUNDED_SMOKE=PASS
```

## Validation

```text
PYTHON_CURRENT_TARGET=770 passed, 0 failed
PY_COMPILE=PASS
CMAKE_CONFIGURE=PASS
CMAKE_RELEASE_BUILD=PASS
TEST_LIBRTSIM=428 passed, 0 failed
TEST_ASAP_ENERGY_MODEL=6 passed, 0 failed
GIT_DIFF_CHECK=PASS
```

The baseline Python gate had 788 collected tests with one pre-existing
environment-sensitive multiprocessing failure (`distinct_worker_pids=1`);
the post-cleanup target excludes deleted legacy-infrastructure tests and
passes completely.

## Scope protection

```text
RTA_MATH_CHANGED=false
FIXED_D_MATH_CHANGED=false
TASK_GENERATION_CHANGED=false
ENERGY_SERVICE_CHANGED=false
CORE3_PROJECTION_CHANGED=false
CORE5A_SCIENCE_CHANGED=false
CORE5B_SCIENCE_CHANGED=false
RESULT_STATUS_SEMANTICS_CHANGED=false
SCHEDULER_SEMANTICS_CHANGED=false
SIMULATOR_SEMANTICS_CHANGED=false
AUTHORIZATION_REQUIRED_AFTER=false
FREEZE_REQUIRED_AFTER=false
PILOT_REQUIRED_AFTER=false
LIFECYCLE_REQUIRED_AFTER=false
PRODUCTION_MANIFEST_REQUIRED_AFTER=false
REPOSITORY_LINEAGE_REQUIRED_AFTER=false
PAPER_RESULT_AUTHORIZATION_REQUIRED_AFTER=false
ACKNOWLEDGE_NOT_FOR_PAPER_REQUIRED_AFTER=false
MINIMAL_REPRODUCIBILITY_RETAINED=true
FORMAL_EXPERIMENT_STARTED=false
AUTODL_OPERATION=false
ORIGINAL_WORKTREE_UNCHANGED=true
```

The cleanup commit is intentionally local and remains unpushed.
