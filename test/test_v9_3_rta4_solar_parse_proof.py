from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import inspect
import json
import math
from pathlib import Path
import stat
import struct

import pytest
import yaml

import asap_block_rta as legacy_rta
from experiments.v9_3 import solar_parse_proof as proof_module
from experiments.v9_3.config import canonical_json, domain_hash
from experiments.v9_3.simulation_engine import (
    SimulationConfigurationError,
    construct_paired_harvest_trace,
    construct_shared_solar_input,
    materialize_simulation_inputs,
)
from experiments.v9_3.solar_parse_proof import (
    ImmutableSolarReplaySnapshot,
    SOLAR_PARSER_ENVIRONMENT_DOMAIN,
    SOLAR_SEMANTIC_SERVICE_SOURCE_DOMAIN,
    SOLAR_STOD_PARSE_PROOF_DOMAIN,
    SolarParseProofError,
    build_diagnostic_untrusted_solar_stod_parse_proof,
    build_solar_stod_parse_proof,
    inspect_solar_rows_diagnostic_untrusted,
    thaw_material,
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
VERIFIER_SOURCE = ROOT / "tools/rta4_solar_stod_verifier.cpp"


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
    )
    path = tmp_path / name
    write_solar_stod_parse_proof(path, value)
    return thaw_material(value), path


def _validate(
    proof_path: Path,
    root: Path,
    system: Path,
    support: Path,
    solar: Path,
    *,
    day_of_year: int = 1,
    time_of_day_ms: int = 0,
    horizon: int = 1,
) -> dict:
    return thaw_material(validate_solar_stod_parse_proof(
        proof_path,
        source_root=root,
        base_system_path=system,
        energy_support=support,
        solar_csv_path=solar,
        day_of_year=day_of_year,
        time_of_day_ms=time_of_day_ms,
        horizon=horizon,
    ))


def _rewrite_all_identities(proof: dict) -> None:
    semantic = proof["semantic_service_source"]
    proof["semantic_service_source_identity"] = domain_hash(
        SOLAR_SEMANTIC_SERVICE_SOURCE_DOMAIN, semantic,
    )
    environment = proof["parser_environment"]
    proof["parser_environment_identity"] = domain_hash(
        SOLAR_PARSER_ENVIRONMENT_DOMAIN, environment,
    )
    material = {
        key: value
        for key, value in proof.items()
        if key not in {"proof_id", "live_proof_identity"}
    }
    live = domain_hash(SOLAR_STOD_PARSE_PROOF_DOMAIN, material)
    proof["live_proof_identity"] = live
    proof["proof_id"] = live


@pytest.mark.parametrize("text", ("216.05", "2.1605e2", " 216.05 "))
def test_successful_stod_proof_requires_exact_python_cpp_bits(
    tmp_path, text,
):
    system, support, solar = _case(tmp_path, (text,))
    proof, proof_path = _proof(tmp_path, system, support, solar)
    row = proof["semantic_service_source"]["rows"][0]
    expected = struct.pack(">d", float(text.strip())).hex()
    assert row["parse_status"] == "success"
    assert row["binary64_bits"] == expected
    assert row["python_binary64_bits"] == expected
    assert row["raw_text_sha256"] == hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()
    assert _validate(
        proof_path, tmp_path, system, support, solar,
    ) == proof

    shared = construct_shared_solar_input(
        system,
        support,
        horizon=1,
        source_root=tmp_path,
        solar_parse_proof=proof_path,
    )
    assert shared.harvest_j_per_tick == construct_paired_harvest_trace(
        system, 1,
    )
    assert shared.beta(1)[1] == shared.harvest_j_per_tick[0]
    assert shared.provenance["solar_stod_parser_binding"]["proof_id"] == (
        proof["proof_id"]
    )


