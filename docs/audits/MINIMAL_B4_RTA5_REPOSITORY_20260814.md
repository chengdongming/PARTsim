# PARTsim Minimal B4/RTA5 Repository Cleanup Audit

Cleanup type: paper-target minimalization in a separate cleanup worktree.
The original worktree was not modified. No formal experiment or AutoDL
operation was performed.

## Baseline and branch

```text
BASE_COMMIT=2396bf9af120f7d2ff2ad7874207d3d15ca62740
BASE_TREE=dee646d98bdb7080b941a8f40acc271ca7fb7056
CLEANUP_BRANCH=cleanup/minimal-b4-rta5-20260814
CLEANUP_WORKTREE=/tmp/PARTsim-minimal-b4-rta5-20260814
```

The cleanup worktree was created from the exact current master commit. The
cleanup candidate plan was generated from the BASE tree and rechecked against
the final tree:

```text
KEEP_PLAN_MISSING_FILES=0
DELETE_PLAN_STILL_TRACKED_FILES=0
```

## Deletion summary

```text
BASE_TRACKED_FILES=2468
BASE_TRACKED_BYTES=62462884
DELETED_FILES=1914
DELETED_BYTES=49375864
TRACKED_FILES_AFTER=556
TRACKED_BYTES_AFTER=13088439
ARTIFACTS_DELETED=1210
ARTIFACT_BYTES_DELETED=39573763
ADDED_FILES_BEFORE_AUDIT=1
ADDED_BYTES_BEFORE_AUDIT=1951
MODIFIED_FILES_BEFORE_AUDIT=4
```

All tracked artifacts were removed. Current research outputs, formal campaign
artifacts, historical package material, obsolete configurations, retired
experiment implementations, inactive scripts, and superseded tests were
removed according to the generated closure plan.

The following repository/runtime items were explicitly retained after build
and test closure checks:

- `.clang-format`, `.gitignore`, `.gitattributes`, and `cmakeopts/LICENSE`;
- current B4-PE V5 execution, materialization, analysis, audit, statistics,
  and manifest protocol files;
- current RTA V5 sources and their V2/V3 compatibility dependencies;
- the C++ simulator/build tree, `utils/unified_logger.hpp`, the C++ test mock,
  and the tracked simulator manifest input;
- the frozen serializer fixture and the schema/dictionary inputs used by the
  current tests;
- `experiments/b4_priority_energy/manifest_protocol_v4.json`.

The orphan tracked `.claude/worktrees/agent-a3555928` gitlink was removed.
The current repository vendors the required `cmakeopts` and `rtsim/cmdarg`
source trees directly, so `.gitmodules` was removed as stale metadata.

```text
GITLINKS_AFTER=0
SUBMODULES_AFTER=0
GITMODULES_REMOVED_AS_STALE_METADATA=true
```

The final tree contains no gitlinks, and `git submodule status` is empty.

## Scope protection

```text
SCIENTIFIC_LOGIC_CHANGED=false
CURRENT_PRODUCTION_EXECUTION_LOGIC_CHANGED=false
FORMAL_EXPERIMENT_STARTED=false
AUTODL_OPERATION=false
```

No current RTA mathematical source, scheduler implementation, simulator
implementation, B4 algorithm, or current RTA V5 implementation was modified.
The only non-deletion source-surface changes are repository scope/build-surface
metadata, the root README, the current method-registry test cleanup, and this
audit report.

## Validation

```text
PYTHON_FULL_TARGET=1208 passed, 0 failed
B4_TESTS=499 passed, 0 failed
RTA4_PRODUCTION_MANIFEST_TESTS=8 passed, 0 failed
CMAKE_CONFIGURE=PASS
CMAKE_RELEASE_BUILD=PASS
CTEST=PASS (no tests registered)
TEST_LIBRTSIM=428 passed
TEST_ASAP_ENERGY_MODEL=6 passed
GIT_DIFF_CHECK=PASS
```

The Python target was run with:

```text
PYTHONDONTWRITEBYTECODE=1
PYTHONPATH=.:experiments/b4_priority_energy:experiments/b4_priority_energy/tests
python3 -m pytest -q test experiments/b4_priority_energy/tests
```

The clean CMake build used `/tmp/PARTsim-minimal-final-build` with
`CMAKE_BUILD_TYPE=Release` and `BUILD_TESTING=ON`. `ctest` reported no
registered tests; the two built GoogleTest binaries were run directly and
passed, including all 428 `test_librtsim` tests.

## Commit structure

Cleanup was kept in scoped commits: artifacts; superseded configs/documents;
obsolete experiment implementations/scripts; obsolete tests and auxiliary
tooling; inactive build/repository surface; current scope documentation; and
subsequent narrow closure restorations found by the build/test gates. Master
was not modified, merged, force-pushed, or auto-merged.

The audit report is committed separately after the deletion and validation
gates.

```text
LEGACY_MINIMALIZATION_CLEANUP_COMPLETE=true
NO_FORMAL_EXPERIMENT_STARTED=true
NO_AUTODL_OPERATION=true
ORIGINAL_WORKTREE_UNCHANGED=true
```
