from experiments.v9_3.performance_identity import assert_unique_request_ids
import pytest


def test_duplicate_request_identity_fails_closed():
    with pytest.raises(ValueError, match="duplicate"):
        assert_unique_request_ids([
            {"semantic_request_id": "same"}, {"semantic_request_id": "same"},
        ])
