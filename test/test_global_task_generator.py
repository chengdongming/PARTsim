import pytest

import global_task_generator as task_generator


def test_uunifast_discard_uses_standard_zero_based_recurrence(monkeypatch):
    monkeypatch.setattr(task_generator.random, "random", lambda: 0.25)

    utilizations = task_generator.UUniFastDiscard().generate(
        n=2,
        U=1.0,
        min_task_util=0.01,
        max_task_util=0.8,
    )

    assert len(utilizations) == 2
    assert sum(utilizations) == pytest.approx(1.0)
    assert utilizations == pytest.approx([0.75, 0.25])