def test_trailing_text_is_rejected_even_when_stod_consumes_a_prefix(
    tmp_path, rta4_solar_stod_diagnostic_untrusted_verifier,
):
    system, support, solar = _case(tmp_path, ("216.05tail",))
    inspected = inspect_solar_rows_diagnostic_untrusted(
        rta4_solar_stod_diagnostic_untrusted_verifier,
        solar,
        first_row=0,
        last_row=0,
    )
    assert inspected["rows"][0]["parse_status"] == "success"
    assert inspected["rows"][0]["consumed_characters"] < (
        inspected["rows"][0]["raw_text_length"]
    )
    with pytest.raises(SolarParseProofError, match="INVALID_NUMERIC_ROW"):
        _proof(tmp_path, system, support, solar)


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
    tmp_path, rta4_solar_stod_diagnostic_untrusted_verifier, text,
):
    system, support, solar = _case(tmp_path, (text,))
    inspected = inspect_solar_rows_diagnostic_untrusted(
        rta4_solar_stod_diagnostic_untrusted_verifier,
        solar,
        first_row=0,
        last_row=0,
    )
    row = inspected["rows"][0]
    python_value = float(text)
    python_acceptable = math.isfinite(python_value) and python_value >= 0
    cpp_acceptable = (
        inspected["returncode"] == 0
        and row["parse_status"] == "success"
        and row["finite"] is True
        and row["negative"] is False
        and row["binary64_bits"] == struct.pack(">d", python_value).hex()
    )
    if python_acceptable and cpp_acceptable:
        proof, proof_path = _proof(tmp_path, system, support, solar)
        assert _validate(
            proof_path, tmp_path, system, support, solar,
        ) == proof
    else:
        with pytest.raises(SolarParseProofError):
            _proof(tmp_path, system, support, solar)


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
    tmp_path, text, category,
):
    system, support, solar = _case(tmp_path, (text,))
    with pytest.raises(SolarParseProofError, match=category):
        _proof(tmp_path, system, support, solar)


def test_missing_row_fails_but_expected_proof_is_optional(tmp_path):
    system, support, solar = _case(
        tmp_path, ("1",), time_of_day_ms=60_000,
    )
    with pytest.raises(SolarParseProofError, match="INSUFFICIENT"):
        _proof(
            tmp_path,
            system,
            support,
            solar,
            time_of_day_ms=60_000,
        )

    ordinary_system, ordinary_support, _ordinary_solar = _case(
        tmp_path, ("1",),
    )
    shared = construct_shared_solar_input(
        ordinary_system,
        ordinary_support,
        horizon=1,
        source_root=tmp_path,
    )
    assert len(shared.harvest_j_per_tick) == 1
    assert shared.provenance["live_solar_stod_parse_proof"]["proof_id"]
    assert shared.provenance["expected_solar_stod_parse_proof"] is None


