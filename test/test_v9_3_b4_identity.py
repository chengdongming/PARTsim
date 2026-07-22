import pytest

from experiments.v9_3.performance_identity import (
    audit_gate_formal_relationship, execution_identity, semantic_config_hash,
    semantic_request_id, trace_sample_selected,
)


def request(**updates):
    values = dict(
        contract_version="v1", taskset_semantic_hash="t",
        energy_identity_value="e", scheduler_id="gpfp_asap_block",
        runtime_horizon_ms=30000, simulation_semantic_config_hash="c",
    )
    values.update(updates)
    return semantic_request_id(**values)


def test_semantic_and_execution_identity_boundaries():
    assert semantic_config_hash({"scientific": 1}) == semantic_config_hash({"scientific": 1})
    with pytest.raises(ValueError, match="execution-only"):
        semantic_config_hash({"scientific": 1, "output_path": "/tmp/a"})
    base = request()
    assert request(runtime_horizon_ms=60000) != base
    assert request(scheduler_id="gpfp_asap_sync") != base
    assert execution_identity(base, "a", "bin") != execution_identity(base, "b", "bin")
    assert execution_identity(base, "a", "bin") != execution_identity(base, "a", "bin2")


def test_gate_subset_and_trace_sample_are_deterministic():
    audit_gate_formal_relationship({"a"}, {"x"}, {"a", "b"})
    with pytest.raises(ValueError):
        audit_gate_formal_relationship({"a"}, {"b"}, {"a", "b"})
    values = [trace_sample_selected(str(index)) for index in range(100)]
    assert values == [trace_sample_selected(str(index)) for index in range(100)]
    assert 0 < sum(values) < 20
