from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from experiments.v9_3.config import canonical_json
from experiments.v9_3.exact_energy import ExactEnergyError
from experiments.v9_3.simulation_engine import (
    SHARED_SOLAR_INPUT_CLASSIFICATION,
    SharedSolarInput,
    SimulationConfigurationError,
    construct_paired_harvest_trace,
    construct_shared_solar_input,
    materialize_simulation_inputs,
)


ROOT = Path(__file__).resolve().parents[1]
BASE_SYSTEM = ROOT / "system_config_unified_template.yml"
ENERGY_SUPPORT = (
    ROOT / "configs/v9_3_rta4_core3_simulation_energy_support_v1.yaml"
)


def _support() -> dict:
    return yaml.safe_load(ENERGY_SUPPORT.read_text(encoding="utf-8"))


def _write_solar(path: Path, values: tuple[int, ...]) -> None:
    path.write_text(
        "irradiance_W_per_m2\n"
        + "\n".join(str(value) for value in values)
        + "\n",
        encoding="utf-8",
    )


def _write_system(
    path: Path,
    solar_path: Path,
    *,
    time_of_day_ms: int = 0,
    use_real_solar_data: bool = True,
) -> None:
    replacements = {
        "day_of_year": "1",
        "time_of_day_ms": str(time_of_day_ms),
        "use_real_solar_data": str(use_real_solar_data).lower(),
        "solar_data_file": json.dumps(str(solar_path)),
    }
    seen = {key: 0 for key in replacements}
    lines = []
    for line in BASE_SYSTEM.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        key = next(
            (
                candidate
                for candidate in replacements
                if stripped.startswith(candidate + ":")
            ),
            None,
        )
        if key is None:
            lines.append(line)
            continue
        indent = line[:len(line) - len(line.lstrip())]
        lines.append(f"{indent}{key}: {replacements[key]}")
        seen[key] += 1
    assert set(seen.values()) == {1}
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _direct_support(system_path: Path, *, solar_scale: str = "1") -> dict:
    return {
        "simulation_initial_battery": "0",
        "battery_capacity": "10",
        "service_curve": {
            "id": "test-production-solar",
            "system_template": str(system_path),
            "horizon": 10,
            "require_real_solar_data": True,
            "raw_reference_pv_area_m2": "1",
            "solar_scale": solar_scale,
        },
    }


def test_repository_solar_input_is_tracked_stable_and_production_identical(
    tmp_path,
):
    support = _support()
    first = construct_shared_solar_input(
        BASE_SYSTEM,
        ENERGY_SUPPORT,
        horizon=4,
        source_root=ROOT,
    )
    second = construct_shared_solar_input(
        BASE_SYSTEM,
        ENERGY_SUPPORT,
        horizon=4,
        source_root=ROOT,
    )

    system_document = yaml.safe_load(BASE_SYSTEM.read_text(encoding="utf-8"))
    assert system_document["energy_management"]["use_real_solar_data"] is True
    assert first == second
    assert canonical_json(first.provenance).encode("utf-8") == canonical_json(
        second.provenance
    ).encode("utf-8")
    assert first.provenance["classification"] == (
        SHARED_SOLAR_INPUT_CLASSIFICATION
    )
    assert first.provenance["solar_csv"]["source_relative_path"] == (
        system_document["energy_management"]["solar_data_file"]
    )
    tracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--error-unmatch",
            first.provenance["solar_csv"]["source_relative_path"],
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0, tracked.stderr

    system_path, _task_path = materialize_simulation_inputs(
        BASE_SYSTEM,
        tmp_path / "production",
        (),
        processors=4,
        initial_battery=Fraction(
            support["simulation_initial_battery"]
        ),
        battery_capacity=Fraction(support["battery_capacity"]),
        service_curve=support["service_curve"],
    )
    assert first.harvest_j_per_tick == construct_paired_harvest_trace(
        system_path, 4
    )
    assert first.offered_harvest_j == sum(
        first.harvest_j_per_tick, Fraction(0)
    )


