from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import inspect
import json
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
DESCENDANT_REGISTRY_PATH = (
    lineage.CORE0A_DESCENDANT_INTEGRATION_REGISTRY_PATH
)


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


def _standalone_source_commit(root: Path) -> str:
    current = _validate(root)
    if current.lineage_mode == lineage.CORE0A_STANDALONE_REVIEWED_LINEAGE:
        candidate = current.current_head_commit
    elif current.lineage_mode == lineage.MASTER_INTEGRATION_MERGE:
        candidate = current.second_parent_commit
    elif current.lineage_mode == lineage.MASTER_PR_WRAPPER_MERGE:
        integration = current.second_parent_commit
        if integration is None:
            raise AssertionError("validated wrapper has no integration parent")
        integration_parents = tuple(_git(
            root, "show", "-s", "--format=%P", integration,
        ).split())
        if len(integration_parents) != 2:
            raise AssertionError("validated wrapper integration is not binary")
        candidate = integration_parents[1]
    else:
        raise AssertionError(
            f"unsupported validated lineage mode: {current.lineage_mode}"
        )

    if candidate is None:
        raise AssertionError("validated lineage has no standalone source")
    _git(root, "cat-file", "-e", f"{candidate}^{{commit}}")
    _tree(root, candidate)
    standalone = lineage._standalone_facts(root, candidate)
    if standalone.lineage_mode != lineage.CORE0A_STANDALONE_REVIEWED_LINEAGE:
        raise AssertionError("resolved source is not reviewed standalone lineage")
    return candidate


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


def _make_descendant_merge(
    root: Path,
    base: str,
    *,
    left_path: str,
    right_path: str,
) -> dict[str, str]:
    _checkout(root, base)
    first = _commit(
        root, {left_path: f"{left_path}\n"}, "descendant first side",
    )
    _checkout(root, base)
    second = _commit(
        root, {right_path: f"{right_path}\n"}, "descendant second side",
    )
    _checkout(root, first)
    _git(root, "merge", "--quiet", "--no-ff", second, "-m", "descendant merge")
    return {
        "base": base,
        "first": first,
        "second": second,
        "merge": _git(root, "rev-parse", "HEAD"),
    }


def _registry_material(
    root: Path,
    anchor: str,
    merges: tuple[str, ...],
) -> dict[str, object]:
    anchor_facts = lineage._integration_anchor_facts(root, anchor)
    effective = lineage._validated(
        anchor_facts
    ).repository_lineage_identity
    contracts = []
    for sequence, merge in enumerate(merges, start=1):
        contract = lineage._reconstruct_descendant_integration_contract(
            root,
            merge,
            sequence=sequence,
            predecessor_effective_lineage_identity=effective,
            predecessor_reviewed_integration_anchor=anchor,
        )
        contracts.append(contract)
        effective = lineage._descendant_effective_lineage_identity(
            effective, contract["contract_content_sha256"],
        )
    material: dict[str, object] = {
        "schema": lineage.CORE0A_DESCENDANT_INTEGRATION_REGISTRY_SCHEMA,
        "contract_version": (
            lineage.CORE0A_DESCENDANT_INTEGRATION_CONTRACT_VERSION
        ),
        "reviewed_integration_anchor": anchor,
        "contracts": contracts,
    }
    material["registry_content_sha256"] = lineage._domain_hash(
        f"{lineage.CORE0A_DESCENDANT_INTEGRATION_DOMAIN}:REGISTRY_CONTENT",
        material,
    )
    return material


def _refresh_registry_hashes(material: dict[str, object]) -> None:
    contracts = material["contracts"]
    assert isinstance(contracts, list)
    for contract in contracts:
        assert isinstance(contract, dict)
        contract.pop("contract_content_sha256", None)
        contract["contract_content_sha256"] = (
            lineage._descendant_contract_content_sha256(contract)
        )
    material.pop("registry_content_sha256", None)
    material["registry_content_sha256"] = lineage._domain_hash(
        f"{lineage.CORE0A_DESCENDANT_INTEGRATION_DOMAIN}:REGISTRY_CONTENT",
        material,
    )


def _commit_registry(
    root: Path,
    material: dict[str, object],
    message: str = "register reviewed descendant integrations",
) -> str:
    payload = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    return _commit(root, {DESCENDANT_REGISTRY_PATH: payload}, message)


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


def test_standalone_source_resolution_is_topology_aware(
    repository: Path,
) -> None:
    assert _standalone_source_commit(repository) == ANCHOR

    data = _make_integration(repository)
    assert tuple(_git(
        repository, "show", "-s", "--format=%P", data["integration"],
    ).split()) == (data["master"], data["core"])
    assert _standalone_source_commit(repository) == data["core"]

    wrapper = _wrapper(repository, data)
    assert tuple(_git(
        repository, "show", "-s", "--format=%P", wrapper,
    ).split()) == (data["master"], data["integration"])
    assert _standalone_source_commit(repository) == data["core"]