def test_expected_proof_row_hash_bits_and_json_mutations_fail_closed(
    tmp_path,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    proof, proof_path = _proof(tmp_path, system, support, solar)

    for field, replacement in (
        ("raw_text_sha256", "0" * 64),
        ("binary64_bits", "0" * 16),
        ("python_binary64_bits", "0" * 16),
    ):
        changed = deepcopy(proof)
        changed["semantic_service_source"]["rows"][0][field] = replacement
        _rewrite_all_identities(changed)
        write_solar_stod_parse_proof(proof_path, changed)
        with pytest.raises(SolarParseProofError, match="fresh internal"):
            _validate(
                proof_path, tmp_path, system, support, solar,
            )

    write_solar_stod_parse_proof(proof_path, proof)
    proof_path.write_bytes(proof_path.read_bytes() + b" ")
    with pytest.raises(SolarParseProofError, match="canonical"):
        _validate(proof_path, tmp_path, system, support, solar)


def _same_size_comment_mutation(payload: bytes) -> bytes:
    marker = b"NASA"
    assert marker in payload
    return payload.replace(marker, b"NASB", 1)


def test_same_size_system_support_and_csv_drift_rejects_stale_expectation(
    tmp_path,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    proof, proof_path = _proof(tmp_path, system, support, solar)

    mutations = (
        (
            system,
            lambda value: _same_size_comment_mutation(value),
        ),
        (
            support,
            lambda value: value.replace(b"proof-a", b"proof-b", 1),
        ),
        (
            solar,
            lambda value: value.replace(b"216.05", b"216.06", 1),
        ),
    )
    for path, mutate in mutations:
        original = path.read_bytes()
        changed = mutate(original)
        assert changed != original
        assert len(changed) == len(original)
        path.write_bytes(changed)
        with pytest.raises(SolarParseProofError, match="fresh internal"):
            _validate(proof_path, tmp_path, system, support, solar)
        path.write_bytes(original)

    assert _validate(
        proof_path, tmp_path, system, support, solar,
    ) == proof


def test_formal_safe_signatures_have_no_caller_binary_or_build_bypass(
    tmp_path,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    proof_signature = inspect.signature(build_solar_stod_parse_proof)
    validation_signature = inspect.signature(
        validate_solar_stod_parse_proof
    )
    shared_signature = inspect.signature(construct_shared_solar_input)
    for signature in (
        proof_signature, validation_signature, shared_signature,
    ):
        assert "verifier_binary" not in signature.parameters
        assert "solar_parse_verifier_binary" not in signature.parameters
        assert "build_verifier" not in signature.parameters

    with pytest.raises(TypeError):
        build_solar_stod_parse_proof(
            source_root=tmp_path,
            base_system_path=system,
            energy_support=support,
            solar_csv_path=solar,
            day_of_year=1,
            time_of_day_ms=0,
            horizon=1,
            verifier_binary=tmp_path / "fake",
        )
    with pytest.raises(TypeError):
        construct_shared_solar_input(
            system,
            support,
            horizon=1,
            source_root=tmp_path,
            build_verifier=False,
        )


def _write_fake_verifier(path: Path) -> None:
    identity = {
        "record_type": "identity",
        "parser_contract_version": (
            proof_module.SOLAR_STOD_PARSER_CONTRACT
        ),
        "numeric_locale": proof_module.SOLAR_STOD_NUMERIC_LOCALE,
        "cpp_standard": 201703,
        "standard_library": "fake-stdlib",
        "libc": "fake-libc",
        "double_is_iec559": True,
        "double_size_bytes": 8,
    }
    row = {
        "record_type": "row",
        "physical_data_row_index": 0,
        "file_physical_line_number": 2,
        "parse_status": "success",
        "exception_category": "none",
        "finite": True,
        "negative": False,
        "binary64_bits": struct.pack(">d", 216.05).hex(),
        "consumed_characters": 6,
        "raw_text_length": 6,
    }
    path.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' '{canonical_json(identity)}'\n"
        f"printf '%s\\n' '{canonical_json(row)}'\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_fake_executable_and_its_self_consistent_proof_are_not_formal_safe(
    tmp_path,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    fake = tmp_path / "fake-verifier"
    _write_fake_verifier(fake)
    diagnostic = build_diagnostic_untrusted_solar_stod_parse_proof(
        source_root=tmp_path,
        base_system_path=system,
        energy_support=support,
        solar_csv_path=solar,
        day_of_year=1,
        time_of_day_ms=0,
        horizon=1,
        verifier_binary=fake,
        build_verifier=False,
    )
    assert diagnostic["classification"] == "UNVERIFIED_DIAGNOSTIC_REPLAY"
    fake_proof = tmp_path / "fake-proof.json"
    write_solar_stod_parse_proof(fake_proof, diagnostic)

    with pytest.raises(SolarParseProofError, match="fresh internal"):
        build_solar_stod_parse_proof(
            source_root=tmp_path,
            base_system_path=system,
            energy_support=support,
            solar_csv_path=solar,
            day_of_year=1,
            time_of_day_ms=0,
            horizon=1,
            expected_proof_path=fake_proof,
        )
    with pytest.raises(
        SimulationConfigurationError, match="fresh internal",
    ):
        construct_shared_solar_input(
            system,
            support,
            horizon=1,
            source_root=tmp_path,
            solar_parse_proof=fake_proof,
        )


def test_expected_binary_path_or_synchronized_environment_forgery_rejects(
    tmp_path,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    proof, proof_path = _proof(tmp_path, system, support, solar)

    changed = deepcopy(proof)
    changed["parser_environment"]["verifier_binary"]["path"] = (
        str(tmp_path / "attacker")
    )
    changed["parser_environment"]["verifier_binary"]["sha256"] = "1" * 64
    changed["parser_environment"]["verifier_source"]["sha256"] = "2" * 64
    changed["parser_environment"]["compiler"]["sha256"] = "3" * 64
    changed["parser_environment"]["compiler_version"] = "attacker compiler"
    _rewrite_all_identities(changed)
    write_solar_stod_parse_proof(proof_path, changed)
    with pytest.raises(SolarParseProofError, match="fresh internal"):
        _validate(proof_path, tmp_path, system, support, solar)


def test_live_proof_is_checked_in_source_and_temp_directory_independent(
    tmp_path,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    first = thaw_material(build_solar_stod_parse_proof(
        source_root=tmp_path,
        base_system_path=system,
        energy_support=support,
        solar_csv_path=solar,
        day_of_year=1,
        time_of_day_ms=0,
        horizon=1,
    ))
    second = thaw_material(build_solar_stod_parse_proof(
        source_root=tmp_path,
        base_system_path=system,
        energy_support=support,
        solar_csv_path=solar,
        day_of_year=1,
        time_of_day_ms=0,
        horizon=1,
    ))
    assert first == second
    assert first["semantic_service_source_identity"] == (
        second["semantic_service_source_identity"]
    )
    assert first["parser_environment_identity"] == (
        second["parser_environment_identity"]
    )
    assert first["live_proof_identity"] == second["live_proof_identity"]
    rendered = canonical_json(first)
    assert "v9_3_verified_solar_" not in rendered
    source = first["parser_environment"]["verifier_source"]
    assert source["source_relative_path"] == (
        "tools/rta4_solar_stod_verifier.cpp"
    )
    assert source["sha256"] == hashlib.sha256(
        VERIFIER_SOURCE.read_bytes()
    ).hexdigest()
    compiler = first["parser_environment"]["compiler"]
    assert Path(compiler["absolute_path"]).is_absolute()
    assert compiler["absolute_path"] not in canonical_json(
        first["semantic_service_source"]
    )


def test_compile_arguments_change_only_environment_and_live_identity(
    tmp_path, monkeypatch,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    baseline = thaw_material(build_solar_stod_parse_proof(
        source_root=tmp_path,
        base_system_path=system,
        energy_support=support,
        solar_csv_path=solar,
        day_of_year=1,
        time_of_day_ms=0,
        horizon=1,
    ))
    original_arguments = proof_module._verifier_compile_arguments

    def changed_arguments(source, binary):
        arguments = original_arguments(source, binary)
        return [*arguments[:-2], "-DASAP_RTA4_TEST_VARIANT=1", *arguments[-2:]]

    normalized = list(proof_module.SOLAR_STOD_NORMALIZED_COMPILE_ARGUMENTS)
    normalized.insert(-2, "-DASAP_RTA4_TEST_VARIANT=1")
    monkeypatch.setattr(
        proof_module, "_verifier_compile_arguments", changed_arguments,
    )
    monkeypatch.setattr(
        proof_module,
        "SOLAR_STOD_NORMALIZED_COMPILE_ARGUMENTS",
        tuple(normalized),
    )
    changed = thaw_material(build_solar_stod_parse_proof(
        source_root=tmp_path,
        base_system_path=system,
        energy_support=support,
        solar_csv_path=solar,
        day_of_year=1,
        time_of_day_ms=0,
        horizon=1,
    ))
    assert baseline["semantic_service_source_identity"] == (
        changed["semantic_service_source_identity"]
    )
    assert baseline["parser_environment_identity"] != (
        changed["parser_environment_identity"]
    )
    assert baseline["live_proof_identity"] != changed["live_proof_identity"]


def test_same_size_checked_in_verifier_source_mutation_is_rejected(
    tmp_path, monkeypatch,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    original_read = proof_module._read_file_once

    def mutated_read(path, *, label):
        payload = original_read(path, label=label)
        if Path(path).resolve() == VERIFIER_SOURCE.resolve():
            marker = b"std::stod"
            assert marker in payload
            return payload.replace(marker, b"std::stoe", 1)
        return payload

    monkeypatch.setattr(proof_module, "_read_file_once", mutated_read)
    with pytest.raises(SolarParseProofError, match="checked-in HEAD blob"):
        build_solar_stod_parse_proof(
            source_root=tmp_path,
            base_system_path=system,
            energy_support=support,
            solar_csv_path=solar,
            day_of_year=1,
            time_of_day_ms=0,
            horizon=1,
        )


def test_snapshot_workspace_permissions_and_system_csv_binding(tmp_path):
    system, support, solar = _case(tmp_path, ("216.05",))
    with ImmutableSolarReplaySnapshot(
        source_root=tmp_path,
        base_system_path=system,
        energy_support_path=support,
        declared_solar_csv_path=solar,
    ) as snapshot:
        assert stat.S_IMODE(snapshot.workspace.stat().st_mode) == 0o700
        for path in (
            snapshot.system_source_path,
            snapshot.support_path,
            snapshot.solar_csv_path,
            snapshot.verifier_source_path,
            snapshot.system_path,
        ):
            assert stat.S_IMODE(path.stat().st_mode) == 0o444
        loaded = legacy_rta.load_system_config(str(snapshot.system_path))
        assert Path(
            legacy_rta._resolve_solar_path(loaded)
        ).resolve(strict=True) == snapshot.solar_csv_path.resolve(strict=True)
        workspace = snapshot.workspace
    assert not workspace.exists()


def test_phase_and_horizon_drift_reject_stale_proof(tmp_path):
    system, support, solar = _case(
        tmp_path, ("216.05", "216.05"), horizon=60_000,
    )
    _proof_value, proof_path = _proof(
        tmp_path, system, support, solar, horizon=1,
    )
    with pytest.raises(SolarParseProofError, match="fresh internal"):
        _validate(
            proof_path,
            tmp_path,
            system,
            support,
            solar,
            horizon=60_000,
        )

    shifted = tmp_path / "shifted-system.yml"
    _write_system(shifted, solar, time_of_day_ms=60_000)
    shifted_support = tmp_path / "shifted-support.yml"
    _write_support(shifted_support, shifted)
    with pytest.raises(SolarParseProofError, match="fresh internal"):
        _validate(
            proof_path,
            tmp_path,
            shifted,
            shifted_support,
            solar,
            time_of_day_ms=60_000,
        )


def test_check_mode_reconstructs_exact_canonical_material(tmp_path):
    system, support, solar = _case(tmp_path, ("216.05",))
    _value, proof_path = _proof(tmp_path, system, support, solar)
    arguments = [
        "--source-root", str(tmp_path),
        "--base-system", str(system),
        "--energy-support", str(support),
        "--solar-csv", str(solar),
        "--day-of-year", "1",
        "--time-of-day-ms", "0",
        "--horizon", "1",
        "--output", str(proof_path),
        "--check",
    ]
    assert proof_cli.main(arguments) == 0
    proof_path.write_bytes(proof_path.read_bytes() + b" ")
    assert proof_cli.main(arguments) == 1


def test_canonical_proof_matches_production_replay_and_builds_beta(tmp_path):
    system = legacy_rta.load_system_config(str(BASE_SYSTEM))
    proof = build_solar_stod_parse_proof(
        source_root=ROOT,
        base_system_path=BASE_SYSTEM,
        energy_support=CANONICAL_SUPPORT,
        solar_csv_path=CANONICAL_SOLAR,
        day_of_year=system.day_of_year,
        time_of_day_ms=system.time_of_day_ms,
        horizon=30_000,
    )
    proof_path = tmp_path / "canonical-proof.json"
    write_solar_stod_parse_proof(proof_path, proof)
    shared = construct_shared_solar_input(
        BASE_SYSTEM,
        CANONICAL_SUPPORT,
        horizon=30_000,
        source_root=ROOT,
        solar_parse_proof=proof_path,
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
    assert shared.harvest_j_per_tick == expected
    beta = shared.beta(3)
    assert len(beta) == 4
    assert beta[0] == 0
    assert beta[-1] == sum(shared.harvest_j_per_tick[:3])


def test_repository_negative_sentinel_never_produces_proof(tmp_path):
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
        )


def test_live_proof_and_snapshot_material_are_recursively_immutable(
    tmp_path,
):
    system, support, solar = _case(tmp_path, ("216.05",))
    proof = build_solar_stod_parse_proof(
        source_root=tmp_path,
        base_system_path=system,
        energy_support=support,
        solar_csv_path=solar,
        day_of_year=1,
        time_of_day_ms=0,
        horizon=1,
    )
    with pytest.raises(TypeError):
        proof["proof_id"] = "changed"
    with pytest.raises(TypeError):
        proof["parser_environment"]["compiler"]["sha256"] = "changed"
    with pytest.raises(AttributeError):
        proof["semantic_service_source"]["rows"].append({})
    thawed = thaw_material(proof)
    thawed["proof_id"] = "copy-only"
    assert proof["proof_id"] != "copy-only"
