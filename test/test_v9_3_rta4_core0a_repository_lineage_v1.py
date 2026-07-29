from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import inspect
import os
from pathlib import Path
import subprocess

import pytest

from experiments.v9_3 import rta4_core0a_repository_lineage_v1 as lineage


ROOT = Path(__file__).resolve().parents[1]
ANCHOR = lineage.CORE0A_REVIEWED_ANCHOR_COMMIT
ANCHOR_PARENT = f"{ANCHOR}^"
REPAIR_PATH = "test/test_v9_3_rta4_core0a_repository_lineage_v1.py"
SECOND_REPAIR_PATH = "test/test_v9_3_rta4_core0a_pilot_freeze_v2.py"
MASTER_PATH = "lineage-master-only.md"


def _git(
    root: Path,
    *arguments: str,
    input_text: str | None = None,
    check: bool = True,
) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=False,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "LC_ALL": "C",
            "LANG": "C",
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
        },
    )
    if check and completed.returncode:
        raise AssertionError(
            f"git {' '.join(arguments)} failed: {completed.stderr}"
        )
    return completed.stdout.strip()


def _configure(root: Path) -> None:
    _git(root, "config", "user.name", "CORE-0A lineage test")
    _git(root, "config", "user.email", "core0a-lineage@example.invalid")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    subprocess.run(
        (
            "git",
            "clone",
            "--quiet",
            "--shared",
            "--no-checkout",
            str(ROOT),
            str(root),
        ),
        check=True,
    )
    _configure(root)
    _git(root, "checkout", "--quiet", "--detach", ANCHOR)
    return root


def _write(root: Path, relative: str, payload: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _commit(
    root: Path,
    changes: dict[str, str | None],
    message: str,
    *,
    executable: tuple[str, ...] = (),
) -> str:
    for relative, payload in changes.items():
        path = root / relative
        if payload is None:
            path.unlink()
        else:
            _write(root, relative, payload)
    for relative in executable:
        (root / relative).chmod(0o755)
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _commit_tree(
    root: Path,
    tree: str,
    parents: tuple[str, ...],
    message: str = "synthetic lineage commit",
) -> str:
    arguments = ["commit-tree", tree]
    for parent in parents:
        arguments.extend(("-p", parent))
    return _git(root, *arguments, input_text=f"{message}\n")


def _checkout(root: Path, commit: str) -> None:
    _git(root, "checkout", "--quiet", "--detach", commit)


def _tree(root: Path, commit: str) -> str:
    return _git(root, "rev-parse", f"{commit}^{{tree}}")


def _validate(root: Path) -> lineage.ValidatedCore0ARepositoryLineageV1:
    return lineage.validate_core0a_repository_lineage_v1(
        source_root=root,
    )


def _make_integration(
    root: Path,
    *,
    master_changes: dict[str, str | None] | None = None,
    core_changes: dict[str, str | None] | None = None,
) -> dict[str, str]:
    _checkout(root, ANCHOR_PARENT)
    master = _commit(
        root,
        master_changes or {MASTER_PATH: "master-only-v1\n"},
        "master-side evolution",
    )
    _checkout(root, ANCHOR)
    core = _commit(
        root,
        core_changes or {REPAIR_PATH: "repair-only-v1\n"},
        "CORE-0A lineage repair",
    )
    _checkout(root, master)
    _git(root, "merge", "--quiet", "--no-ff", core, "-m", "integration")
    return {
        "base": _git(root, "merge-base", master, core),
        "master": master,
        "core": core,
        "integration": _git(root, "rev-parse", "HEAD"),
    }


def _amend_merge(
    root: Path,
    changes: dict[str, str | None],
) -> str:
    _commit(root, changes, "contaminated integration")
    # _commit creates a child. Rebuild that tree with the original merge parents.
    child = _git(root, "rev-parse", "HEAD")
    merge = _git(root, "rev-parse", "HEAD^")
    parents = tuple(_git(
        root, "show", "-s", "--format=%P", merge,
    ).split())
    contaminated = _commit_tree(
        root, _tree(root, child), parents, "contaminated integration",
    )
    _checkout(root, contaminated)
    return contaminated


def _wrapper(
    root: Path,
    data: dict[str, str],
    *,
    first_parent: str | None = None,
    second_parent: str | None = None,
    tree: str | None = None,
) -> str:
    wrapper = _commit_tree(
        root,
        tree or _tree(root, data["integration"]),
        (
            first_parent or data["master"],
            second_parent or data["integration"],
        ),
        "PR wrapper",
    )
    _checkout(root, wrapper)
    return wrapper


def test_public_interface_is_source_root_only_and_result_is_frozen(
    repository: Path,
) -> None:
    signature = inspect.signature(
        lineage.validate_core0a_repository_lineage_v1
    )
    assert tuple(signature.parameters) == ("source_root",)
    result = _validate(repository)
    with pytest.raises(FrozenInstanceError):
        result.current_head_commit = "0" * 40
    assert result.lineage_mode == lineage.CORE0A_STANDALONE_REVIEWED_LINEAGE
    assert result.current_head_commit == ANCHOR
    assert result.current_head_tree == lineage.CORE0A_REVIEWED_ANCHOR_TREE
    assert result.current_parent_count == 1
    assert result.first_parent_commit is None
    assert result.second_parent_commit is None
    assert result.merge_base_commit is None
    assert result.worktree_clean is True


def test_standalone_linear_repair_only_success(repository: Path) -> None:
    first = _commit(
        repository, {REPAIR_PATH: "repair-v1\n"}, "repair implementation",
    )
    first_result = _validate(repository)
    second = _commit(
        repository,
        {SECOND_REPAIR_PATH: "repair-test-v1\n"},
        "repair tests",
    )
    result = _validate(repository)
    assert result.current_head_commit == second
    assert result.core0a_changed_paths == tuple(sorted((
        REPAIR_PATH,
        SECOND_REPAIR_PATH,
    )))
    assert first_result.current_head_commit == first
    assert (
        result.repository_lineage_identity
        != first_result.repository_lineage_identity
    )


def test_standalone_nonrepair_path_rejected(repository: Path) -> None:
    _commit(
        repository,
        {"configs/forbidden-lineage-change.yaml": "forbidden\n"},
        "forbidden repair",
    )
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="non-repair",
    ):
        _validate(repository)


