from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import struct

import pytest
import yaml

import asap_block_rta as legacy_rta
from experiments.v9_3 import simulation_engine
from experiments.v9_3.config import canonical_json, domain_hash
from experiments.v9_3.simulation_engine import (
    SimulationConfigurationError,
    construct_paired_harvest_trace,
    construct_shared_solar_input,
    materialize_simulation_inputs,
)
from experiments.v9_3.solar_parse_proof import (
    SOLAR_STOD_PARSE_PROOF_DOMAIN,
    SolarParseProofError,
    build_solar_stod_parse_proof,
    inspect_solar_rows,
    validate_solar_stod_parse_proof,
    write_solar_stod_parse_proof,
)
from scripts import build_v9_3_rta4_solar_parse_proof as proof_cli


ROOT = Path(__file__).resolve().parents[1]
BASE_SYSTEM = ROOT / "system_config_unified_template.yml"
CANONICAL_SUPPORT = (
    ROOT / "configs/v9_3_rta4_core3_simulation_energy_support_v1.yaml"
)
CANONICAL_SOLAR = ROOT / "data/processed/shenyang_solar_minute.csv"


def _write_solar(path: Path, rows: tuple[str, ...]) -> None:
    path.write_text(
        "irradiance_W_per_m2\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


def _write_system(
    path: Path,
    solar_path: Path,
    *,
    day_of_year: int = 1,
    time_of_day_ms: int = 0,
) -> None:
    replacements = {
        "day_of_year": str(day_of_year),
        "time_of_day_ms": str(time_of_day_ms),
        "use_real_solar_data": "true",
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


def _write_support(
    path: Path,
    system_path: Path,
    *,
    horizon: int = 60_000,
    support_id: str = "proof-a",
) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "simulation_initial_battery": "0",
                "battery_capacity": "10",
                "service_curve": {
                    "id": support_id,
                    "system_template": str(system_path),
                    "horizon": horizon,
                    "require_real_solar_data": True,
                    "raw_reference_pv_area_m2": "1",
                    "solar_scale": "1",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _case(
    tmp_path: Path,
    rows: tuple[str, ...],
    *,
    time_of_day_ms: int = 0,
    horizon: int = 1,
) -> tuple[Path, Path, Path]:
    solar = tmp_path / "solar.csv"
    system = tmp_path / "system.yml"
    support = tmp_path / "support.yml"
    _write_solar(solar, rows)
    _write_system(system, solar, time_of_day_ms=time_of_day_ms)
    _write_support(support, system, horizon=max(horizon, 60_000))
    return system, support, solar


def _proof(
    tmp_path: Path,
    verifier: Path,
    system: Path,
    support: Path,
    solar: Path,
    *,
    day_of_year: int = 1,
    time_of_day_ms: int = 0,
    horizon: int = 1,
    name: str = "proof.json",
) -> tuple[dict, Path]:
    value = build_solar_stod_parse_proof(
        source_root=tmp_path,
        base_system_path=system,
        energy_support=support,
        solar_csv_path=solar,
        day_of_year=day_of_year,
        time_of_day_ms=time_of_day_ms,
        horizon=horizon,
        verifier_binary=verifier,
        build_verifier=False,
    )
    path = tmp_path / name
    write_solar_stod_parse_proof(path, value)
    return value, path


def _validate(
    proof_path: Path,
    verifier: Path,
    root: Path,
    system: Path,
    support: Path,
    solar: Path,
    *,
    day_of_year: int = 1,
    time_of_day_ms: int = 0,
    horizon: int = 1,
) -> dict:
    return validate_solar_stod_parse_proof(
        proof_path,
        verifier_binary=verifier,
        source_root=root,
        base_system_path=system,
        energy_support=support,
        solar_csv_path=solar,
        day_of_year=day_of_year,
        time_of_day_ms=time_of_day_ms,
        horizon=horizon,
    )


@pytest.mark.parametrize(
    "text",
    ("216.05", "2.1605e2", " 216.05 "),
)
def test_successful_stod_proof_requires_exact_python_cpp_bits(
    tmp_path, rta4_solar_stod_verifier, text,
):
    system, support, solar = _case(tmp_path, (text,))
    proof, proof_path = _proof(
        tmp_path, rta4_solar_stod_verifier, system, support, solar,
    )
    row = proof["rows"][0]
    expected = struct.pack(">d", float(text.strip())).hex()
    assert row["parse_status"] == "success"
    assert row["binary64_bits"] == expected
    assert row["python_binary64_bits"] == expected
    assert row["raw_text_sha256"] == hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()
    assert _validate(
        proof_path, rta4_solar_stod_verifier, tmp_path,
        system, support, solar,
    ) == proof

    shared = construct_shared_solar_input(
        system,
        support,
        horizon=1,
        source_root=tmp_path,
        solar_parse_proof=proof_path,
        solar_parse_verifier_binary=rta4_solar_stod_verifier,
    )
    assert shared.harvest_j_per_tick == construct_paired_harvest_trace(
        system, 1,
    )
    assert shared.beta(1)[1] == shared.harvest_j_per_tick[0]
    assert shared.provenance["solar_stod_parser_binding"]["proof_id"] == (
        proof["proof_id"]
    )


def test_trailing_text_is_rejected_even_when_stod_consumes_a_prefix(
    tmp_path, rta4_solar_stod_verifier,
):
    system, support, solar = _case(tmp_path, ("216.05tail",))
    inspected = inspect_solar_rows(
        rta4_solar_stod_verifier, solar, first_row=0, last_row=0,
    )
    assert inspected["rows"][0]["parse_status"] == "success"
    assert inspected["rows"][0]["consumed_characters"] < (
        inspected["rows"][0]["raw_text_length"]
    )
    with pytest.raises(SolarParseProofError, match="INVALID_NUMERIC_ROW"):
        _proof(
            tmp_path, rta4_solar_stod_verifier, system, support, solar,
        )


@pytest.mark.parametrize(
    "text",
    (
        "1e-308",
        "1e-309",
        "5e-324",
        "1e-4000",
        "2.2250738585072014e-308",
        "1.7976931348623157e308",
        "1.7976931348623159e308",
        "1e309",
    ),
)
def test_numeric_boundaries_follow_actual_verifier_not_python_thresholds(
    tmp_path, rta4_solar_stod_verifier, text,
):
    system, support, solar = _case(tmp_path, (text,))
    inspected = inspect_solar_rows(
        rta4_solar_stod_verifier, solar, first_row=0, last_row=0,
    )
    row = inspected["rows"][0]
    python_value = float(text)
    python_acceptable = math.isfinite(python_value) and python_value >= 0
    cpp_acceptable = (
        inspected["returncode"] == 0
        and row["parse_status"] == "success"
        and row["finite"] is True
        and row["negative"] is False
        and row["binary64_bits"]
        == struct.pack(">d", python_value).hex()
    )

    if python_acceptable and cpp_acceptable:
        proof, proof_path = _proof(
            tmp_path, rta4_solar_stod_verifier, system, support, solar,
        )
        assert _validate(
            proof_path, rta4_solar_stod_verifier, tmp_path,
            system, support, solar,
        ) == proof
    else:
        with pytest.raises(SolarParseProofError):
            _proof(
                tmp_path, rta4_solar_stod_verifier, system, support, solar,
            )


def test_forged_python_only_success_cannot_bypass_live_cpp_evidence(
    tmp_path, rta4_solar_stod_verifier,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    proof, proof_path = _proof(
        tmp_path, rta4_solar_stod_verifier, system, support, solar,
    )
    replacement = "1e-308"
    _write_solar(solar, (replacement,))
    bits = struct.pack(">d", float(replacement)).hex()
    raw = replacement.encode("utf-8")
    forged = deepcopy(proof)
    forged["inputs"]["solar_csv"]["sha256"] = hashlib.sha256(
        solar.read_bytes()
    ).hexdigest()
    forged["rows"][0].update({
        "parse_status": "success",
        "exception_category": "none",
        "finite": True,
        "negative": False,
        "binary64_bits": bits,
        "python_binary64_bits": bits,
        "consumed_characters": len(raw),
        "raw_text_length": len(raw),
        "raw_text_sha256": hashlib.sha256(raw).hexdigest(),
    })
    _rewrite_proof(proof_path, forged)

    inspected = inspect_solar_rows(
        rta4_solar_stod_verifier, solar, first_row=0, last_row=0,
    )
    row = inspected["rows"][0]
    cpp_matches_python = (
        inspected["returncode"] == 0
        and row["parse_status"] == "success"
        and row["finite"] is True
        and row["negative"] is False
        and row["binary64_bits"] == bits
    )
    if cpp_matches_python:
        assert _validate(
            proof_path, rta4_solar_stod_verifier, tmp_path,
            system, support, solar,
        ) == forged
    else:
        with pytest.raises(SolarParseProofError, match="C\\+\\+|verifier"):
            _validate(
                proof_path, rta4_solar_stod_verifier, tmp_path,
                system, support, solar,
            )


@pytest.mark.parametrize(
    ("text", "category"),
    (
        ("-999.0", "NEGATIVE_ACCESSED_IRRADIANCE"),
        ("NaN", "NONFINITE_IRRADIANCE"),
        ("Inf", "NONFINITE_IRRADIANCE"),
        ("-Inf", "NONFINITE_IRRADIANCE"),
        ("not-a-number", "INVALID_NUMERIC_ROW"),
        ("", "EMPTY_DATA_ROW"),
    ),
)
def test_unsafe_rows_cannot_generate_formal_safe_proof(
    tmp_path, rta4_solar_stod_verifier, text, category,
):
    system, support, solar = _case(tmp_path, (text,))
    with pytest.raises(SolarParseProofError, match=category):
        _proof(
            tmp_path, rta4_solar_stod_verifier, system, support, solar,
        )


def test_missing_row_and_missing_proof_fail_closed(
    tmp_path, rta4_solar_stod_verifier,
):
    system, support, solar = _case(
        tmp_path, ("1",), time_of_day_ms=60_000,
    )
    with pytest.raises(SolarParseProofError, match="INSUFFICIENT"):
        _proof(
            tmp_path,
            rta4_solar_stod_verifier,
            system,
            support,
            solar,
            time_of_day_ms=60_000,
        )
    inspected = inspect_solar_rows(
        rta4_solar_stod_verifier, solar, first_row=1, last_row=1,
    )
    assert inspected["returncode"] != 0
    assert inspected["errors"][0]["category"] == "insufficient_rows"

    ordinary_system, ordinary_support, _ordinary_solar = _case(
        tmp_path, ("1",),
    )
    with pytest.raises(
        SimulationConfigurationError, match="requires a C\\+\\+ parse proof",
    ):
        construct_shared_solar_input(
            ordinary_system,
            ordinary_support,
            horizon=1,
            source_root=tmp_path,
        )


def _rewrite_proof(path: Path, proof: dict) -> None:
    material = {key: value for key, value in proof.items() if key != "proof_id"}
    proof["proof_id"] = domain_hash(SOLAR_STOD_PARSE_PROOF_DOMAIN, material)
    write_solar_stod_parse_proof(path, proof)


def test_proof_row_hash_bits_and_json_mutations_fail_closed(
    tmp_path, rta4_solar_stod_verifier,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    proof, proof_path = _proof(
        tmp_path, rta4_solar_stod_verifier, system, support, solar,
    )

    for field, replacement in (
        ("raw_text_sha256", "0" * 64),
        ("binary64_bits", "0" * 16),
        ("python_binary64_bits", "0" * 16),
    ):
        changed = deepcopy(proof)
        changed["rows"][0][field] = replacement
        _rewrite_proof(proof_path, changed)
        with pytest.raises(SolarParseProofError, match="row"):
            _validate(
                proof_path, rta4_solar_stod_verifier, tmp_path,
                system, support, solar,
            )

    write_solar_stod_parse_proof(proof_path, proof)
    proof_path.write_bytes(proof_path.read_bytes() + b" ")
    with pytest.raises(SolarParseProofError, match="canonical"):
        _validate(
            proof_path, rta4_solar_stod_verifier, tmp_path,
            system, support, solar,
        )


def _same_size_comment_mutation(payload: bytes) -> bytes:
    marker = b"NASA"
    assert marker in payload
    return payload.replace(marker, b"NASB", 1)


def test_same_size_system_support_and_csv_drift_is_rejected(
    tmp_path, rta4_solar_stod_verifier,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    proof, proof_path = _proof(
        tmp_path, rta4_solar_stod_verifier, system, support, solar,
    )

    system_bytes = system.read_bytes()
    changed_system = _same_size_comment_mutation(system_bytes)
    assert len(changed_system) == len(system_bytes)
    system.write_bytes(changed_system)
    with pytest.raises(SolarParseProofError, match="input"):
        _validate(
            proof_path, rta4_solar_stod_verifier, tmp_path,
            system, support, solar,
        )
    system.write_bytes(system_bytes)

    support_bytes = support.read_bytes()
    changed_support = support_bytes.replace(b"proof-a", b"proof-b", 1)
    assert changed_support != support_bytes
    assert len(changed_support) == len(support_bytes)
    support.write_bytes(changed_support)
    with pytest.raises(SolarParseProofError, match="input"):
        _validate(
            proof_path, rta4_solar_stod_verifier, tmp_path,
            system, support, solar,
        )
    support.write_bytes(support_bytes)

    solar_bytes = solar.read_bytes()
    changed_solar = solar_bytes.replace(b"216.05", b"216.06", 1)
    assert len(changed_solar) == len(solar_bytes)
    solar.write_bytes(changed_solar)
    with pytest.raises(SolarParseProofError, match="input"):
        _validate(
            proof_path, rta4_solar_stod_verifier, tmp_path,
            system, support, solar,
        )
    solar.write_bytes(solar_bytes)
    assert _validate(
        proof_path, rta4_solar_stod_verifier, tmp_path,
        system, support, solar,
    ) == proof


def test_verifier_source_and_binary_identity_drift_is_rejected(
    tmp_path, rta4_solar_stod_verifier,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    proof, proof_path = _proof(
        tmp_path, rta4_solar_stod_verifier, system, support, solar,
    )

    changed = deepcopy(proof)
    changed["parser"]["verifier_source"]["sha256"] = "0" * 64
    _rewrite_proof(proof_path, changed)
    with pytest.raises(SolarParseProofError, match="verifier source"):
        _validate(
            proof_path, rta4_solar_stod_verifier, tmp_path,
            system, support, solar,
        )

    binary_copy = tmp_path / "verifier-copy"
    binary_copy.write_bytes(rta4_solar_stod_verifier.read_bytes())
    binary_copy.chmod(0o755)
    copied_proof, copied_path = _proof(
        tmp_path,
        binary_copy,
        system,
        support,
        solar,
        name="copied-proof.json",
    )
    binary_bytes = binary_copy.read_bytes()
    binary_copy.write_bytes(
        bytes([binary_bytes[0] ^ 1]) + binary_bytes[1:]
    )
    assert binary_copy.stat().st_size == copied_proof["parser"][
        "verifier_binary"
    ]["size"]
    with pytest.raises(SolarParseProofError, match="verifier binary"):
        _validate(
            copied_path, binary_copy, tmp_path, system, support, solar,
        )


def test_phase_and_horizon_drift_reject_stale_proof(
    tmp_path, rta4_solar_stod_verifier,
):
    system, support, solar = _case(
        tmp_path, ("216.05", "216.05"), horizon=60_000,
    )
    _proof_value, proof_path = _proof(
        tmp_path, rta4_solar_stod_verifier, system, support, solar,
        horizon=1,
    )
    with pytest.raises(SolarParseProofError, match="input"):
        _validate(
            proof_path, rta4_solar_stod_verifier, tmp_path,
            system, support, solar, horizon=60_000,
        )

    shifted = tmp_path / "shifted-system.yml"
    _write_system(shifted, solar, time_of_day_ms=60_000)
    shifted_support = tmp_path / "shifted-support.yml"
    _write_support(shifted_support, shifted)
    with pytest.raises(SolarParseProofError, match="input"):
        _validate(
            proof_path, rta4_solar_stod_verifier, tmp_path,
            shifted, shifted_support, solar, time_of_day_ms=60_000,
        )


def test_source_change_during_replay_fails_closed(
    tmp_path, rta4_solar_stod_verifier, monkeypatch,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    _proof_value, proof_path = _proof(
        tmp_path, rta4_solar_stod_verifier, system, support, solar,
    )
    production_replay = simulation_engine.construct_paired_harvest_trace

    def mutate_after_replay(system_path, horizon):
        trace = production_replay(system_path, horizon)
        payload = solar.read_bytes()
        solar.write_bytes(payload.replace(b"216.05", b"216.06", 1))
        return trace

    monkeypatch.setattr(
        simulation_engine,
        "construct_paired_harvest_trace",
        mutate_after_replay,
    )
    with pytest.raises(
        SimulationConfigurationError, match="changed during verified replay",
    ):
        construct_shared_solar_input(
            system,
            support,
            horizon=1,
            source_root=tmp_path,
            solar_parse_proof=proof_path,
            solar_parse_verifier_binary=rta4_solar_stod_verifier,
        )


def test_check_mode_reconstructs_exact_canonical_material(
    tmp_path, rta4_solar_stod_verifier,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    _value, proof_path = _proof(
        tmp_path, rta4_solar_stod_verifier, system, support, solar,
    )
    arguments = [
        "--source-root", str(tmp_path),
        "--base-system", str(system),
        "--energy-support", str(support),
        "--solar-csv", str(solar),
        "--day-of-year", "1",
        "--time-of-day-ms", "0",
        "--horizon", "1",
        "--verifier-binary", str(rta4_solar_stod_verifier),
        "--output", str(proof_path),
        "--check",
    ]
    assert proof_cli.main(arguments) == 0
    proof_path.write_bytes(proof_path.read_bytes() + b" ")
    assert proof_cli.main(arguments) == 1


def test_canonical_proof_matches_production_replay_and_builds_beta(
    tmp_path, rta4_solar_stod_verifier,
):
    system = legacy_rta.load_system_config(str(BASE_SYSTEM))
    proof = build_solar_stod_parse_proof(
        source_root=ROOT,
        base_system_path=BASE_SYSTEM,
        energy_support=CANONICAL_SUPPORT,
        solar_csv_path=CANONICAL_SOLAR,
        day_of_year=system.day_of_year,
        time_of_day_ms=system.time_of_day_ms,
        horizon=30_000,
        verifier_binary=rta4_solar_stod_verifier,
        build_verifier=False,
    )
    proof_path = tmp_path / "canonical-proof.json"
    write_solar_stod_parse_proof(proof_path, proof)
    shared = construct_shared_solar_input(
        BASE_SYSTEM,
        CANONICAL_SUPPORT,
        horizon=30_000,
        source_root=ROOT,
        solar_parse_proof=proof_path,
        solar_parse_verifier_binary=rta4_solar_stod_verifier,
    )
    support = yaml.safe_load(CANONICAL_SUPPORT.read_text(encoding="utf-8"))
    projected_system, _taskset = materialize_simulation_inputs(
        BASE_SYSTEM,
        tmp_path / "projected",
        (),
        processors=4,
        initial_battery=Fraction(support["simulation_initial_battery"]),
        battery_capacity=Fraction(support["battery_capacity"]),
        service_curve=support["service_curve"],
    )
    expected = construct_paired_harvest_trace(projected_system, 30_000)
    assert len(shared.harvest_j_per_tick) == len(expected) == 30_000
    assert all(
        observed == reference
        for observed, reference in zip(shared.harvest_j_per_tick, expected)
    )
    beta = shared.beta(3)
    assert len(beta) == 4
    assert beta[0] == 0
    assert beta[-1] == sum(shared.harvest_j_per_tick[:3])


def test_repository_negative_sentinel_never_produces_proof_trace_or_beta(
    tmp_path, rta4_solar_stod_verifier,
):
    physical_rows = CANONICAL_SOLAR.read_text(
        encoding="utf-8"
    ).splitlines()[1:]
    negative_index = next(
        index for index, value in enumerate(physical_rows)
        if float(value) < 0
    )
    system = tmp_path / "negative-system.yml"
    support = tmp_path / "negative-support.yml"
    _write_system(
        system,
        CANONICAL_SOLAR,
        day_of_year=negative_index // 1440 + 1,
        time_of_day_ms=(negative_index % 1440) * 60_000,
    )
    _write_support(support, system, horizon=1)
    with pytest.raises(
        SolarParseProofError, match="NEGATIVE_ACCESSED_IRRADIANCE",
    ):
        build_solar_stod_parse_proof(
            source_root=ROOT,
            base_system_path=system,
            energy_support=support,
            solar_csv_path=CANONICAL_SOLAR,
            day_of_year=negative_index // 1440 + 1,
            time_of_day_ms=(negative_index % 1440) * 60_000,
            horizon=1,
            verifier_binary=rta4_solar_stod_verifier,
            build_verifier=False,
        )
