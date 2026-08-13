# PARTsim Current Legacy Cleanup Audit

Cleanup type: frozen candidate deletion with external archive. No scientific
or production execution implementation was changed.

## Baseline and scope

```text
BASE_COMMIT=8c58c2bd01140eec70f775a0caabfff424b588a8
BASE_TREE=f3454140d9a4680a612bfcc5ead3a92d2904316b
PRE_CLEANUP_REPAIR=21da963cf5843fe16cdbbad8734dfcf119dcfcff
MERGE_TREE_EQUALS_TESTED_REPAIR_TREE=true
```

PR #85 was already in BASE. Its only three files were rechecked against the
frozen cleanup candidate paths; no new SAFE_DELETE or ARCHIVE_THEN_DELETE
references were found.

```text
CLEANUP_CANDIDATE_DELTA_REVALIDATION=PASS
NEW_REPAIR_TO_SAFE_DELETE_REFERENCE_COUNT=0
NEW_REPAIR_TO_ARCHIVE_CANDIDATE_REFERENCE_COUNT=0
```

The cleanup branch was created from the exact BASE commit in a separate
writable worktree. The previous read-only cleanup worktree was not reused.

## Deleted candidates

```text
SAFE_DELETE_FILES=26
SAFE_DELETE_BYTES=235502
ARCHIVE_DELETE_FILES=51
ARCHIVE_DELETE_BYTES=2739137
TOTAL_DELETED_FILES=77
TOTAL_DELETED_BYTES=2974639
ARTIFACTS_DELETED=0
```

The 26 SAFE_DELETE files were the 23-file top-level duplicate `cmdarg/` tree
and three tracked `rtsim/cmdarg` build outputs. The 51 archived files were the
39 files under `old/`, the tracked `librtsim` backup, seven `tools/screens`
files, `solar_linear_model.png`, and three tracked historical log files.

Deletion was performed in three commits:

```text
de06de581a38096013ffcbf04538b63648d93937  cleanup: remove tracked cmdarg build outputs
8afa3999ea93868753e629eaeda72c3a83d9f16b  cleanup: remove unused duplicate top-level cmdarg tree
aee1fa72d948d432062194055fde4e9801b35dbf  cleanup: archive and remove obsolete legacy files
a8a367c146a55cb5770ab3544345acd3dc55cc87  chore: ignore local logs and cmdarg build outputs
```

The BASE-to-cleanup diff contains exactly 77 deletions and one narrow
`.gitignore` modification. No other additions or modifications were made
before this report.

## External archive

The 51 ARCHIVE_THEN_DELETE files were archived outside the repository before
deletion. The archive contains source identities with relative path, BASE Git
blob SHA, blob size, and blob SHA-256.

```text
ARCHIVE_PATH=/tmp/PARTsim_legacy_archive_20260813_222228
ARCHIVE_ENTRIES=51
ARCHIVE_BYTES=2739137
ARCHIVE_MEMBER_CHECK=PASS
ARCHIVE_SHA256=3d29ac9ae03103e1ec4584f7dd8e7f2436adf43f29c182ab6cfd38a15a85a602
```

Archive files:

```text
ARCHIVE_SOURCE_IDENTITIES.tsv
legacy_files.tar.gz
legacy_files.tar.gz.sha256
ARCHIVE_README.txt
```

## Protected scope

The following scopes have no cleanup diff: `artifacts/`, all
`experiments/v9_3/`, `experiments/b4_priority_energy/`, `configs/`,
`scripts/`, and `test/`. RTA mathematical files, scheduler implementation,
and simulator implementation are unchanged. The only `librtsim/` and `rtsim/`
changes are the five explicitly frozen deletions.

```text
SCIENTIFIC_CODE_CHANGED=false
PRODUCTION_EXECUTION_CODE_CHANGED=false
```

The v1.3.12 runner microcase archive remains byte-frozen:

```text
V1_3_12_SHA256=fd7fa5fc54110cb6e61fd156002dbe0a2698755215dec4d876c1dea4d71558d4
V1_3_12_FREEZE=PASS
```

The 88 REVIEW files remain tracked: 42 v1.3.10 package files, 42 v1.3.11
package files, and four manually reviewed tools.

```text
REVIEW_FILES_RETAINED=88
```

## Validation

```text
POST_CLEANUP_COLLECTION=4282
POST_CLEANUP_TARGETED=903 passed, 0 failed
POST_CLEANUP_FULL_PYTEST=3575 passed, 129 skipped, 34 warnings, 0 failed
CMAKE_CONFIGURE=PASS
CMAKE_BUILD=PASS
CTEST=PASS (no tests registered)
CPP_TESTS=428 passed + 6 passed
GIT_DIFF_CHECK=PASS
```

The full Python suite was run from `test/` as required; its 3704 collected
items equal 3575 passed plus 129 skipped. The unrestricted collection check
remained 4282. No formal experiment was started and AutoDL was not used.

## Final state

This report is committed separately after all deletion and validation gates.
The cleanup branch is intended to be pushed for review only; master is not
modified or merged automatically.

```text
LEGACY_FILES_DELETED=77
ARTIFACTS_DELETED=0
NO_FORMAL_EXPERIMENT_STARTED=true
NO_AUTODL_OPERATION=true
```