@pytest.mark.parametrize("dirty_kind", ("tracked", "untracked"))
def test_dirty_worktree_rejected(
    repository: Path, dirty_kind: str,
) -> None:
    if dirty_kind == "tracked":
        _write(repository, "README.md", "dirty tracked\n")
    else:
        _write(repository, "untracked-lineage-probe.md", "dirty untracked\n")
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="clean tracked and untracked",
    ):
        _validate(repository)


def test_unfinished_git_operation_rejected(repository: Path) -> None:
    marker = Path(_git(repository, "rev-parse", "--git-path", "MERGE_HEAD"))
    if not marker.is_absolute():
        marker = repository / marker
    marker.write_text(ANCHOR + "\n", encoding="ascii")
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="unfinished Git operation",
    ):
        _validate(repository)


def test_anchor_tree_mismatch_rejected(
    repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        lineage, "CORE0A_REVIEWED_ANCHOR_TREE", "0" * 40,
    )
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="anchor tree mismatch",
    ):
        _validate(repository)


def test_absent_anchor_rejected(tmp_path: Path) -> None:
    root = tmp_path / "unrelated"
    _git(tmp_path, "init", "--quiet", str(root))
    _configure(root)
    _write(root, "unrelated.txt", "unrelated\n")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "unrelated root")
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="Git command failed",
    ):
        _validate(root)


def test_standalone_merge_cannot_bypass_master_side_role(
    repository: Path,
) -> None:
    _checkout(repository, ANCHOR)
    left = _commit(
        repository, {REPAIR_PATH: "left\n"}, "left repair",
    )
    _checkout(repository, ANCHOR)
    right = _commit(
        repository, {SECOND_REPAIR_PATH: "right\n"}, "right repair",
    )
    _checkout(repository, left)
    _git(repository, "merge", "--quiet", "--no-ff", right, "-m", "repair merge")
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="independent master side",
    ):
        _validate(repository)


def test_exact_disjoint_two_parent_integration_success(
    repository: Path,
) -> None:
    data = _make_integration(repository)
    result = _validate(repository)
    assert result.lineage_mode == lineage.MASTER_INTEGRATION_MERGE
    assert result.current_parent_count == 2
    assert result.first_parent_commit == data["master"]
    assert result.second_parent_commit == data["core"]
    assert result.merge_base_commit == data["base"]
    assert result.master_changed_paths == (MASTER_PATH,)
    assert REPAIR_PATH in result.core0a_changed_paths
    assert result.overlap_paths == ()
    assert result.master_only_blob_states[0].state == "REGULAR_BLOB"
    assert result.final_integration_tree == _tree(
        repository, data["integration"],
    )
    assert all(
        len(value) == 64
        for value in (
            result.master_path_set_digest,
            result.core0a_path_set_digest,
            result.overlap_path_set_digest,
            result.master_only_blob_state_digest,
            result.core0a_only_blob_state_digest,
            result.repair_scope_path_digest,
            result.repository_lineage_identity,
        )
    )