def test_standalone_source_resolution_rejects_reversed_parents(
    repository: Path,
) -> None:
    data = _make_integration(repository)
    reversed_parents = _commit_tree(
        repository,
        _tree(repository, data["integration"]),
        (data["core"], data["master"]),
        "reversed integration",
    )
    _checkout(repository, reversed_parents)
    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _standalone_source_commit(repository)


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


def test_reviewed_integration_one_linear_descendant_success(
    repository: Path,
) -> None:
    data = _make_integration(repository)
    descendant = _commit(
        repository,
        {"post-integration-business-1.md": "business-v1\n"},
        "ordinary post-integration change",
    )

    result = _validate(repository)

    assert result.lineage_mode == lineage.MASTER_INTEGRATION_MERGE
    assert result.current_head_commit == descendant
    assert result.current_head_tree == _tree(repository, descendant)
    assert result.current_parent_count == 1
    assert result.first_parent_commit == data["master"]
    assert result.second_parent_commit == data["core"]


def test_reviewed_wrapper_multiple_linear_descendants_success(
    repository: Path,
) -> None:
    data = _make_integration(repository)
    wrapper = _wrapper(repository, data)
    descendants = []
    for index in range(1, 4):
        descendants.append(_commit(
            repository,
            {f"post-wrapper-business-{index}.md": f"business-v{index}\n"},
            f"ordinary post-wrapper change {index}",
        ))

    result = _validate(repository)

    assert result.lineage_mode == lineage.MASTER_PR_WRAPPER_MERGE
    assert result.current_head_commit == descendants[-1]
    assert result.current_head_tree == _tree(repository, descendants[-1])
    assert result.current_parent_count == 1
    assert result.first_parent_commit == data["master"]
    assert result.second_parent_commit == data["integration"]
    assert wrapper not in descendants


def test_one_exact_registered_descendant_merge_success(
    repository: Path,
) -> None:
    data = _make_integration(repository)
    descendant = _make_descendant_merge(
        repository,
        data["integration"],
        left_path="registered-one-left.md",
        right_path="registered-one-right.md",
    )
    material = _registry_material(
        repository, data["integration"], (descendant["merge"],),
    )
    head = _commit_registry(repository, material)

    result = _validate(repository)

    assert result.current_head_commit == head
    assert result.lineage_mode == lineage.MASTER_INTEGRATION_MERGE
    assert result.descendant_integration_contract_type == (
        lineage.REVIEWED_DESCENDANT_INTEGRATION_MERGE_V1
    )
    assert result.descendant_integration_registry_sha256 == material[
        "registry_content_sha256"
    ]
    assert result.descendant_integration_contract_sha256s == (
        material["contracts"][0]["contract_content_sha256"],
    )
    assert len(result.descendant_effective_lineage_identity or "") == 64
    assert result.identity_material()[
        "descendant_integration_contract_count"
    ] == 1


def test_two_ordered_registered_descendant_merges_success(
    repository: Path,
) -> None:
    data = _make_integration(repository)
    first = _make_descendant_merge(
        repository,
        data["integration"],
        left_path="registered-first-left.md",
        right_path="registered-first-right.md",
    )
    second = _make_descendant_merge(
        repository,
        first["merge"],
        left_path="registered-second-left.md",
        right_path="registered-second-right.md",
    )
    material = _registry_material(
        repository,
        data["integration"],
        (first["merge"], second["merge"]),
    )
    head = _commit_registry(repository, material)

    result = _validate(repository)

    assert result.current_head_commit == head
    assert result.descendant_integration_contract_sha256s == tuple(
        contract["contract_content_sha256"]
        for contract in material["contracts"]
    )
    assert result.identity_material()[
        "descendant_integration_contract_count"
    ] == 2


