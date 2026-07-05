import asap_block_rta as v20


def make_task():
    return v20.RTATask(
        "target",
        period=10,
        wcet=2,
        deadline=10,
        workload="fixed",
        yaml_index=0,
        energy_per_tick=0.1,
    )


def test_all_empty_energy_states_are_unproven(monkeypatch):
    task = make_task()
    monkeypatch.setattr(
        v20,
        "_deadline_energy_states_for_z",
        lambda *args, **kwargs: [],
    )

    result = v20.response_time_bound(
        task,
        tasks=[task],
        M=1,
        beta=[0.0] + [100.0] * 10,
        E0=1.0,
        assume_no_overflow=True,
        certified_bounds={},
    )

    assert not result.proven
    assert result.response_time_bound is None
    assert result.failure_reason == (
        "no feasible energy state for the candidate response window"
    )


def test_normal_feasible_energy_state_result_is_unchanged():
    task = make_task()
    result = v20.response_time_bound(
        task,
        tasks=[task],
        M=1,
        beta=[0.0] + [100.0] * 10,
        E0=1.0,
        assume_no_overflow=True,
        certified_bounds={},
    )

    assert result.proven
    assert result.response_time_bound == task.wcet
    assert result.failure_reason is None