def test_parent_order_reversal_rejected(repository: Path) -> None:
    data = _make_integration(repository)
    reversed_merge = _commit_tree(
        repository,
        _tree(repository, data["integration"]),
        (data["core"], data["master"]),
    )
    _checkout(repository, reversed_merge)
    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(repository)


def test_second_parent_without_anchor_rejected(repository: Path) -> None:
    _checkout(repository, ANCHOR)
    anchored = _commit(
        repository, {REPAIR_PATH: "anchored\n"}, "anchored side",
    )
    empty_tree = _git(repository, "mktree", input_text="")
    unrelated = _commit_tree(repository, empty_tree, (), "unrelated root")
    shaped = _commit_tree(
        repository, _tree(repository, anchored), (anchored, unrelated),
    )
    _checkout(repository, shaped)
    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(repository)


def test_octopus_merge_rejected(repository: Path) -> None:
    data = _make_integration(repository)
    third = _commit_tree(
        repository, _tree(repository, data["base"]), (data["base"],),
    )
    octopus = _commit_tree(
        repository,
        _tree(repository, data["integration"]),
        (data["master"], data["core"], third),
    )
    _checkout(repository, octopus)
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="octopus",
    ):
        _validate(repository)


def test_overlap_rejected(repository: Path) -> None:
    _checkout(repository, ANCHOR_PARENT)
    master = _commit(
        repository, {REPAIR_PATH: "master overlap\n"}, "master overlap",
    )
    _checkout(repository, ANCHOR)
    core = _commit(
        repository, {REPAIR_PATH: "CORE-0A overlap\n"}, "CORE-0A overlap",
    )
    merge = _commit_tree(
        repository, _tree(repository, master), (master, core),
    )
    _checkout(repository, merge)
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="overlap",
    ):
        _validate(repository)


def test_master_side_rename_rejected(repository: Path) -> None:
    _checkout(repository, ANCHOR_PARENT)
    _git(repository, "mv", "LICENSE", "LICENSE.master-renamed")
    _git(repository, "commit", "--quiet", "-m", "master rename")
    master = _git(repository, "rev-parse", "HEAD")
    _checkout(repository, ANCHOR)
    core = _commit(
        repository, {REPAIR_PATH: "repair\n"}, "CORE-0A repair",
    )
    merge = _commit_tree(
        repository, _tree(repository, core), (master, core),
    )
    _checkout(repository, merge)
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="rename/copy",
    ):
        _validate(repository)


def test_master_side_copy_rejected(repository: Path) -> None:
    _checkout(repository, ANCHOR_PARENT)
    (repository / "README.master-copy.md").write_bytes(
        (repository / "README.md").read_bytes()
    )
    _git(repository, "add", "--all")
    _git(repository, "commit", "--quiet", "-m", "master copy")
    master = _git(repository, "rev-parse", "HEAD")
    _checkout(repository, ANCHOR)
    core = _commit(
        repository, {REPAIR_PATH: "repair\n"}, "CORE-0A repair",
    )
    merge = _commit_tree(
        repository, _tree(repository, core), (master, core),
    )
    _checkout(repository, merge)
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="rename/copy",
    ):
        _validate(repository)


@pytest.mark.parametrize(
    "changes",
    (
        {MASTER_PATH: "polluted master blob\n"},
        {REPAIR_PATH: "polluted CORE-0A blob\n"},
        {"third-class-lineage-path.md": "third class\n"},
        {MASTER_PATH: None},
        {REPAIR_PATH: None},
    ),
    ids=(
        "master-blob",
        "core0a-blob",
        "third-path",
        "master-delete",
        "core0a-delete",
    ),
)
def test_integration_tree_contamination_rejected(
    repository: Path,
    changes: dict[str, str | None],
) -> None:
    _make_integration(repository)
    _amend_merge(repository, changes)
    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(repository)


def test_changed_path_set_mismatch_rejected(repository: Path) -> None:
    data = _make_integration(repository)
    missing_core = _commit_tree(
        repository,
        _tree(repository, data["master"]),
        (data["master"], data["core"]),
    )
    _checkout(repository, missing_core)
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="path set",
    ):
        _validate(repository)


