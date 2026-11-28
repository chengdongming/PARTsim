"""Resolve repository data and relocated templates without changing logical IDs."""
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
            return root / "configs" / "templates" / (
                "priority_energy_system_template.yml" if path.name ==
                "v9_3_b4_priority_energy_system_template.yml" else path.name
            )
        return root / path
    for name in LEGACY_TEMPLATE_NAMES:
        if path == root / name:
            return root / "configs" / "templates" / (
                "priority_energy_system_template.yml" if name ==
                "v9_3_b4_priority_energy_system_template.yml" else name
            )
    return path

# Both current and historical bundled traces are repository-root-relative
# in the system templates. Other relative paths remain config-relative.
BUNDLED_SOLAR_PATHS = frozenset({
    "data/processed/shenyang_solar_minute.csv",
    "data/processed/srl_edinburg_solar_minute.csv",
})


def resolve_solar_data_path(value, system_path, project_root=PROJECT_ROOT):
    path = Path(value)
    if path.is_absolute():
        return path
    local = Path(system_path).resolve().parent / path
    if path.as_posix() in BUNDLED_SOLAR_PATHS and not local.is_file():
        return Path(project_root).resolve() / path
    return local
