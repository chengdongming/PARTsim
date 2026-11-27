"""Resolve relocated repository templates without changing historical logical IDs."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIRECTORY = PROJECT_ROOT / "configs" / "templates"
LEGACY_TEMPLATE_NAMES = frozenset({
    "system_config_unified_template.yml",
    "v9_3_b4_priority_energy_system_template.yml",
})


def resolve_system_template(value, project_root=PROJECT_ROOT):
    root = Path(project_root)
    path = Path(value)
    # Historic configs record these root-relative names in their identity
    # material. Keep the logical strings; only resolve the physical location.
    if not path.is_absolute():
        if str(path) in LEGACY_TEMPLATE_NAMES:
            return root / "configs" / "templates" / path
        return root / path
    for name in LEGACY_TEMPLATE_NAMES:
        if path == root / name:
            return root / "configs" / "templates" / name
    return path
