import ast
import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_b4_modules_do_not_import_ext1b_specific_modules():
    for path in (PROJECT_ROOT / "experiments/v9_3").glob("performance_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any("ext1b" in name for name in imports), (path, imports)


def test_b4_does_not_modify_ext1b_contract_files():
    changed = __import__("subprocess").run(
        ["git", "diff", "--name-only", "b391ee6ad2854834ab1248ef713499350f195b9e"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert not any(
        name.startswith(("experiments/v9_3/ext1b_", "configs/v9_3_ext1b", "scripts/run_v9_3_ext1b"))
        for name in changed
    )
