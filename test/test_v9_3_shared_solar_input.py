from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import subprocess

import pytest
import yaml

import asap_block_rta as legacy_rta
from experiments.v9_3.config import canonical_json
from experiments.v9_3.exact_energy import ExactEnergyError
from experiments.v9_3.solar_parse_proof import (
    build_solar_stod_parse_proof,
    write_solar_stod_parse_proof,
)
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
SOLAR_CSV = ROOT / "data/processed/shenyang_solar_minute.csv"


def _support() -> dict:
    return yaml.safe_load(ENERGY_SUPPORT.read_text(encoding="utf-8"))


def _write_solar(path: Path, values: tuple[object, ...]) -> None:
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
    day_of_year: int = 1,
    time_of_day_ms: int = 0,
    use_real_solar_data: bool = True,
) -> None:
    replacements = {
        "day_of_year": str(day_of_year),
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


def _direct_support_document(
    system_path: Path,
    *,
    solar_scale: str = "1",
    horizon: int = 10,
    support_id: str = "test-production-solar",
) -> dict:
    return {
        "simulation_initial_battery": "0",
        "battery_capacity": "10",
        "service_curve": {
            "id": support_id,
            "system_template": str(system_path),
            "horizon": horizon,
            "require_real_solar_data": True,
            "raw_reference_pv_area_m2": "1",
            "solar_scale": solar_scale,
        },
    }


def _direct_support(
    system_path: Path,
    **kwargs,
) -> Path:
    path = system_path.with_name(system_path.stem + "-energy-support.yml")
    path.write_text(
        yaml.safe_dump(
            _direct_support_document(system_path, **kwargs),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def verified_shared(tmp_path, rta4_solar_stod_verifier):
    def construct(
        system_path,
        support,
        *,
        horizon,
        source_root,
    ):
        system = legacy_rta.load_system_config(str(system_path))
        solar = Path(legacy_rta._resolve_solar_path(system)).resolve(strict=True)
        proof = build_solar_stod_parse_proof(
            source_root=source_root,
            base_system_path=system_path,
            energy_support=support,
            solar_csv_path=solar,
            day_of_year=system.day_of_year,
            time_of_day_ms=system.time_of_day_ms,
            horizon=horizon,
            verifier_binary=rta4_solar_stod_verifier,
            build_verifier=False,
        )
        proof_path = tmp_path / f"{proof['proof_id']}.json"
        write_solar_stod_parse_proof(proof_path, proof)
        return construct_shared_solar_input(
            system_path,
            support,
            horizon=horizon,
            source_root=source_root,
            solar_parse_proof=proof_path,
            solar_parse_verifier_binary=rta4_solar_stod_verifier,
        )

    return construct


def test_repository_solar_input_is_tracked_stable_and_production_identical(
    tmp_path, verified_shared,
):
    support = _support()
    first = verified_shared(
        BASE_SYSTEM,
        ENERGY_SUPPORT,
        horizon=30_000,
        source_root=ROOT,
    )
    second = verified_shared(
        BASE_SYSTEM,
        ENERGY_SUPPORT,
        horizon=30_000,
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
    assert first.provenance["system_template"]["source_relative_path"] == (
        "system_config_unified_template.yml"
    )
    assert first.provenance["energy_support"]["source_relative_path"] == (
        "configs/v9_3_rta4_core3_simulation_energy_support_v1.yaml"
    )
    assert all(
        "\\" not in first.provenance[source]["source_relative_path"]
        and "/tmp/partsim_v9_3" not in (
            first.provenance[source]["source_relative_path"]
        )
        for source in ("system_template", "energy_support", "solar_csv")
    )
    start_offset = legacy_rta.materialize_runtime_start_offset_ms(
        system_document["energy_management"]["day_of_year"],
        system_document["energy_management"]["time_of_day_ms"],
    )
    expected_first = (start_offset + 1) // 60_000
    expected_last = (start_offset + 30_000) // 60_000
    assert first.provenance["first_accessed_data_row"] == expected_first
    assert first.provenance["last_accessed_data_row"] == expected_last
    assert first.provenance["first_calendar_minute_index"] == expected_first
    assert first.provenance["last_calendar_minute_index"] == expected_last
    assert first.provenance["accessed_sample_count"] == 1
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
        system_path, 30_000
    )
    assert first.offered_harvest_j == sum(
        first.harvest_j_per_tick, Fraction(0)
    )


def test_repository_negative_sentinel_fails_closed_before_trace_or_beta(
    tmp_path,
):
    physical_rows = SOLAR_CSV.read_text(encoding="utf-8").splitlines()[1:]
    negative_index = next(
        index for index, value in enumerate(physical_rows)
        if float(value) < 0
    )
    system = tmp_path / "repository-negative.yml"
    _write_system(
        system,
        SOLAR_CSV,
        day_of_year=negative_index // 1440 + 1,
        time_of_day_ms=(negative_index % 1440) * 60_000,
    )

    with pytest.raises(
        SimulationConfigurationError,
        match=(
            rf"physical_data_row_index={negative_index} "
            rf"calendar_minute_index={negative_index} "
            r"category=NEGATIVE_ACCESSED_IRRADIANCE"
        ),
    ):
        construct_shared_solar_input(
            system,
            _direct_support(system, horizon=1),
            horizon=1,
            source_root=ROOT,
        )


def test_phase_scale_and_caller_selected_csv_change_the_replayed_trace(
    tmp_path, verified_shared,
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

    base = verified_shared(
        phase_zero, _direct_support(phase_zero), horizon=1,
        source_root=tmp_path,
    )
    shifted = verified_shared(
        phase_one, _direct_support(phase_one), horizon=1,
        source_root=tmp_path,
    )
    scaled = verified_shared(
        phase_zero,
        _direct_support(phase_zero, solar_scale="1/2"),
        horizon=1,
        source_root=tmp_path,
    )
    alternate_csv = verified_shared(
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


def test_negative_outside_accessed_window_is_allowed_but_accessed_negative_rejects(
    tmp_path, verified_shared,
):
    solar = tmp_path / "solar.csv"
    _write_solar(solar, (-7, 1, -7))
    current = tmp_path / "current.yml"
    accessed_negative = tmp_path / "accessed-negative.yml"
    _write_system(current, solar, time_of_day_ms=60_000)
    _write_system(accessed_negative, solar)

    allowed = verified_shared(
        current,
        _direct_support(current),
        horizon=1,
        source_root=tmp_path,
    )
    assert allowed.harvest_j_per_tick[0] > 0
    assert allowed.provenance["physical_data_row_count"] == 3
    assert allowed.provenance["first_accessed_data_row"] == 1
    assert allowed.provenance["last_accessed_data_row"] == 1

    with pytest.raises(
        SimulationConfigurationError,
        match=(
            r"source=solar.csv physical_data_row_index=0 "
            r"calendar_minute_index=0 "
            r"category=NEGATIVE_ACCESSED_IRRADIANCE"
        ),
    ):
        construct_shared_solar_input(
            accessed_negative,
            _direct_support(accessed_negative),
            horizon=1,
            source_root=tmp_path,
        )


@pytest.mark.parametrize(
    ("rows", "category"),
    [
        (("1", "", "2"), "EMPTY_DATA_ROW"),
        (("1", "not-a-number", "2"), "INVALID_NUMERIC_ROW"),
    ],
)
def test_filtered_physical_row_before_target_is_rejected(
    tmp_path,
    rows,
    category,
):
    solar = tmp_path / "solar.csv"
    _write_solar(solar, rows)
    system = tmp_path / "system.yml"
    _write_system(system, solar, time_of_day_ms=120_000)

    with pytest.raises(
        SimulationConfigurationError,
        match=(
            rf"physical_data_row_index=1 calendar_minute_index=1 "
            rf"category={category}"
        ),
    ):
        construct_shared_solar_input(
            system,
            _direct_support(system),
            horizon=1,
            source_root=tmp_path,
        )


@pytest.mark.parametrize("value", ("NaN", "Inf", "-Inf"))
def test_nonfinite_accessed_irradiance_is_rejected(tmp_path, value):
    solar = tmp_path / "solar.csv"
    _write_solar(solar, (value,))
    system = tmp_path / "system.yml"
    _write_system(system, solar)

    with pytest.raises(
        SimulationConfigurationError,
        match=(
            r"physical_data_row_index=0 calendar_minute_index=0 "
            r"category=NONFINITE_IRRADIANCE"
        ),
    ):
        construct_shared_solar_input(
            system,
            _direct_support(system),
            horizon=1,
            source_root=tmp_path,
        )


def test_insufficient_physical_rows_reports_last_required_row(tmp_path):
    solar = tmp_path / "solar.csv"
    _write_solar(solar, (1,))
    system = tmp_path / "system.yml"
    _write_system(system, solar, time_of_day_ms=60_000)

    with pytest.raises(
        SimulationConfigurationError,
        match=(
            r"physical_data_row_index=1 calendar_minute_index=1 "
            r"category=INSUFFICIENT_PHYSICAL_DATA_ROWS"
        ),
    ):
        construct_shared_solar_input(
            system,
            _direct_support(system),
            horizon=1,
            source_root=tmp_path,
        )


def test_horizon_alone_changes_access_range_and_provenance(
    tmp_path, verified_shared,
):
    solar = tmp_path / "solar.csv"
    _write_solar(solar, (1, 2))
    system = tmp_path / "system.yml"
    _write_system(system, solar)
    support = _direct_support(system, horizon=60_000)

    short = verified_shared(
        system, support, horizon=1, source_root=tmp_path,
    )
    long = verified_shared(
        system, support, horizon=60_000, source_root=tmp_path,
    )

    assert short.provenance["last_accessed_data_row"] == 0
    assert short.provenance["accessed_sample_count"] == 1
    assert long.provenance["last_accessed_data_row"] == 1
    assert long.provenance["accessed_sample_count"] == 2
    assert short.provenance["horizon"] == 1
    assert long.provenance["horizon"] == 60_000
    assert short.provenance["replay_input_sha256"] != (
        long.provenance["replay_input_sha256"]
    )


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

    mismatch = deepcopy(_direct_support_document(valid))
    mismatch["service_curve"]["system_template"] = str(missing)
    mismatch_path = tmp_path / "mismatch-support.yml"
    mismatch_path.write_text(
        yaml.safe_dump(mismatch, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(
        SimulationConfigurationError, match="does not match"
    ):
        construct_shared_solar_input(
            valid, mismatch_path, horizon=1, source_root=tmp_path
        )


def test_shared_solar_provenance_describes_inputs_not_a_universal_claim(
    tmp_path, verified_shared,
):
    solar = tmp_path / "solar.csv"
    _write_solar(solar, (1, 2))
    system = tmp_path / "system.yml"
    _write_system(system, solar)
    shared = verified_shared(
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
    assert provenance["indexing_policy"]
    assert provenance["invalid_row_policy"]
    assert provenance["negative_value_policy"]
    assert provenance["operation_order_version"]
    assert provenance["tick_duration_seconds"]
    assert provenance["physical_data_row_count"] == 2
    assert provenance["first_accessed_data_row"] == 0
    assert provenance["last_accessed_data_row"] == 0
    assert provenance["first_calendar_minute_index"] == 0
    assert provenance["last_calendar_minute_index"] == 0
    assert provenance["accessed_sample_count"] == 1
    assert provenance["raw_reference_pv_area_m2"] == "1"
    assert provenance["effective_pv_area_m2"] == "1"
    assert provenance["replay_input_sha256"]
    assert "UNIVERSAL_REAL_SOLAR_GUARANTEE" not in canonical_json(provenance)


def test_same_length_source_byte_changes_change_provenance(
    tmp_path, verified_shared,
):
    solar_a = tmp_path / "a.csv"
    solar_b = tmp_path / "b.csv"
    _write_solar(solar_a, (1,))
    _write_solar(solar_b, (2,))
    assert solar_a.stat().st_size == solar_b.stat().st_size

    system_a = tmp_path / "system-a.yml"
    system_b = tmp_path / "system-b.yml"
    _write_system(system_a, solar_a, time_of_day_ms=0)
    _write_system(system_b, solar_a, time_of_day_ms=1)
    assert system_a.stat().st_size == system_b.stat().st_size

    support_a = tmp_path / "support-a.yml"
    support_b = tmp_path / "support-b.yml"
    support_a.write_text(
        yaml.safe_dump(
            _direct_support_document(system_a, support_id="support-a"),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    support_b.write_text(
        yaml.safe_dump(
            _direct_support_document(system_a, support_id="support-b"),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assert support_a.stat().st_size == support_b.stat().st_size

    baseline = verified_shared(
        system_a, support_a, horizon=1, source_root=tmp_path,
    )
    changed_system = verified_shared(
        system_b, _direct_support(system_b), horizon=1, source_root=tmp_path,
    )
    changed_support = verified_shared(
        system_a, support_b, horizon=1, source_root=tmp_path,
    )
    csv_system = tmp_path / "system-c.yml"
    _write_system(csv_system, solar_b)
    changed_csv = verified_shared(
        csv_system, _direct_support(csv_system), horizon=1,
        source_root=tmp_path,
    )

    assert baseline.provenance["system_template"]["sha256"] != (
        changed_system.provenance["system_template"]["sha256"]
    )
    assert baseline.provenance["energy_support"]["sha256"] != (
        changed_support.provenance["energy_support"]["sha256"]
    )
    assert baseline.provenance["solar_csv"]["sha256"] != (
        changed_csv.provenance["solar_csv"]["sha256"]
    )


def test_direct_mapping_is_not_a_formal_safe_energy_support(
    tmp_path,
):
    solar = tmp_path / "solar.csv"
    system = tmp_path / "system.yml"
    _write_solar(solar, (1,))
    _write_system(system, solar)
    with pytest.raises(
        SimulationConfigurationError,
        match="versioned energy-support file path",
    ):
        construct_shared_solar_input(
            system,
            _direct_support_document(system),
            horizon=1,
            source_root=tmp_path,
        )


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


@pytest.mark.parametrize(
    ("raw_trace", "expected"),
    [
        ((1, 2, 3, 4, 5), (0, 1, 3, 6, 10, 15)),
        ((5, 4, 3, 2, 1), (0, 1, 3, 6, 10, 15)),
        ((5, 1, 4, 1, 3), (0, 1, 4, 6, 9, 14)),
    ],
    ids=("ascending", "descending", "alternating"),
)
def test_nonconstant_arbitrary_window_beta_regressions(raw_trace, expected):
    trace = tuple(Fraction.from_float(float(value)) for value in raw_trace)
    assert SharedSolarInput(trace, {}).beta(len(trace)) == tuple(
        Fraction(value) for value in expected
    )


@pytest.mark.parametrize(
    "trace",
    [
        (Fraction(-1),),
        (float("nan"),),
        (float("inf"),),
        (float("-inf"),),
    ],
)
def test_beta_rejects_negative_or_nonfinite_trace(trace):
    with pytest.raises(ExactEnergyError, match="service trace"):
        SharedSolarInput(trace, {}).beta(1)


def test_beta_boundary_contract_and_last_legal_start():
    empty = SharedSolarInput((), {})
    assert empty.beta(0) == (Fraction(0),)
    with pytest.raises(ExactEnergyError, match="outside the trace"):
        empty.beta(1)

    trace = tuple(Fraction(value) for value in (1, 2, 3))
    shared = SharedSolarInput(trace, {})
    assert shared.beta(0) == (Fraction(0),)
    with pytest.raises(ExactEnergyError, match="outside the trace"):
        shared.beta(-1)
    with pytest.raises(ExactEnergyError, match="outside the trace"):
        shared.beta(4)
    with pytest.raises(ExactEnergyError, match="must not be empty"):
        shared.beta(1, valid_start_range=range(0, 0))
    with pytest.raises(ExactEnergyError, match="unit-step"):
        shared.beta(1, valid_start_range=range(0, 3, 2))
    assert shared.beta(
        1, valid_start_range=range(2, 3)
    ) == (Fraction(0), Fraction(3))
    with pytest.raises(ExactEnergyError, match="incomplete window"):
        shared.beta(2, valid_start_range=range(2, 3))