def test_unreachable_merge_base_rejected(repository: Path) -> None:
    _checkout(repository, ANCHOR)
    core = _commit(
        repository, {REPAIR_PATH: "repair\n"}, "CORE-0A repair",
    )
    empty_tree = _git(repository, "mktree", input_text="")
    unrelated = _commit_tree(repository, empty_tree, (), "unrelated master")
    shaped = _commit_tree(
        repository, _tree(repository, core), (unrelated, core),
    )
    _checkout(repository, shaped)
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="merge base",
    ):
        _validate(repository)


def test_caller_rehash_cannot_override_git_reconstruction(
    repository: Path,
) -> None:
    _make_integration(repository)
    forged = hashlib.sha256(b"forged synchronized path material").hexdigest()
    assert len(forged) == 64
    _amend_merge(repository, {MASTER_PATH: "polluted after rehash\n"})
    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(repository)


def test_blob_state_material_distinguishes_all_supported_modes(
    repository: Path,
) -> None:
    _checkout(repository, ANCHOR_PARENT)
    _write(repository, "regular-state.md", "regular\n")
    _write(repository, "executable-state.sh", "#!/bin/sh\nexit 0\n")
    (repository / "executable-state.sh").chmod(0o755)
    (repository / "symlink-state").symlink_to("regular-state.md")
    (repository / "README.md").unlink()
    _git(repository, "add", "--all")
    _git(
        repository,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{ANCHOR},gitlink-state",
    )
    _git(repository, "commit", "--quiet", "-m", "master blob states")
    master = _git(repository, "rev-parse", "HEAD")
    _checkout(repository, ANCHOR)
    core = _commit(
        repository, {REPAIR_PATH: "repair\n"}, "CORE-0A repair",
    )
    _checkout(repository, master)
    _git(repository, "merge", "--quiet", "--no-ff", core, "-m", "integration")
    result = _validate(repository)
    states = {
        row.path: row.state for row in result.master_only_blob_states
    }
    assert states["regular-state.md"] == "REGULAR_BLOB"
    assert states["executable-state.sh"] == "EXECUTABLE_BLOB"
    assert states["symlink-state"] == "SYMLINK_BLOB"
    assert states["README.md"] == "DELETED"
    assert states["gitlink-state"] == "GITLINK"


def test_exact_pr_wrapper_success(repository: Path) -> None:
    data = _make_integration(repository)
    wrapper = _wrapper(repository, data)
    result = _validate(repository)
    assert result.lineage_mode == lineage.MASTER_PR_WRAPPER_MERGE
    assert result.current_head_commit == wrapper
    assert result.first_parent_commit == data["master"]
    assert result.second_parent_commit == data["integration"]
    assert result.current_head_tree == _tree(
        repository, data["integration"],
    )


def test_pr_wrapper_first_parent_drift_rejected(repository: Path) -> None:
    data = _make_integration(repository)
    drifted_master = _commit_tree(
        repository,
        _tree(repository, data["master"]),
        (data["master"],),
        "advanced master",
    )
    _wrapper(repository, data, first_parent=drifted_master)
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="base drift",
    ):
        _validate(repository)


def test_pr_wrapper_tree_or_extra_content_rejected(repository: Path) -> None:
    data = _make_integration(repository)
    _write(repository, "wrapper-extra.md", "extra\n")
    _git(repository, "add", "--all")
    polluted_tree = _git(repository, "write-tree")
    _wrapper(repository, data, tree=polluted_tree)
    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(repository)


def test_pr_wrapper_invalid_second_parent_rejected(repository: Path) -> None:
    data = _make_integration(repository)
    _amend_merge(repository, {MASTER_PATH: "bad integration\n"})
    bad_integration = _git(repository, "rev-parse", "HEAD")
    bad_data = {**data, "integration": bad_integration}
    _wrapper(repository, bad_data)
    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(repository)


@pytest.mark.parametrize("shape", ("squash", "rebase"))
def test_linear_shapes_cannot_impersonate_wrapper(
    repository: Path, shape: str,
) -> None:
    data = _make_integration(repository)
    if shape == "squash":
        shaped = _commit_tree(
            repository,
            _tree(repository, data["integration"]),
            (data["master"],),
            "squash shaped",
        )
    else:
        _checkout(repository, data["master"])
        shaped = _commit(
            repository, {REPAIR_PATH: "rebased repair\n"}, "rebased repair",
        )
    _checkout(repository, shaped)
    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(repository)


