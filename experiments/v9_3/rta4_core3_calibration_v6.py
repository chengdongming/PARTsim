"""Independent, paired CORE-3 calibration generation and audit contracts."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import yaml

from .result_writer import atomic_write_json, atomic_write_text
from .rta4_formal_config import domain_hash, fraction_text
from .rta4_formal_config_v5 import normalize_rta4_campaign_v5
from .rta4_task_source_v4 import GENERATED_FAMILY, _UniqueKeyLoader


CORE3_CALIBRATION_CONFIG_V6 = (
    "ASAP_BLOCK_V9_3_RTA4_CORE3_CALIBRATION_CONFIG_V6"
)
CORE3_CALIBRATION_MANIFEST_DOMAIN_V6 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_CALIBRATION_MANIFEST:v6"
)
CORE3_CALIBRATION_SUMMARY_DOMAIN_V6 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_CALIBRATION_SUMMARY:v6"
)
CORE3_CALIBRATION_FREEZE_DOMAIN_V6 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_CALIBRATION_FREEZE:v6"
)
CORE3_CALIBRATION_RELEASE_HORIZONS_V6 = (30000, 60000)


class RTA4Core3CalibrationV6Error(ValueError):
    """Raised when calibration pairing or provenance is incomplete."""


def _sha(value: Any, label: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RTA4Core3CalibrationV6Error(f"{label} must be a SHA-256")
    return value


def _canonical_capacity_axis(value: Any) -> list[str]:
    if type(value) is not list or not value:
        raise RTA4Core3CalibrationV6Error(
            "finite battery candidate capacities must be non-empty"
        )
    result = []
    for index, item in enumerate(value):
        if type(item) is not str:
            raise RTA4Core3CalibrationV6Error(
                f"capacity {index} must be a rational string"
            )
        try:
            exact = Fraction(item)
        except (ValueError, ZeroDivisionError) as exc:
            raise RTA4Core3CalibrationV6Error(
                f"capacity {index} is not rational"
            ) from exc
        if exact <= 0 or fraction_text(exact) != item:
            raise RTA4Core3CalibrationV6Error(
                f"capacity {index} must be canonical and positive"
            )
        result.append(item)
    if len(set(result)) != len(result):
        raise RTA4Core3CalibrationV6Error("capacity candidates are duplicated")
    return result


def materialize_calibration_campaigns_v6(
    raw: Any, *, base_directory: Path | str | None = None,
) -> dict[str, Any]:
    """Build the two paired campaigns without selecting a winning setting."""

    required = {
        "schema_version", "calibration_id", "experiment1_task_source_identity",
        "release_horizons", "finite_battery_candidate_capacities",
        "base_campaign",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise RTA4Core3CalibrationV6Error(
            "calibration config field set mismatch"
        )
    if raw["schema_version"] != CORE3_CALIBRATION_CONFIG_V6:
        raise RTA4Core3CalibrationV6Error("calibration schema version mismatch")
    if (
        type(raw["calibration_id"]) is not str
        or not raw["calibration_id"].strip()
    ):
        raise RTA4Core3CalibrationV6Error("calibration_id is invalid")
    if tuple(raw["release_horizons"]) != CORE3_CALIBRATION_RELEASE_HORIZONS_V6:
        raise RTA4Core3CalibrationV6Error(
            "calibration release horizons must be exactly 30000 and 60000"
        )
    capacities = _canonical_capacity_axis(
        raw["finite_battery_candidate_capacities"]
    )
    experiment1_identity = _sha(
        raw["experiment1_task_source_identity"],
        "experiment1_task_source_identity",
    )
    if not isinstance(raw["base_campaign"], Mapping):
        raise RTA4Core3CalibrationV6Error("base_campaign must be a mapping")
    base = deepcopy(dict(raw["base_campaign"]))
    if base.get("core") != "CORE-3":
        raise RTA4Core3CalibrationV6Error("base_campaign must target CORE-3")
    if base.get("core3_campaign_type") != "CALIBRATION":
        raise RTA4Core3CalibrationV6Error(
            "base_campaign must explicitly be a CALIBRATION campaign"
        )
    if base.get("finite_battery_capacities") != capacities:
        raise RTA4Core3CalibrationV6Error(
            "candidate capacity axis differs from base campaign"
        )
    task_source = base.get("task_source")
    parameters = (
        task_source.get("parameters")
        if isinstance(task_source, Mapping) else None
    )
    if (
        not isinstance(parameters, Mapping)
        or task_source.get("mode") != GENERATED_FAMILY
    ):
        raise RTA4Core3CalibrationV6Error(
            "calibration task source must be a generated family"
        )
    base_seed = parameters.get("base_seed")
    taskset_count = parameters.get("taskset_count")
    generation_indices = parameters.get("generation_indices")
    if (
        type(base_seed) is not int
        or type(taskset_count) is not int
        or taskset_count <= 0
        or type(generation_indices) is not list
        or len(generation_indices) != taskset_count
        or len(set(generation_indices)) != taskset_count
        or any(type(index) is not int or index < 0 for index in generation_indices)
    ):
        raise RTA4Core3CalibrationV6Error(
            "calibration seed/generation index binding is invalid"
        )

    campaigns: list[dict[str, Any]] = []
    task_source_identity: str | None = None
    for horizon in CORE3_CALIBRATION_RELEASE_HORIZONS_V6:
        campaign = deepcopy(base)
        campaign["campaign_id"] = f"{raw['calibration_id']}-hrel-{horizon}"
        campaign["simulation_horizon"] = {
            "release_horizon": horizon,
            "observation_horizon": "release_horizon_plus_dmax",
        }
        normalized = normalize_rta4_campaign_v5(
            campaign, base_directory=base_directory,
        )
        scientific = normalized["normalized_scientific_config"]
        binding = normalized["task_sources"]
        if len(binding) != 1:
            raise RTA4Core3CalibrationV6Error(
                "calibration campaign has no unique task source"
            )
        observed_identity = binding[0].source.identity
        if task_source_identity is None:
            task_source_identity = observed_identity
        elif observed_identity != task_source_identity:
            raise RTA4Core3CalibrationV6Error(
                "paired horizons changed calibration task source"
            )
        campaigns.append({
            "release_horizon": horizon,
            "campaign": campaign,
            "scientific_config_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4_FORMAL_CONFIG:v5", scientific,
            ),
        })
    assert task_source_identity is not None
    if task_source_identity == experiment1_identity:
        raise RTA4Core3CalibrationV6Error(
            "calibration task source is not independent from experiment one"
        )
    material = {
        "schema_version": CORE3_CALIBRATION_CONFIG_V6,
        "calibration_id": raw["calibration_id"],
        "calibration_task_source_identity": task_source_identity,
        "experiment1_task_source_identity": experiment1_identity,
        "base_seed": base_seed,
        "generation_indices": list(generation_indices),
        "taskset_count": taskset_count,
        "release_horizons": list(CORE3_CALIBRATION_RELEASE_HORIZONS_V6),
        "finite_battery_candidate_capacities": capacities,
        "release_modes": list(base["release_modes"]),
        "campaigns": campaigns,
        "automatic_parameter_selection": False,
    }
    return {
        **material,
        "calibration_manifest_identity": domain_hash(
            CORE3_CALIBRATION_MANIFEST_DOMAIN_V6, material,
        ),
    }


def write_calibration_campaigns_v6(
    config_path: Path | str, output_root: Path | str,
) -> Mapping[str, Any]:
    source = Path(config_path).expanduser().resolve(strict=True)
    try:
        raw = yaml.load(source.read_bytes(), Loader=_UniqueKeyLoader)
    except Exception as exc:
        raise RTA4Core3CalibrationV6Error(
            "cannot parse calibration config"
        ) from exc
    manifest = materialize_calibration_campaigns_v6(
        raw, base_directory=source.parent,
    )
    root = Path(output_root).expanduser().resolve(strict=False)
    if root.exists() and any(root.iterdir()):
        raise RTA4Core3CalibrationV6Error(
            "refusing to write into a non-empty calibration output root"
        )
    root.mkdir(parents=True, exist_ok=True)
    campaign_rows = []
    for item in manifest["campaigns"]:
        horizon = item["release_horizon"]
        path = root / f"core3_calibration_hrel_{horizon}_v6.yaml"
        atomic_write_text(
            path,
            yaml.safe_dump(item["campaign"], sort_keys=False),
        )
        campaign_rows.append({
            "release_horizon": horizon,
            "relative_path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "scientific_config_identity": item["scientific_config_identity"],
        })
    persisted = {
        **{key: value for key, value in manifest.items() if key != "campaigns"},
        "campaigns": campaign_rows,
    }
    atomic_write_json(root / "core3_calibration_manifest_v6.json", persisted)
    return persisted


def _completed_rows(core3_root: Path) -> list[tuple[Path, Mapping[str, Any]]]:
    rows = []
    for path in sorted(core3_root.rglob("*.json")):
        if path.name == "simulation_job_observations_v6.json":
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (
            isinstance(value, Mapping)
            and value.get("result_schema_version")
            == "ASAP_BLOCK_V9_3_RTA4_CORE3_SIMULATION_RESULT_V6"
        ):
            enriched = dict(value)
            execution = value.get("result")
            if (
                "runtime_wall_seconds" not in enriched
                and isinstance(execution, Mapping)
                and execution.get("runtime_wall_seconds") is not None
            ):
                enriched["runtime_wall_seconds"] = execution[
                    "runtime_wall_seconds"
                ]
            rows.append((path, enriched))
    if not rows:
        raise RTA4Core3CalibrationV6Error("no CORE-3 V6 terminal rows found")
    return rows


def summarize_calibration_v6(core3_root: Path | str) -> dict[str, Any]:
    """Aggregate complete candidates and report only paired differences."""

    root = Path(core3_root).expanduser().resolve(strict=True)
    rows = _completed_rows(root)
    by_horizon: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    by_capacity: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    pair_rows: dict[tuple[Any, ...], dict[int, Mapping[str, Any]]] = defaultdict(dict)
    for path, row in rows:
        horizon = row.get("release_horizon")
        capacity = row.get("battery_capacity")
        if horizon not in CORE3_CALIBRATION_RELEASE_HORIZONS_V6:
            raise RTA4Core3CalibrationV6Error(
                f"unexpected calibration horizon in {path}"
            )
        if type(capacity) is not str:
            raise RTA4Core3CalibrationV6Error(
                f"missing calibration capacity in {path}"
            )
        key = (
            row.get("taskset_identity"), row.get("track"),
            row.get("release_mode"), capacity,
        )
        if horizon in pair_rows[key]:
            raise RTA4Core3CalibrationV6Error("duplicate paired calibration row")
        pair_rows[key][horizon] = row
        by_horizon[horizon].append(row)
        by_capacity[capacity].append(row)
    incomplete = [key for key, pair in pair_rows.items() if set(pair) != {30000, 60000}]
    if incomplete:
        raise RTA4Core3CalibrationV6Error(
            f"calibration horizon pairing is incomplete: {incomplete[:3]}"
        )

    def exact(value: Any, label: str) -> Fraction:
        try:
            result = Fraction(str(value))
        except (ValueError, ZeroDivisionError) as exc:
            raise RTA4Core3CalibrationV6Error(
                f"calibration {label} is not exact numeric material"
            ) from exc
        return result

    def exact_value(value: Fraction) -> int | str:
        return (
            value.numerator
            if value.denominator == 1 else fraction_text(value)
        )

    def ratio(numerator: Any, denominator: Any) -> dict[str, Any]:
        exact_numerator = exact(numerator, "ratio numerator")
        exact_denominator = exact(denominator, "ratio denominator")
        return {
            "numerator": exact_value(exact_numerator),
            "denominator": exact_value(exact_denominator),
            "display": (
                exact_value(exact_numerator / exact_denominator)
                if exact_denominator else None
            ),
        }

    horizon_summary = []
    for horizon in CORE3_CALIBRATION_RELEASE_HORIZONS_V6:
        group = by_horizon[horizon]
        released = sum(int(row["released_job_count"]) for row in group)
        classified = sum(int(row["classified_job_count"]) for row in group)
        missed = sum(int(row["deadline_miss_job_count"]) for row in group)
        coverage: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for row in group:
            for item in row["conditional_coverage"]:
                coverage[str(item["exact_e0"])][0] += int(
                    item["coverage_rate_numerator"]
                )
                coverage[str(item["exact_e0"])][1] += int(
                    item["coverage_rate_denominator"]
                )
        horizon_summary.append({
            "release_horizon": horizon,
            "run_count": len(group),
            "released_job_count": released,
            "classified_job_count": classified,
            "unfinished_without_miss_count": sum(
                int(row["unfinished_without_miss_count"]) for row in group
            ),
            "deadline_miss": ratio(missed, released),
            "conditional_coverage": [
                {"exact_e0": exact, **ratio(*coverage[exact])}
                for exact in sorted(coverage, key=Fraction)
            ],
            "wall_seconds": exact_value(sum(
                (
                    exact(row.get("runtime_wall_seconds", 0), "wall time")
                    for row in group
                ),
                Fraction(0),
            )),
            "simulator_timeout_count": sum(
                row.get("simulation_status") == "TIMEOUT" for row in group
            ),
        })

    capacity_summary = []
    for capacity in sorted(by_capacity, key=Fraction):
        group = by_capacity[capacity]
        offered = sum(
            (exact(row["offered_energy_j"], "offered energy") for row in group),
            Fraction(0),
        )
        clipped = sum(
            (exact(row["clipped_energy_j"], "clipped energy") for row in group),
            Fraction(0),
        )
        released = sum(int(row["released_job_count"]) for row in group)
        missed = sum(int(row["deadline_miss_job_count"]) for row in group)
        capacity_summary.append({
            "battery_capacity": capacity,
            "run_count": len(group),
            "offered_energy_j": exact_value(offered),
            "clipped_energy_j": exact_value(clipped),
            "overflow_ratio": ratio(clipped, offered),
            "battery_full_ticks": sum(
                int(row["battery_full_ticks"]) for row in group
            ),
            "deadline_miss": ratio(missed, released),
            "valid_job_count": released,
            "wall_seconds": exact_value(sum(
                (
                    exact(row.get("runtime_wall_seconds", 0), "wall time")
                    for row in group
                ),
                Fraction(0),
            )),
        })

    paired_differences = []
    delta_fields = (
        "released_job_count", "classified_job_count",
        "unfinished_without_miss_count", "deadline_miss_job_count",
        "offered_energy_j", "clipped_energy_j", "battery_full_ticks",
        "runtime_wall_seconds",
    )
    for key in sorted(pair_rows, key=lambda item: tuple(map(str, item))):
        low, high = pair_rows[key][30000], pair_rows[key][60000]
        paired_differences.append({
            "taskset_identity": key[0], "track": key[1],
            "release_mode": key[2], "battery_capacity": key[3],
            "from_release_horizon": 30000,
            "to_release_horizon": 60000,
            "delta": {
                field: exact_value(
                    exact(high.get(field, 0), field)
                    - exact(low.get(field, 0), field)
                )
                for field in delta_fields
            },
        })
    material = {
        "summary_schema_version": (
            "ASAP_BLOCK_V9_3_RTA4_CORE3_CALIBRATION_SUMMARY_V6"
        ),
        "source_root": str(root),
        "run_count": len(rows),
        "horizon_summary": horizon_summary,
        "capacity_summary": capacity_summary,
        "paired_differences": paired_differences,
        "pairing_complete": True,
        "automatic_parameter_selection": False,
    }
    return {
        **material,
        "calibration_summary_identity": domain_hash(
            CORE3_CALIBRATION_SUMMARY_DOMAIN_V6, material,
        ),
    }


def freeze_calibration_v6(
    summary_path: Path | str,
    output_path: Path | str,
    *,
    release_horizon: int,
    b_low: str,
    b_high: str,
) -> Mapping[str, Any]:
    """Bind a human-selected tuple to an immutable calibration summary."""

    source = Path(summary_path).expanduser().resolve(strict=True)
    payload = source.read_bytes()
    try:
        summary = json.loads(payload)
    except Exception as exc:
        raise RTA4Core3CalibrationV6Error(
            "calibration summary is unreadable"
        ) from exc
    if summary.get("pairing_complete") is not True:
        raise RTA4Core3CalibrationV6Error("calibration summary is incomplete")
    if release_horizon not in CORE3_CALIBRATION_RELEASE_HORIZONS_V6:
        raise RTA4Core3CalibrationV6Error("freeze horizon is not a candidate")
    capacities = {
        row.get("battery_capacity")
        for row in summary.get("capacity_summary", [])
        if isinstance(row, Mapping)
    }
    if b_low not in capacities or b_high not in capacities:
        raise RTA4Core3CalibrationV6Error("freeze capacity is not a candidate")
    if Fraction(b_low) >= Fraction(b_high):
        raise RTA4Core3CalibrationV6Error("freeze requires B_low < B_high")
    material = {
        "freeze_schema_version": (
            "ASAP_BLOCK_V9_3_RTA4_CORE3_CALIBRATION_FREEZE_V6"
        ),
        "calibration_summary_sha256": hashlib.sha256(payload).hexdigest(),
        "calibration_summary_identity": summary.get(
            "calibration_summary_identity"
        ),
        "release_horizon": release_horizon,
        "b_low": b_low,
        "b_high": b_high,
        "selection_mode": "EXPLICIT_HUMAN_REVIEWED_NO_AUTOMATIC_SELECTION",
    }
    value = {
        **material,
        "calibration_freeze_identity": domain_hash(
            CORE3_CALIBRATION_FREEZE_DOMAIN_V6, material,
        ),
    }
    atomic_write_json(Path(output_path), value)
    return value


__all__ = [
    "CORE3_CALIBRATION_CONFIG_V6",
    "CORE3_CALIBRATION_RELEASE_HORIZONS_V6",
    "RTA4Core3CalibrationV6Error",
    "freeze_calibration_v6",
    "materialize_calibration_campaigns_v6",
    "summarize_calibration_v6",
    "write_calibration_campaigns_v6",
]
