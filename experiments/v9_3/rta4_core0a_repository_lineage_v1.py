"""Fail-closed Git lineage validation for the RTA4 CORE-0A freeze.

The validator reconstructs every identity input from the supplied worktree.
It does not trust branch names, remotes, commit messages, caller-selected
commits, path sets, blob digests, or lineage classifications.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any, Iterable, Mapping, Sequence
import unicodedata


CORE0A_REPOSITORY_LINEAGE_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_REPOSITORY_LINEAGE_V1"
)
CORE0A_REPOSITORY_LINEAGE_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_MASTER_EVOLUTION_LINEAGE_V1"
)
CORE0A_REPOSITORY_LINEAGE_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:REPOSITORY_LINEAGE:v1"
)

CORE0A_STANDALONE_REVIEWED_LINEAGE = (
    "CORE0A_STANDALONE_REVIEWED_LINEAGE"
)
MASTER_INTEGRATION_MERGE = "MASTER_INTEGRATION_MERGE"
MASTER_PR_WRAPPER_MERGE = "MASTER_PR_WRAPPER_MERGE"

CORE0A_REVIEWED_ANCHOR_COMMIT = (
    "51149fa918ae9a753293116737174ffe1c0f680a"
)
CORE0A_REVIEWED_ANCHOR_TREE = (
    "d9f7847efea88f864bfda88035d089478b1e0333"
)

CORE0A_LINEAGE_REPAIR_SCOPE = frozenset({
    "experiments/v9_3/rta4_core0a_repository_lineage_v1.py",
    "experiments/v9_3/rta4_core0a_pilot_v2.py",
    "experiments/v9_3/rta4_production_build_manifest.py",
    "test/test_v9_3_rta4_core0a_repository_lineage_v1.py",
    "test/test_v9_3_rta4_core0a_pilot_freeze_v2.py",
    "test/test_v9_3_rta4_production_build_manifest.py",
})

_KNOWN_TREE_MODES = {
    "100644": "REGULAR_BLOB",
    "100755": "EXECUTABLE_BLOB",
    "120000": "SYMLINK_BLOB",
    "160000": "GITLINK",
}
_UNFINISHED_GIT_PATHS = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "BISECT_LOG",
    "rebase-apply",
    "rebase-merge",
    "sequencer",
)


class Core0ARepositoryLineageV1Error(ValueError):
    """Raised when repository state or history is outside the reviewed contract."""


@dataclass(frozen=True)
class BlobStateV1:
    path: str
    state: str
    object_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "state": self.state,
            "object_id": self.object_id,
        }


@dataclass(frozen=True)
class ValidatedCore0ARepositoryLineageV1:
    lineage_mode: str
    current_head_commit: str
    current_head_tree: str
    anchor_commit: str
    anchor_tree: str
    current_parent_count: int
    first_parent_commit: str | None
    first_parent_tree: str | None
    second_parent_commit: str | None
    second_parent_tree: str | None
    merge_base_commit: str | None
    merge_base_tree: str | None
    master_changed_paths: tuple[str, ...]
    core0a_changed_paths: tuple[str, ...]
    overlap_paths: tuple[str, ...]
    master_path_set_digest: str
    core0a_path_set_digest: str
    overlap_path_set_digest: str
    master_only_blob_states: tuple[BlobStateV1, ...]
    core0a_only_blob_states: tuple[BlobStateV1, ...]
    master_only_blob_state_digest: str
    core0a_only_blob_state_digest: str
    final_integration_tree: str
    worktree_clean: bool
    repair_scope_path_digest: str
    repository_lineage_identity: str

    def identity_material(self) -> dict[str, Any]:
        return {
            "schema": CORE0A_REPOSITORY_LINEAGE_SCHEMA,
            "contract_version": (
                CORE0A_REPOSITORY_LINEAGE_CONTRACT_VERSION
            ),
            "lineage_mode": self.lineage_mode,
            "current_head_commit": self.current_head_commit,
            "current_head_tree": self.current_head_tree,
            "anchor_commit": self.anchor_commit,
            "anchor_tree": self.anchor_tree,
            "current_parent_count": self.current_parent_count,
            "first_parent_commit": self.first_parent_commit,
            "first_parent_tree": self.first_parent_tree,
            "second_parent_commit": self.second_parent_commit,
            "second_parent_tree": self.second_parent_tree,
            "merge_base_commit": self.merge_base_commit,
            "merge_base_tree": self.merge_base_tree,
            "master_changed_path_count": len(self.master_changed_paths),
            "core0a_changed_path_count": len(self.core0a_changed_paths),
            "overlap_path_count": len(self.overlap_paths),
            "master_path_set_digest": self.master_path_set_digest,
            "core0a_path_set_digest": self.core0a_path_set_digest,
            "overlap_path_set_digest": self.overlap_path_set_digest,
            "master_only_blob_state_digest": (
                self.master_only_blob_state_digest
            ),
            "core0a_only_blob_state_digest": (
                self.core0a_only_blob_state_digest
            ),
            "final_integration_tree": self.final_integration_tree,
            "worktree_clean": self.worktree_clean,
            "repair_scope_path_digest": self.repair_scope_path_digest,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.identity_material(),
            "repository_lineage_identity": (
                self.repository_lineage_identity
            ),
        }


@dataclass(frozen=True)
class _DiffPaths:
    changed_paths: tuple[str, ...]
    rename_or_copy: tuple[str, ...]


@dataclass(frozen=True)
class _LineageFacts:
    lineage_mode: str
    current_head_commit: str
    current_head_tree: str
    current_parent_count: int
    first_parent_commit: str | None
    first_parent_tree: str | None
    second_parent_commit: str | None
    second_parent_tree: str | None
    merge_base_commit: str | None
    merge_base_tree: str | None
    master_changed_paths: tuple[str, ...]
    core0a_changed_paths: tuple[str, ...]
    overlap_paths: tuple[str, ...]
    master_only_blob_states: tuple[BlobStateV1, ...]
    core0a_only_blob_states: tuple[BlobStateV1, ...]
    repair_touched_paths: tuple[str, ...]


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _domain_hash(domain: str, value: Any) -> str:
    return hashlib.sha256(
        domain.encode("utf-8") + b"\0" + _canonical_json_bytes(value)
    ).hexdigest()


def _path_set_digest(label: str, paths: Sequence[str]) -> str:
    return _domain_hash(
        f"{CORE0A_REPOSITORY_LINEAGE_DOMAIN}:{label}:PATH_SET",
        list(paths),
    )


def _blob_state_digest(
    label: str, rows: Sequence[BlobStateV1],
) -> str:
    return _domain_hash(
        f"{CORE0A_REPOSITORY_LINEAGE_DOMAIN}:{label}:BLOB_STATE",
        [row.as_dict() for row in rows],
    )


def _run(
    root: Path, arguments: Sequence[str], *, binary: bool = False,
) -> bytes | str:
    completed = subprocess.run(
        tuple(arguments),
        cwd=str(root),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
    )
    if completed.returncode:
        stderr = (
            completed.stderr.decode("utf-8", "replace")
            if binary else completed.stderr
        )
        raise Core0ARepositoryLineageV1Error(
            f"Git command failed: {' '.join(arguments)}: {stderr.strip()}"
        )
    return completed.stdout


def _git_text(root: Path, *arguments: str) -> str:
    value = _run(root, ("git", *arguments))
    assert isinstance(value, str)
    return value.strip()


def _git_bytes(root: Path, *arguments: str) -> bytes:
    value = _run(root, ("git", *arguments), binary=True)
    assert isinstance(value, bytes)
    return value


def _canonical_path(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise Core0ARepositoryLineageV1Error(
            "repository path is not valid UTF-8"
        ) from exc
    if (
        not value
        or value != unicodedata.normalize("NFC", value)
        or "\\" in value
    ):
        raise Core0ARepositoryLineageV1Error(
            f"repository path is not canonical NFC/POSIX: {value!r}"
        )
    parsed = PurePosixPath(value)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise Core0ARepositoryLineageV1Error(
            f"repository path escapes or is not normalized: {value!r}"
        )
    return value


def _canonical_path_tuple(paths: Iterable[str]) -> tuple[str, ...]:
    values = tuple(sorted(paths))
    if len(values) != len(set(values)):
        raise Core0ARepositoryLineageV1Error(
            "repository path set contains duplicates"
        )
    for value in values:
        if _canonical_path(value.encode("utf-8")) != value:
            raise AssertionError("canonical path round trip failed")
    return values


def _source_root(source_root: Path) -> Path:
    try:
        root = Path(source_root).resolve(strict=True)
    except OSError as exc:
        raise Core0ARepositoryLineageV1Error(
            "source_root does not resolve"
        ) from exc
    if not root.is_dir():
        raise Core0ARepositoryLineageV1Error(
            "source_root is not a directory"
        )
    top = Path(_git_text(root, "rev-parse", "--show-toplevel")).resolve(
        strict=True
    )
    if top != root:
        raise Core0ARepositoryLineageV1Error(
            "source_root must be the exact Git worktree root"
        )
    return root


def _require_clean_worktree(root: Path) -> None:
    status = _git_bytes(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
    )
    if status:
        raise Core0ARepositoryLineageV1Error(
            "CORE-0A repository lineage requires a clean tracked and "
            "untracked worktree"
        )
    for name in _UNFINISHED_GIT_PATHS:
        raw = _git_text(root, "rev-parse", "--git-path", name)
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        if path.exists():
            raise Core0ARepositoryLineageV1Error(
                f"unfinished Git operation is present: {name}"
            )


def _require_anchor(root: Path) -> None:
    _git_text(
        root,
        "cat-file",
        "-e",
        f"{CORE0A_REVIEWED_ANCHOR_COMMIT}^{{commit}}",
    )
    observed = _commit_tree(root, CORE0A_REVIEWED_ANCHOR_COMMIT)
    if observed != CORE0A_REVIEWED_ANCHOR_TREE:
        raise Core0ARepositoryLineageV1Error(
            "reviewed CORE-0A anchor tree mismatch"
        )


def _parents(root: Path, commit: str) -> tuple[str, ...]:
    fields = _git_text(
        root, "rev-list", "--parents", "-n", "1", commit,
    ).split()
    if not fields or fields[0] != commit:
        raise Core0ARepositoryLineageV1Error(
            "cannot reconstruct commit parents"
        )
    return tuple(fields[1:])


def _commit_tree(root: Path, commit: str) -> str:
    return _git_text(root, "rev-parse", f"{commit}^{{tree}}")


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        ("git", "merge-base", "--is-ancestor", ancestor, descendant),
        cwd=str(root),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode not in {0, 1}:
        raise Core0ARepositoryLineageV1Error(
            "cannot evaluate Git ancestor relation"
        )
    return completed.returncode == 0


def _merge_base(root: Path, left: str, right: str) -> str:
    completed = subprocess.run(
        ("git", "merge-base", "--all", left, right),
        cwd=str(root),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode not in {0, 1}:
        raise Core0ARepositoryLineageV1Error(
            "cannot reconstruct lineage merge base"
        )
    bases = tuple(filter(None, completed.stdout.strip().splitlines()))
    if len(bases) != 1:
        raise Core0ARepositoryLineageV1Error(
            "lineage parents require exactly one reachable merge base"
        )
    return bases[0]


def _diff_paths(root: Path, base: str, tip: str) -> _DiffPaths:
    output = _git_bytes(
        root,
        "-c",
        "diff.renameLimit=10000",
        "diff",
        "--name-status",
        "-z",
        "--no-ext-diff",
        "--no-textconv",
        "--find-renames",
        "--find-copies-harder",
        base,
        tip,
        "--",
    )
    tokens = output.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    changed: set[str] = set()
    rename_or_copy: list[str] = []
    index = 0
    while index < len(tokens):
        status = tokens[index].decode("ascii", "strict")
        index += 1
        if not status:
            raise Core0ARepositoryLineageV1Error(
                "empty Git diff status"
            )
        if status[0] in {"R", "C"}:
            if index + 1 >= len(tokens):
                raise Core0ARepositoryLineageV1Error(
                    "truncated rename/copy diff record"
                )
            old_path = _canonical_path(tokens[index])
            new_path = _canonical_path(tokens[index + 1])
            index += 2
            changed.update((old_path, new_path))
            rename_or_copy.append(
                f"{status}:{old_path}->{new_path}"
            )
        else:
            if index >= len(tokens):
                raise Core0ARepositoryLineageV1Error(
                    "truncated Git diff record"
                )
            changed.add(_canonical_path(tokens[index]))
            index += 1
    return _DiffPaths(
        changed_paths=_canonical_path_tuple(changed),
        rename_or_copy=tuple(sorted(rename_or_copy)),
    )


def _tree_entries(
    root: Path, commit: str,
) -> dict[str, tuple[str, str]]:
    output = _git_bytes(
        root, "ls-tree", "-rz", "--full-tree", commit,
    )
    entries: dict[str, tuple[str, str]] = {}
    for record in output.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = header.decode(
                "ascii", "strict",
            ).split()
        except (ValueError, UnicodeDecodeError) as exc:
            raise Core0ARepositoryLineageV1Error(
                "malformed Git tree entry"
            ) from exc
        path = _canonical_path(raw_path)
        if mode not in _KNOWN_TREE_MODES:
            raise Core0ARepositoryLineageV1Error(
                f"unrecognized Git tree mode {mode} for {path}"
            )
        expected_type = "commit" if mode == "160000" else "blob"
        if object_type != expected_type or path in entries:
            raise Core0ARepositoryLineageV1Error(
                f"invalid or duplicate Git tree entry for {path}"
            )
        entries[path] = (mode, object_id)
    return entries


def _blob_states(
    root: Path, commit: str, paths: Sequence[str],
) -> tuple[BlobStateV1, ...]:
    tree = _tree_entries(root, commit)
    rows = []
    for path in paths:
        entry = tree.get(path)
        if entry is None:
            rows.append(BlobStateV1(path, "DELETED", None))
        else:
            mode, object_id = entry
            rows.append(
                BlobStateV1(path, _KNOWN_TREE_MODES[mode], object_id)
            )
    return tuple(rows)


def _require_same_states(
    label: str,
    left: Sequence[BlobStateV1],
    right: Sequence[BlobStateV1],
) -> None:
    if tuple(left) != tuple(right):
        raise Core0ARepositoryLineageV1Error(
            f"{label} blob or deletion state differs in final tree"
        )


def _standalone_facts(root: Path, head: str) -> _LineageFacts:
    if not _is_ancestor(root, CORE0A_REVIEWED_ANCHOR_COMMIT, head):
        raise Core0ARepositoryLineageV1Error(
            "reviewed CORE-0A anchor is not an ancestor of standalone HEAD"
        )
    merges = _git_text(
        root,
        "rev-list",
        "--merges",
        f"{CORE0A_REVIEWED_ANCHOR_COMMIT}..{head}",
    )
    if merges:
        raise Core0ARepositoryLineageV1Error(
            "standalone CORE-0A lineage contains a merge commit"
        )

    touched: set[str] = set()
    commits = tuple(filter(None, _git_text(
        root,
        "rev-list",
        "--reverse",
        f"{CORE0A_REVIEWED_ANCHOR_COMMIT}..{head}",
    ).splitlines()))
    for commit in commits:
        parents = _parents(root, commit)
        if len(parents) != 1:
            raise Core0ARepositoryLineageV1Error(
                "standalone repair commit is not linear"
            )
        diff = _diff_paths(root, parents[0], commit)
        if diff.rename_or_copy:
            raise Core0ARepositoryLineageV1Error(
                "standalone repair contains rename/copy status"
            )
        touched.update(diff.changed_paths)
    touched_paths = _canonical_path_tuple(touched)
    forbidden = sorted(
        set(touched_paths).difference(CORE0A_LINEAGE_REPAIR_SCOPE)
    )
    if forbidden:
        raise Core0ARepositoryLineageV1Error(
            f"standalone lineage changed non-repair paths: {forbidden}"
        )

    net = _diff_paths(root, CORE0A_REVIEWED_ANCHOR_COMMIT, head)
    if net.rename_or_copy:
        raise Core0ARepositoryLineageV1Error(
            "standalone repair net diff contains rename/copy status"
        )
    parents = _parents(root, head)
    return _LineageFacts(
        lineage_mode=CORE0A_STANDALONE_REVIEWED_LINEAGE,
        current_head_commit=head,
        current_head_tree=_commit_tree(root, head),
        current_parent_count=len(parents),
        first_parent_commit=None,
        first_parent_tree=None,
        second_parent_commit=None,
        second_parent_tree=None,
        merge_base_commit=None,
        merge_base_tree=None,
        master_changed_paths=(),
        core0a_changed_paths=net.changed_paths,
        overlap_paths=(),
        master_only_blob_states=(),
        core0a_only_blob_states=_blob_states(
            root, head, net.changed_paths,
        ),
        repair_touched_paths=touched_paths,
    )


def _integration_facts(root: Path, head: str) -> _LineageFacts:
    parents = _parents(root, head)
    if len(parents) != 2:
        raise Core0ARepositoryLineageV1Error(
            "master integration requires exactly two parents"
        )
    master_parent, core0a_parent = parents
    standalone = _standalone_facts(root, core0a_parent)
    if standalone.lineage_mode != CORE0A_STANDALONE_REVIEWED_LINEAGE:
        raise Core0ARepositoryLineageV1Error(
            "integration second parent is not reviewed standalone lineage"
        )
    if _is_ancestor(
        root, CORE0A_REVIEWED_ANCHOR_COMMIT, master_parent,
    ):
        raise Core0ARepositoryLineageV1Error(
            "integration first parent is not an independent master side"
        )
    base = _merge_base(root, master_parent, core0a_parent)
    if (
        not _is_ancestor(root, base, master_parent)
        or not _is_ancestor(root, base, core0a_parent)
        or _is_ancestor(root, master_parent, core0a_parent)
        or _is_ancestor(root, core0a_parent, master_parent)
    ):
        raise Core0ARepositoryLineageV1Error(
            "integration parents are not divergent descendants of merge base"
        )

    master_diff = _diff_paths(root, base, master_parent)
    core0a_diff = _diff_paths(root, base, core0a_parent)
    if master_diff.rename_or_copy or core0a_diff.rename_or_copy:
        raise Core0ARepositoryLineageV1Error(
            "integration side contains rename/copy status"
        )
    overlap = _canonical_path_tuple(
        set(master_diff.changed_paths).intersection(
            core0a_diff.changed_paths
        )
    )
    if overlap:
        raise Core0ARepositoryLineageV1Error(
            f"integration changed-path overlap is not supported: {overlap}"
        )

    relative_master = _diff_paths(root, master_parent, head)
    relative_core0a = _diff_paths(root, core0a_parent, head)
    if relative_master.rename_or_copy or relative_core0a.rename_or_copy:
        raise Core0ARepositoryLineageV1Error(
            "integration final diff contains rename/copy status"
        )
    if relative_master.changed_paths != core0a_diff.changed_paths:
        raise Core0ARepositoryLineageV1Error(
            "integration diff relative to first parent is not exact "
            "CORE-0A-only path set"
        )
    if relative_core0a.changed_paths != master_diff.changed_paths:
        raise Core0ARepositoryLineageV1Error(
            "integration diff relative to second parent is not exact "
            "master-only path set"
        )

    master_parent_states = _blob_states(
        root, master_parent, master_diff.changed_paths,
    )
    master_head_states = _blob_states(
        root, head, master_diff.changed_paths,
    )
    _require_same_states(
        "master-only", master_parent_states, master_head_states,
    )
    core0a_parent_states = _blob_states(
        root, core0a_parent, core0a_diff.changed_paths,
    )
    core0a_head_states = _blob_states(
        root, head, core0a_diff.changed_paths,
    )
    _require_same_states(
        "CORE-0A-only", core0a_parent_states, core0a_head_states,
    )

    base_tree = _tree_entries(root, base)
    master_tree = _tree_entries(root, master_parent)
    expected_tree = dict(base_tree)
    for path in master_diff.changed_paths:
        if path in master_tree:
            expected_tree[path] = master_tree[path]
        else:
            expected_tree.pop(path, None)
    core0a_tree = _tree_entries(root, core0a_parent)
    for path in core0a_diff.changed_paths:
        if path in core0a_tree:
            expected_tree[path] = core0a_tree[path]
        else:
            expected_tree.pop(path, None)
    if _tree_entries(root, head) != expected_tree:
        raise Core0ARepositoryLineageV1Error(
            "integration tree is not the exact disjoint combination of parents"
        )

    return _LineageFacts(
        lineage_mode=MASTER_INTEGRATION_MERGE,
        current_head_commit=head,
        current_head_tree=_commit_tree(root, head),
        current_parent_count=2,
        first_parent_commit=master_parent,
        first_parent_tree=_commit_tree(root, master_parent),
        second_parent_commit=core0a_parent,
        second_parent_tree=_commit_tree(root, core0a_parent),
        merge_base_commit=base,
        merge_base_tree=_commit_tree(root, base),
        master_changed_paths=master_diff.changed_paths,
        core0a_changed_paths=core0a_diff.changed_paths,
        overlap_paths=overlap,
        master_only_blob_states=master_parent_states,
        core0a_only_blob_states=core0a_parent_states,
        repair_touched_paths=standalone.repair_touched_paths,
    )


def _wrapper_facts(
    root: Path, head: str, integration: _LineageFacts,
) -> _LineageFacts:
    parents = _parents(root, head)
    if len(parents) != 2:
        raise Core0ARepositoryLineageV1Error(
            "PR wrapper requires exactly two parents"
        )
    first_parent, second_parent = parents
    if (
        integration.lineage_mode != MASTER_INTEGRATION_MERGE
        or second_parent != integration.current_head_commit
    ):
        raise Core0ARepositoryLineageV1Error(
            "PR wrapper second parent is not a valid integration merge"
        )
    if first_parent != integration.first_parent_commit:
        raise Core0ARepositoryLineageV1Error(
            "PR wrapper first parent has base drift"
        )
    if _commit_tree(root, head) != integration.current_head_tree:
        raise Core0ARepositoryLineageV1Error(
            "PR wrapper tree differs from integration tree"
        )
    if _diff_paths(root, second_parent, head).changed_paths:
        raise Core0ARepositoryLineageV1Error(
            "PR wrapper adds, deletes, or modifies content"
        )
    return _LineageFacts(
        lineage_mode=MASTER_PR_WRAPPER_MERGE,
        current_head_commit=head,
        current_head_tree=_commit_tree(root, head),
        current_parent_count=2,
        first_parent_commit=first_parent,
        first_parent_tree=_commit_tree(root, first_parent),
        second_parent_commit=second_parent,
        second_parent_tree=_commit_tree(root, second_parent),
        merge_base_commit=integration.merge_base_commit,
        merge_base_tree=integration.merge_base_tree,
        master_changed_paths=integration.master_changed_paths,
        core0a_changed_paths=integration.core0a_changed_paths,
        overlap_paths=integration.overlap_paths,
        master_only_blob_states=integration.master_only_blob_states,
        core0a_only_blob_states=integration.core0a_only_blob_states,
        repair_touched_paths=integration.repair_touched_paths,
    )


def _integration_anchor_facts(root: Path, head: str) -> _LineageFacts:
    """Validate one candidate integration or PR-wrapper merge."""

    parents = _parents(root, head)
    if len(parents) != 2:
        raise Core0ARepositoryLineageV1Error(
            "unsupported octopus or non-binary lineage topology"
        )

    first_parent, second_parent = parents
    if _is_ancestor(root, first_parent, second_parent):
        return _same_lineage_pr_wrapper_facts(
            root, head, first_parent, second_parent,
        )
    if _is_ancestor(root, second_parent, first_parent):
        raise Core0ARepositoryLineageV1Error(
            "same-lineage PR wrapper parent order is reversed"
        )

    second_parents = _parents(root, parents[1])
    if len(second_parents) == 2:
        try:
            integration = _integration_facts(root, parents[1])
        except Core0ARepositoryLineageV1Error:
            integration = None
        if integration is not None:
            return _wrapper_facts(root, head, integration)
    return _integration_facts(root, head)


def _same_lineage_pr_wrapper_facts(
    root: Path,
    head: str,
    first_parent: str,
    second_parent: str,
) -> _LineageFacts:
    """Validate a content-free PR wrapper around one supported lineage.

    GitHub can materialize a merge commit even when the selected base is
    already an ancestor of the selected head.  This shape is safe only when
    the merge is an exact, ordered wrapper around a recursively valid second
    parent and introduces no tree or path-level change of its own.
    """

    parents = _parents(root, head)
    if parents != (first_parent, second_parent) or len(parents) != 2:
        raise Core0ARepositoryLineageV1Error(
            "same-lineage PR wrapper requires exactly two ordered parents"
        )
    if not _is_ancestor(root, first_parent, second_parent):
        raise Core0ARepositoryLineageV1Error(
            "same-lineage PR wrapper first parent is not an ancestor "
            "of second parent"
        )

    relative_second = _diff_paths(root, second_parent, head)
    if relative_second.rename_or_copy:
        raise Core0ARepositoryLineageV1Error(
            "same-lineage PR wrapper contains rename/copy status"
        )
    if relative_second.changed_paths:
        raise Core0ARepositoryLineageV1Error(
            "same-lineage PR wrapper adds, deletes, or modifies content"
        )
    if _commit_tree(root, head) != _commit_tree(root, second_parent):
        raise Core0ARepositoryLineageV1Error(
            "same-lineage PR wrapper tree differs from second parent tree"
        )

    wrapped = _classify(root, second_parent)
    if wrapped.current_head_commit != second_parent:
        raise Core0ARepositoryLineageV1Error(
            "same-lineage PR wrapper second parent was not reconstructed "
            "as the supported lineage HEAD"
        )
    return _LineageFacts(
        lineage_mode=MASTER_PR_WRAPPER_MERGE,
        current_head_commit=head,
        current_head_tree=_commit_tree(root, head),
        current_parent_count=2,
        first_parent_commit=first_parent,
        first_parent_tree=_commit_tree(root, first_parent),
        second_parent_commit=second_parent,
        second_parent_tree=_commit_tree(root, second_parent),
        merge_base_commit=wrapped.merge_base_commit,
        merge_base_tree=wrapped.merge_base_tree,
        master_changed_paths=wrapped.master_changed_paths,
        core0a_changed_paths=wrapped.core0a_changed_paths,
        overlap_paths=wrapped.overlap_paths,
        master_only_blob_states=wrapped.master_only_blob_states,
        core0a_only_blob_states=wrapped.core0a_only_blob_states,
        repair_touched_paths=wrapped.repair_touched_paths,
    )


def _linear_descendant_facts(
    root: Path,
    head: str,
    anchor: _LineageFacts,
) -> _LineageFacts:
    """Bind a reviewed integration anchor to its linear current HEAD."""

    if anchor.current_head_commit == head:
        return anchor
    return replace(
        anchor,
        current_head_commit=head,
        current_head_tree=_commit_tree(root, head),
        current_parent_count=len(_parents(root, head)),
    )


def _classify(root: Path, head: str) -> _LineageFacts:
    """Classify HEAD without mistaking reviewed merge descendants for standalone."""

    cursor = head
    while cursor != CORE0A_REVIEWED_ANCHOR_COMMIT:
        parents = _parents(root, cursor)
        if len(parents) == 1:
            diff = _diff_paths(root, parents[0], cursor)
            if any(
                row.startswith("R") for row in diff.rename_or_copy
            ):
                raise Core0ARepositoryLineageV1Error(
                    "post-integration linear descendant contains "
                    "rename status"
                )
            _tree_entries(root, cursor)
            cursor = parents[0]
            continue
        if len(parents) == 2:
            anchor = _integration_anchor_facts(root, cursor)
            return _linear_descendant_facts(root, head, anchor)
        if len(parents) > 2:
            raise Core0ARepositoryLineageV1Error(
                "unsupported octopus or non-binary lineage topology"
            )
        raise Core0ARepositoryLineageV1Error(
            "repository lineage terminated before reviewed CORE-0A anchor"
        )
    return _standalone_facts(root, head)


def _validated(facts: _LineageFacts) -> ValidatedCore0ARepositoryLineageV1:
    master_path_digest = _path_set_digest(
        "MASTER", facts.master_changed_paths,
    )
    core0a_path_digest = _path_set_digest(
        "CORE0A", facts.core0a_changed_paths,
    )
    overlap_digest = _path_set_digest(
        "OVERLAP", facts.overlap_paths,
    )
    master_blob_digest = _blob_state_digest(
        "MASTER", facts.master_only_blob_states,
    )
    core0a_blob_digest = _blob_state_digest(
        "CORE0A", facts.core0a_only_blob_states,
    )
    repair_digest = _path_set_digest(
        "REPAIR_SCOPE", facts.repair_touched_paths,
    )
    provisional = ValidatedCore0ARepositoryLineageV1(
        lineage_mode=facts.lineage_mode,
        current_head_commit=facts.current_head_commit,
        current_head_tree=facts.current_head_tree,
        anchor_commit=CORE0A_REVIEWED_ANCHOR_COMMIT,
        anchor_tree=CORE0A_REVIEWED_ANCHOR_TREE,
        current_parent_count=facts.current_parent_count,
        first_parent_commit=facts.first_parent_commit,
        first_parent_tree=facts.first_parent_tree,
        second_parent_commit=facts.second_parent_commit,
        second_parent_tree=facts.second_parent_tree,
        merge_base_commit=facts.merge_base_commit,
        merge_base_tree=facts.merge_base_tree,
        master_changed_paths=facts.master_changed_paths,
        core0a_changed_paths=facts.core0a_changed_paths,
        overlap_paths=facts.overlap_paths,
        master_path_set_digest=master_path_digest,
        core0a_path_set_digest=core0a_path_digest,
        overlap_path_set_digest=overlap_digest,
        master_only_blob_states=facts.master_only_blob_states,
        core0a_only_blob_states=facts.core0a_only_blob_states,
        master_only_blob_state_digest=master_blob_digest,
        core0a_only_blob_state_digest=core0a_blob_digest,
        final_integration_tree=facts.current_head_tree,
        worktree_clean=True,
        repair_scope_path_digest=repair_digest,
        repository_lineage_identity="",
    )
    identity = _domain_hash(
        CORE0A_REPOSITORY_LINEAGE_DOMAIN,
        provisional.identity_material(),
    )
    return replace(
        provisional,
        repository_lineage_identity=identity,
    )


def validate_core0a_repository_lineage_v1(
    *,
    source_root: Path,
) -> ValidatedCore0ARepositoryLineageV1:
    """Rebuild and validate the exact supported lineage rooted at ``source_root``."""

    root = _source_root(source_root)
    _require_clean_worktree(root)
    _require_anchor(root)
    head = _git_text(root, "rev-parse", "HEAD")
    if not _is_ancestor(root, CORE0A_REVIEWED_ANCHOR_COMMIT, head):
        raise Core0ARepositoryLineageV1Error(
            "reviewed CORE-0A anchor is not an ancestor of HEAD"
        )
    return _validated(_classify(root, head))


__all__ = [
    "BlobStateV1",
    "CORE0A_LINEAGE_REPAIR_SCOPE",
    "CORE0A_REPOSITORY_LINEAGE_CONTRACT_VERSION",
    "CORE0A_REPOSITORY_LINEAGE_DOMAIN",
    "CORE0A_REPOSITORY_LINEAGE_SCHEMA",
    "CORE0A_REVIEWED_ANCHOR_COMMIT",
    "CORE0A_REVIEWED_ANCHOR_TREE",
    "CORE0A_STANDALONE_REVIEWED_LINEAGE",
    "Core0ARepositoryLineageV1Error",
    "MASTER_INTEGRATION_MERGE",
    "MASTER_PR_WRAPPER_MERGE",
    "ValidatedCore0ARepositoryLineageV1",
    "validate_core0a_repository_lineage_v1",
]