def test_lineage_identity_changes_for_every_parent_side_and_tree(
    repository: Path,
) -> None:
    data = _make_integration(repository)
    original = _validate(repository)

    master_same_tree = _commit_tree(
        repository,
        _tree(repository, data["master"]),
        (data["master"],),
        "master identity-only advance",
    )
    master_parent_changed = _commit_tree(
        repository,
        _tree(repository, data["integration"]),
        (master_same_tree, data["core"]),
        "integration with changed master parent",
    )
    _checkout(repository, master_parent_changed)
    changed_parent = _validate(repository)

    core_same_tree = _commit_tree(
        repository,
        _tree(repository, data["core"]),
        (data["core"],),
        "CORE-0A identity-only advance",
    )
    core_parent_changed = _commit_tree(
        repository,
        _tree(repository, data["integration"]),
        (data["master"], core_same_tree),
        "integration with changed CORE-0A parent",
    )
    _checkout(repository, core_parent_changed)
    changed_core = _validate(repository)

    _checkout(repository, data["base"])
    master_blob = _commit(
        repository, {MASTER_PATH: "master-only-v2\n"}, "changed master blob",
    )
    changed_tree_merge = _commit_tree(
        repository,
        _tree(repository, master_blob),
        (master_blob, data["core"]),
        "placeholder integration",
    )
    _checkout(repository, master_blob)
    _git(
        repository,
        "merge",
        "--quiet",
        "--no-ff",
        data["core"],
        "-m",
        "changed master integration",
    )
    changed_tree_merge = _git(repository, "rev-parse", "HEAD")
    changed_tree = _validate(repository)

    identities = {
        original.repository_lineage_identity,
        changed_parent.repository_lineage_identity,
        changed_core.repository_lineage_identity,
        changed_tree.repository_lineage_identity,
    }
    assert len(identities) == 4
    assert (
        original.master_path_set_digest
        == changed_parent.master_path_set_digest
    )
    assert (
        original.master_only_blob_state_digest
        != changed_tree.master_only_blob_state_digest
    )
    assert original.final_integration_tree != changed_tree.final_integration_tree
    assert changed_tree.current_head_commit == changed_tree_merge


def test_real_failed_topology_probe_and_repaired_source_integration(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real-topology"
    subprocess.run(
        (
            "git",
            "clone",
            "--quiet",
            "--shared",
            "--no-checkout",
            str(ROOT),
            str(root),
        ),
        check=True,
    )
    _configure(root)
    repaired_head = _git(ROOT, "rev-parse", "HEAD")
    master = "04aeba2d8491288ed9af83fca8e79666d2689b93"
    _checkout(root, master)
    _git(
        root,
        "merge",
        "--quiet",
        "--no-ff",
        repaired_head,
        "-m",
        "temporary repaired CORE-0A integration probe",
    )
    integration = _git(root, "rev-parse", "HEAD")
    result = _validate(root)
    assert result.lineage_mode == lineage.MASTER_INTEGRATION_MERGE
    assert len(result.master_changed_paths) == 168
    assert result.overlap_paths == ()
    assert all(
        left == right
        for left, right in zip(
            lineage._blob_states(
                root, master, result.master_changed_paths,
            ),
            lineage._blob_states(
                root, integration, result.master_changed_paths,
            ),
        )
    )
    assert all(
        left == right
        for left, right in zip(
            lineage._blob_states(
                root, repaired_head, result.core0a_changed_paths,
            ),
            lineage._blob_states(
                root, integration, result.core0a_changed_paths,
            ),
        )
    )

    regular_master = next(
        row.path
        for row in result.master_only_blob_states
        if row.state == "REGULAR_BLOB"
    )
    _amend_merge(root, {regular_master: "polluted master probe\n"})
    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(root)

    _checkout(root, integration)
    _amend_merge(root, {"third-class-real-probe.md": "extra\n"})
    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(root)

    _checkout(root, integration)
    data = {
        "master": master,
        "core": repaired_head,
        "integration": integration,
    }
    _wrapper(root, data)
    assert _validate(root).lineage_mode == lineage.MASTER_PR_WRAPPER_MERGE

    _checkout(root, integration)
    advanced_master = _commit_tree(
        root, _tree(root, master), (master,), "wrapper base drift",
    )
    _wrapper(root, data, first_parent=advanced_master)
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="base drift",
    ):
        _validate(root)
