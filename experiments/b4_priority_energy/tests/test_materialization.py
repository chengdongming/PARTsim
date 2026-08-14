from pathlib import Path

from experiments.b4_priority_energy.experiment import iter_requests, materialize_request


def test_direct_materialization_is_idempotent(tmp_path: Path):
    request = next(iter_requests(("pilot",)))
    cache = {}
    first = materialize_request(request, tmp_path, cache)
    second = materialize_request(request, tmp_path, cache)
    assert first["case_id"] == second["case_id"]
    assert first["taskset_sha256"] == second["taskset_sha256"]
    assert first["source_sha256"] == second["source_sha256"]
    assert first["system_sha256"] == second["system_sha256"]
