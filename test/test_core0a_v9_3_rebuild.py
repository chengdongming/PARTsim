from fractions import Fraction

import pytest

import asap_block_rta_v9_3 as core
from core0a_v9_3_build_identity import (
    MUTATION_SANDBOX_SOURCE_FILES,
    ROOT,
    RTA_EXECUTION_SOURCE_FILES,
    SOURCE_FILES,
)
from core0a_v9_3_evidence import finite_domain_instances
from core0a_v9_3_evidence_schema import RAW_TABLES, TABLE_SCHEMAS
from core0a_v9_3_oracles import (
    envelope_reference,
    processor_reference,
    workload_reference,
)
from scripts.core0a_v9_3_mutation_harness import (
    MutationHarnessError,
    SOURCE_MUTATIONS,
    materialize_mutation_source_closure,
    mutation_source_closure_material,
    run_source_mutation,
    validate_materialized_source_closure,
)
from experiments.v9_3.rta4_production_build_manifest import (
    DEFAULT_RELEVANT_SOURCES,
)


def task(name, c, d, t, power=1):
    return core.V93Task(name, c, d, t, Fraction(power))


def test_independent_oracles_match_small_exact_boundary_cases():
    target = task("k", 2, 5, 7, 3)
    hp = (task("h0", 1, 2, 3, 2), task("h1", 2, 4, 5, 1))
    lp = (task("l0", 1, 5, 6, 4),)
    theta = {"h0": 1, "h1": 3}
    for item in hp:
        for length in range(0, 9):
            assert workload_reference(item, length, theta[item.name]) == core.workload_bound_v9_3(
                item, length, theta[item.name]
            )
    for window in range(target.wcet, target.deadline + 1):
        assert processor_reference(target, hp, window, 2, theta) == core.processor_delay_v9_3(
            target, hp, window, 2, theta
        )
    for kind in core.EnvelopeKind:
        for q, h in ((1, 0), (1, 1), (2, 0)):
            expected = envelope_reference(
                kind.value, target, hp, lp, 3, q, h, 2, theta
            )
            actual = core.exact_energy_envelope_v9_3(
                kind, target, hp, lp, 3, q, h, 2, theta
            )
            assert actual == expected


def test_raw_schemas_have_stable_primary_keys_and_identity_fields():
    assert len(RAW_TABLES) == 27
    for name in RAW_TABLES:
        schema = TABLE_SCHEMAS[name]
        assert schema["primary_key"]
        assert set(schema["primary_key"]) <= set(schema["fields"])
        assert "input_hash" in schema["fields"]
        assert "build_identity_hash" in schema["fields"]


def test_finite_state_code_domain_matches_frozen_fourteen_instance_shape():
    instances = finite_domain_instances()
    assert len(instances) == 14
    assert sum(label == "structural" for *_rest, label in instances) == 12
    assert {label for *_rest, label in instances if label != "structural"} == {
        "energy-witness",
        "processor-witness",
    }


def test_build_identity_relevant_source_set_exists():
    assert all((ROOT / name).is_file() for name in SOURCE_FILES)


def test_execution_source_closure_is_shared_by_production_portable_and_mutation():
    required = set(RTA_EXECUTION_SOURCE_FILES)
    assert required <= set(DEFAULT_RELEVANT_SOURCES)
    assert required <= set(SOURCE_FILES)
    assert required <= set(MUTATION_SANDBOX_SOURCE_FILES)


def test_mutation_source_closure_identity_and_blob_validation_fail_closed(tmp_path):
    material = mutation_source_closure_material()
    assert len(material["source_closure_identity"]) == 64
    assert {row["path"] for row in material["files"]} <= set(SOURCE_FILES)

    identity_tampered = dict(material)
    identity_tampered["source_closure_identity"] = "0" * 64
    with pytest.raises(MutationHarnessError, match="identity mismatch"):
        validate_materialized_source_closure(tmp_path, identity_tampered)

    missing = tmp_path / "missing"
    materialize_mutation_source_closure(missing, material)
    (missing / "asap_block_rta_v9_3_methods.py").unlink()
    with pytest.raises(MutationHarnessError, match="missing or escapes"):
        validate_materialized_source_closure(missing, material)

    tampered = tmp_path / "tampered"
    materialize_mutation_source_closure(tampered, material)
    methods = tampered / "asap_block_rta_v9_3_methods.py"
    methods.write_bytes(methods.read_bytes() + b"\n# tampered\n")
    with pytest.raises(MutationHarnessError, match="blob mismatch"):
        validate_materialized_source_closure(tampered, material)


def test_all_ten_source_mutations_hit_and_are_semantically_detected():
    assert len(SOURCE_MUTATIONS) == 10
    rows = [run_source_mutation(spec, "a" * 64) for spec in SOURCE_MUTATIONS]
    assert all(row["mutation_applied"] == "true" for row in rows)
    assert all(row["exit_code"] != "0" for row in rows)
    assert all(row["failure_matches_target"] == "true" for row in rows)
    assert all(row["restored_source_hash"] == row["original_source_hash"] for row in rows)
    assert all(row["detected"] == "true" for row in rows)
