"""Unauthorized CORE-3 V7 paired-calibration campaign materialization."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any, Mapping

import yaml

from .result_writer import atomic_write_json, atomic_write_text
from .rta4_core3_calibration_v6 import (
    CORE3_CALIBRATION_CONFIG_V6,
    RTA4Core3CalibrationV6Error,
    materialize_calibration_campaigns_v6,
)
from .rta4_core3_contracts_v7 import CORE3_SIMULATION_CONTRACT_V7
from .rta4_formal_config import domain_hash
from .rta4_formal_config_v5 import normalize_rta4_campaign_v5
from .rta4_task_source_v4 import _UniqueKeyLoader


CORE3_CALIBRATION_CONFIG_V7 = (
    "ASAP_BLOCK_V9_3_RTA4_CORE3_CALIBRATION_CONFIG_V7"
)
CORE3_CALIBRATION_MANIFEST_DOMAIN_V7 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_CALIBRATION_MANIFEST:v7"
)


class RTA4Core3CalibrationV7Error(ValueError):
    """Raised before a V7 calibration generator can emit campaigns."""


def materialize_calibration_campaigns_v7(
    raw: Any, *, base_directory: Path | str | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise RTA4Core3CalibrationV7Error(
            "calibration config must be a mapping"
        )
    if raw.get("schema_version") != CORE3_CALIBRATION_CONFIG_V7:
        raise RTA4Core3CalibrationV7Error(
            "calibration schema version mismatch"
        )
    base = raw.get("base_campaign")
    if not isinstance(base, Mapping):
        raise RTA4Core3CalibrationV7Error(
            "base_campaign must be a mapping"
        )
    try:
        normalized_base = normalize_rta4_campaign_v5(
            base, base_directory=base_directory,
        )["normalized_scientific_config"]
    except Exception as exc:
        raise RTA4Core3CalibrationV7Error(str(exc)) from exc
    contract = normalized_base.get("core3_simulation_contract")
    if (
        not isinstance(contract, Mapping)
        or contract.get("contract_version") != CORE3_SIMULATION_CONTRACT_V7
    ):
        raise RTA4Core3CalibrationV7Error(
            "calibration base campaign is not CORE-3 V7"
        )
    legacy_input = deepcopy(dict(raw))
    legacy_input["schema_version"] = CORE3_CALIBRATION_CONFIG_V6
    try:
        legacy = materialize_calibration_campaigns_v6(
            legacy_input, base_directory=base_directory,
        )
    except RTA4Core3CalibrationV6Error as exc:
        raise RTA4Core3CalibrationV7Error(str(exc)) from exc
    campaigns = []
    for item in legacy["campaigns"]:
        normalized = normalize_rta4_campaign_v5(
            item["campaign"], base_directory=base_directory,
        )["normalized_scientific_config"]
        campaign_contract = normalized["core3_simulation_contract"]
        campaigns.append({
            **item,
            "core3_simulation_contract_identity": campaign_contract[
                "contract_identity"
            ],
        })
    material = {
        **{
            key: value
            for key, value in legacy.items()
            if key not in {
                "schema_version", "campaigns",
                "calibration_manifest_identity",
            }
        },
        "schema_version": CORE3_CALIBRATION_CONFIG_V7,
        "model_energy_unit_joules": normalized_base[
            "model_energy_unit_joules"
        ],
        "core3_simulation_contract_identity": contract[
            "contract_identity"
        ],
        "campaigns": campaigns,
    }
    return {
        **material,
        "calibration_manifest_identity": domain_hash(
            CORE3_CALIBRATION_MANIFEST_DOMAIN_V7, material,
        ),
    }


def write_calibration_campaigns_v7(
    config_path: Path | str, output_root: Path | str,
) -> Mapping[str, Any]:
    source = Path(config_path).expanduser().resolve(strict=True)
    try:
        raw = yaml.load(source.read_bytes(), Loader=_UniqueKeyLoader)
    except Exception as exc:
        raise RTA4Core3CalibrationV7Error(
            "cannot parse calibration config"
        ) from exc
    manifest = materialize_calibration_campaigns_v7(
        raw, base_directory=source.parent,
    )
    root = Path(output_root).expanduser().resolve(strict=False)
    if root.exists() and any(root.iterdir()):
        raise RTA4Core3CalibrationV7Error(
            "refusing to write into a non-empty calibration output root"
        )
    root.mkdir(parents=True, exist_ok=True)
    campaign_rows = []
    for item in manifest["campaigns"]:
        horizon = item["release_horizon"]
        path = root / f"core3_calibration_hrel_{horizon}_v7.yaml"
        atomic_write_text(
            path, yaml.safe_dump(item["campaign"], sort_keys=False),
        )
        row = {
            key: value for key, value in item.items() if key != "campaign"
        }
        row.update({
            "relative_path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
        campaign_rows.append(row)
    persisted = {
        **{key: value for key, value in manifest.items() if key != "campaigns"},
        "campaigns": campaign_rows,
    }
    atomic_write_json(root / "core3_calibration_manifest_v7.json", persisted)
    return persisted


__all__ = [
    "CORE3_CALIBRATION_CONFIG_V7",
    "CORE3_CALIBRATION_MANIFEST_DOMAIN_V7",
    "RTA4Core3CalibrationV7Error",
    "materialize_calibration_campaigns_v7",
    "write_calibration_campaigns_v7",
]