def test_phase_scale_and_caller_selected_csv_change_the_replayed_trace(
    tmp_path,
):
    first_csv = tmp_path / "first.csv"
    second_csv = tmp_path / "second.csv"
    _write_solar(first_csv, (1, 3, 5))
    _write_solar(second_csv, (2, 4, 6))
    phase_zero = tmp_path / "phase-zero.yml"
    phase_one = tmp_path / "phase-one.yml"
    alternate = tmp_path / "alternate.yml"
    _write_system(phase_zero, first_csv)
    _write_system(phase_one, first_csv, time_of_day_ms=60_000)
    _write_system(alternate, second_csv)

    base = construct_shared_solar_input(
        phase_zero, _direct_support(phase_zero), horizon=1,
        source_root=tmp_path,
    )
    shifted = construct_shared_solar_input(
        phase_one, _direct_support(phase_one), horizon=1,
        source_root=tmp_path,
    )
    scaled = construct_shared_solar_input(
        phase_zero,
        _direct_support(phase_zero, solar_scale="1/2"),
        horizon=1,
        source_root=tmp_path,
    )
    alternate_csv = construct_shared_solar_input(
        alternate, _direct_support(alternate), horizon=1,
        source_root=tmp_path,
    )

    assert base.harvest_j_per_tick != shifted.harvest_j_per_tick
    assert base.harvest_j_per_tick != scaled.harvest_j_per_tick
    assert base.harvest_j_per_tick != alternate_csv.harvest_j_per_tick
    assert base.provenance["solar_csv"]["sha256"] != (
        alternate_csv.provenance["solar_csv"]["sha256"]
    )
    assert shifted.provenance["time_of_day_ms"] != (
        base.provenance["time_of_day_ms"]
    )
    assert scaled.provenance["solar_scale"] == "1/2"


def test_shared_solar_input_rejects_missing_disabled_or_mismatched_sources(
    tmp_path,
):
    solar = tmp_path / "solar.csv"
    _write_solar(solar, (1, 2))
    valid = tmp_path / "valid.yml"
    _write_system(valid, solar)

    missing = tmp_path / "missing.yml"
    _write_system(missing, tmp_path / "absent.csv")
    with pytest.raises(
        SimulationConfigurationError, match="solar data file not found"
    ):
        construct_shared_solar_input(
            missing, _direct_support(missing), horizon=1,
            source_root=tmp_path,
        )

    disabled = tmp_path / "disabled.yml"
    _write_system(disabled, solar, use_real_solar_data=False)
    with pytest.raises(
        SimulationConfigurationError, match="requires real solar data"
    ):
        construct_shared_solar_input(
            disabled, _direct_support(disabled), horizon=1,
            source_root=tmp_path,
        )

    mismatch = deepcopy(_direct_support(valid))
    mismatch["service_curve"]["system_template"] = str(missing)
    with pytest.raises(
        SimulationConfigurationError, match="does not match"
    ):
        construct_shared_solar_input(
            valid, mismatch, horizon=1, source_root=tmp_path
        )


def test_shared_solar_provenance_describes_inputs_not_a_universal_claim(
    tmp_path,
):
    solar = tmp_path / "solar.csv"
    _write_solar(solar, (1, 2))
    system = tmp_path / "system.yml"
    _write_system(system, solar)
    shared = construct_shared_solar_input(
        system, _direct_support(system), horizon=1,
        source_root=tmp_path,
    )
    provenance = shared.provenance

    assert set(("source_relative_path", "sha256", "size")) <= set(
        provenance["system_template"]
    )
    assert set(("source_relative_path", "sha256", "size")) <= set(
        provenance["energy_support"]
    )
    assert set(("source_relative_path", "sha256", "size")) <= set(
        provenance["solar_csv"]
    )
    assert provenance["sampling_rule"]
    assert provenance["operation_order_version"]
    assert provenance["tick_duration_seconds"]
    assert provenance["replay_input_sha256"]
    assert "UNIVERSAL_REAL_SOLAR_GUARANTEE" not in canonical_json(provenance)


def test_beta_is_the_exact_arbitrary_window_minimum_with_optional_starts():
    trace = tuple(
        Fraction.from_float(float(value))
        for value in (4, 1, 3, 2)
    )
    shared = SharedSolarInput(trace, {})
    beta = shared.beta(3)

    assert beta[0] == 0
    assert all(value >= 0 for value in beta)
    assert all(left <= right for left, right in zip(beta, beta[1:]))
    for length in range(1, 4):
        candidates = []
        for start in range(0, len(trace) - length + 1):
            total = 0.0
            for value in trace[start:start + length]:
                total += float(value)
            candidate = Fraction.from_float(total)
            candidates.append(candidate)
            assert beta[length] <= candidate
        assert beta[length] == min(candidates)

    restricted = shared.beta(2, valid_start_range=range(1, 3))
    assert restricted[1] == min(trace[1], trace[2])
    expected = 0.0
    for value in trace[1:3]:
        expected += float(value)
    assert restricted[2] == Fraction.from_float(expected)
    with pytest.raises(ExactEnergyError, match="incomplete window"):
        shared.beta(3, valid_start_range=range(2, 3))