def test_current_two_merge_registry_reconstructs_exact_topology() -> None:
    result = _validate(ROOT)
    registry = lineage._load_descendant_integration_registry(ROOT)

    assert tuple(
        contract["merge_commit"] for contract in registry.contracts
    ) == (
        "8ea8f209f274bd329e41cb0b1ab59265983b3631",
        "95b9045612cfa908aaecea6ae3440d2bd9a0d6ec",
    )
    assert tuple(
        (
            contract["first_parent"],
            contract["second_parent"],
            contract["merge_result_tree"],
        )
        for contract in registry.contracts
    ) == (
        (
            "4a04e2afd88424b8ebe85500b0561d7203c64e4e",
            "d0a37d67f913c44252791316d1140034f04cf285",
            "e728475571031724606f8729204b6055034307a2",
        ),
        (
            "8ea8f209f274bd329e41cb0b1ab59265983b3631",
            "af8a092121087e25dc080de82e6f9194a0d1e0a6",
            "cdbf5122396a7226f1dbde981b80b5016649d7d2",
        ),
    )
    assert tuple(
        (
            contract["first_parent_side"]["changed_path_count"],
            contract["second_parent_side"]["changed_path_count"],
            contract["overlap_count"],
        )
        for contract in registry.contracts
    ) == ((0, 2, 0), (2, 52, 0))
    assert registry.contracts[1]["second_parent_side"][
        "classification_counts"
    ] == {
        "ADD": 42,
        "COPY": 1,
        "DELETE": 0,
        "MODIFY": 9,
        "RENAME": 0,
    }
    assert result.descendant_integration_contract_sha256s == tuple(
        contract["contract_content_sha256"]
        for contract in registry.contracts
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "merge-commit",
        "first-parent",
        "second-parent",
        "parent-order",
        "merge-base",
        "merge-tree",
        "changed-path",
        "overlap",
        "classification",
        "result-blob-state",
        "predecessor-identity",
    ),
)
def test_registered_descendant_merge_reconstruction_tampering_rejected(
    repository: Path,
    mutation: str,
) -> None:
    data = _make_integration(repository)
    descendant = _make_descendant_merge(
        repository,
        data["integration"],
        left_path="tamper-left.md",
        right_path="tamper-right.md",
    )
    material = _registry_material(
        repository, data["integration"], (descendant["merge"],),
    )
    changed = deepcopy(material)
    contract = changed["contracts"][0]
    if mutation == "merge-commit":
        contract["merge_commit"] = "0" * 40
    elif mutation == "first-parent":
        contract["first_parent"] = "1" * 40
    elif mutation == "second-parent":
        contract["second_parent"] = "2" * 40
    elif mutation == "parent-order":
        contract["first_parent"], contract["second_parent"] = (
            contract["second_parent"], contract["first_parent"],
        )
    elif mutation == "merge-base":
        contract["merge_base"] = "3" * 40
    elif mutation == "merge-tree":
        contract["merge_result_tree"] = "4" * 40
    elif mutation == "changed-path":
        contract["first_parent_side"]["changed_path_set_sha256"] = "5" * 64
    elif mutation == "overlap":
        contract["overlap_count"] = 1
    elif mutation == "classification":
        counts = contract["second_parent_side"]["classification_counts"]
        counts["COPY"] += 1
    elif mutation == "result-blob-state":
        contract["result_touched_blob_state_sha256"] = "6" * 64
    elif mutation == "predecessor-identity":
        contract["predecessor_effective_lineage_identity"] = "7" * 64
    else:
        raise AssertionError(mutation)
    _refresh_registry_hashes(changed)
    _commit_registry(repository, changed, f"tampered registry {mutation}")

    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(repository)


def test_registry_missing_first_merge_and_reversed_order_rejected(
    repository: Path,
) -> None:
    data = _make_integration(repository)
    first = _make_descendant_merge(
        repository,
        data["integration"],
        left_path="missing-first-left.md",
        right_path="missing-first-right.md",
    )
    second = _make_descendant_merge(
        repository,
        first["merge"],
        left_path="missing-second-left.md",
        right_path="missing-second-right.md",
    )
    original = _registry_material(
        repository,
        data["integration"],
        (first["merge"], second["merge"]),
    )
    for mode in ("missing-first", "reversed"):
        _checkout(repository, second["merge"])
        changed = deepcopy(original)
        contracts = changed["contracts"]
        if mode == "missing-first":
            changed["contracts"] = [contracts[1]]
        else:
            changed["contracts"] = list(reversed(contracts))
        for sequence, contract in enumerate(changed["contracts"], start=1):
            contract["sequence"] = sequence
        _refresh_registry_hashes(changed)
        _commit_registry(repository, changed, f"invalid registry {mode}")
        with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
            _validate(repository)


def test_unknown_merge_inserted_between_registered_chain_rejected(
    repository: Path,
) -> None:
    data = _make_integration(repository)
    registered = _make_descendant_merge(
        repository,
        data["integration"],
        left_path="before-unknown-left.md",
        right_path="before-unknown-right.md",
    )
    material = _registry_material(
        repository, data["integration"], (registered["merge"],),
    )
    unknown = _make_descendant_merge(
        repository,
        registered["merge"],
        left_path="unknown-left.md",
        right_path="unknown-right.md",
    )
    _commit_registry(repository, material, "registry skips unknown merge")

    assert unknown["merge"] != registered["merge"]
    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(repository)


def test_noncanonical_registry_bytes_rejected(repository: Path) -> None:
    data = _make_integration(repository)
    descendant = _make_descendant_merge(
        repository,
        data["integration"],
        left_path="canonical-left.md",
        right_path="canonical-right.md",
    )
    material = _registry_material(
        repository, data["integration"], (descendant["merge"],),
    )
    payload = json.dumps(material, sort_keys=True, indent=2) + "\n"
    _commit(
        repository,
        {DESCENDANT_REGISTRY_PATH: payload},
        "noncanonical descendant registry bytes",
    )

    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="bytes are not canonical",
    ):
        _validate(repository)


@pytest.mark.parametrize(
    "field",
    ("contract_content_sha256", "registry_content_sha256"),
)
def test_descendant_contract_or_registry_identity_tampering_rejected(
    repository: Path,
    field: str,
) -> None:
    data = _make_integration(repository)
    descendant = _make_descendant_merge(
        repository,
        data["integration"],
        left_path="identity-left.md",
        right_path="identity-right.md",
    )
    material = _registry_material(
        repository, data["integration"], (descendant["merge"],),
    )
    if field == "contract_content_sha256":
        material["contracts"][0][field] = "8" * 64
    else:
        material[field] = "9" * 64
    _commit_registry(repository, material, f"tampered {field}")

    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(repository)


def test_registered_descendant_with_incomplete_git_metadata_rejected(
    repository: Path,
) -> None:
    data = _make_integration(repository)
    descendant = _make_descendant_merge(
        repository,
        data["integration"],
        left_path="metadata-left.md",
        right_path="metadata-right.md",
    )
    material = _registry_material(
        repository, data["integration"], (descendant["merge"],),
    )
    _commit_registry(repository, material)
    missing = descendant["second"]
    object_path = Path(_git(
        repository,
        "rev-parse",
        "--git-path",
        f"objects/{missing[:2]}/{missing[2:]}",
    ))
    if not object_path.is_absolute():
        object_path = repository / object_path
    assert object_path.is_file()
    object_path.unlink()
    assert not object_path.exists()

    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(repository)


def test_post_integration_unknown_merge_rejected(repository: Path) -> None:
    data = _make_integration(repository)
    left = _commit(
        repository,
        {"post-integration-left.md": "left\n"},
        "post-integration left",
    )
    _checkout(repository, data["integration"])
    right = _commit(
        repository,
        {"post-integration-right.md": "right\n"},
        "post-integration right",
    )
    _checkout(repository, left)
    _git(
        repository,
        "merge",
        "--quiet",
        "--no-ff",
        right,
        "-m",
        "unsupported post-integration merge",
    )

    with pytest.raises(lineage.Core0ARepositoryLineageV1Error):
        _validate(repository)


def test_post_integration_octopus_merge_rejected(repository: Path) -> None:
    data = _make_integration(repository)
    first = _commit_tree(
        repository,
        _tree(repository, data["integration"]),
        (data["integration"],),
        "first post-integration parent",
    )
    second = _commit_tree(
        repository,
        _tree(repository, data["integration"]),
        (data["integration"],),
        "second post-integration parent",
    )
    octopus = _commit_tree(
        repository,
        _tree(repository, data["integration"]),
        (data["integration"], first, second),
        "unsupported post-integration octopus",
    )
    _checkout(repository, octopus)

    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="octopus",
    ):
        _validate(repository)


def test_post_integration_rename_rejected(repository: Path) -> None:
    _make_integration(repository)
    _git(repository, "mv", "README.md", "README.post-integration.md")
    _git(repository, "commit", "--quiet", "-m", "unsupported rename")

    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="rename status",
    ):
        _validate(repository)


def test_post_integration_override_is_bound_to_current_identity(
    repository: Path,
) -> None:
    data = _make_integration(repository)
    anchor = _validate(repository)
    descendant = _commit(
        repository,
        {REPAIR_PATH: "post-integration-reviewed-path-v2\n"},
        "ordinary reviewed-path evolution",
    )

    result = _validate(repository)

    assert result.current_head_commit == descendant
    assert result.first_parent_commit == data["master"]
    assert result.second_parent_commit == data["core"]
    assert result.master_changed_paths == anchor.master_changed_paths
    assert result.core0a_changed_paths == anchor.core0a_changed_paths
    assert result.current_head_tree != anchor.current_head_tree
    assert (
        result.repository_lineage_identity
        != anchor.repository_lineage_identity
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
    repaired_head = _standalone_source_commit(ROOT)
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
    assert _standalone_source_commit(root) == repaired_head

    _checkout(root, integration)
    advanced_master = _commit_tree(
        root, _tree(root, master), (master,), "wrapper base drift",
    )
    _wrapper(root, data, first_parent=advanced_master)
    with pytest.raises(
        lineage.Core0ARepositoryLineageV1Error,
        match="base drift",
    ):
        _standalone_source_commit(root)
