import itertools
from fractions import Fraction

import pytest
import yaml

import asap_block_rta as v20
import asap_block_rta_v21_local_window as v21


def make_task(name, period, wcet, energy, index, deadline=None):
    return v20.RTATask(
        name,
        period,
        wcet,
        period if deadline is None else deadline,
        "fixed",
        index,
        energy,
    )


def tiny_taskset():
    tasks = [
        make_task("h1", 7, 2, 0.3, 0),
        make_task("h2", 11, 2, 0.7, 1),
        make_task("k", 19, 3, 0.5, 2),
        make_task("l1", 23, 2, 0.9, 3),
        make_task("l2", 29, 1, 0.4, 4),
    ]
    return tasks, tasks[2], {"h1": 4, "h2": 5}


def brute_extrema(target, tasks, x, delta, z, processors, certified):
    local_length = x + delta
    high = v20.hp(tasks, target)
    low = v20._lp(tasks, target)
    high_limits = [
        min(
            v20.workload_bound(
                task, local_length, certified[task.name]
            ),
            local_length,
        )
        for task in high
    ]
    low_limits = [
        min(v20.workload_bound(task, local_length), z) for task in low
    ]
    high_choices = []
    for limit in high_limits:
        choices = []
        for a_value in range(min(limit, x - z) + 1):
            for b_value in range(min(limit - a_value, z) + 1):
                for u_value in range(limit - a_value - b_value + 1):
                    choices.append((a_value, b_value, u_value))
        high_choices.append(choices)

    best_u = None
    best_energy = None
    best_u_state = None
    best_energy_state = None
    for high_state in itertools.product(*high_choices):
        if sum(value[0] for value in high_state) != processors * (x - z):
            continue
        if sum(value[2] for value in high_state) > processors * delta:
            continue
        for low_state in itertools.product(
            *(range(limit + 1) for limit in low_limits)
        ):
            if (
                sum(value[1] for value in high_state) + sum(low_state)
                > (processors - 1) * z
            ):
                continue
            u_total = sum(value[2] for value in high_state)
            energy = Fraction(z) * v20._energy_fraction(target)
            energy += sum(
                Fraction(sum(value)) * v20._energy_fraction(task)
                for value, task in zip(high_state, high)
            )
            energy += sum(
                Fraction(value) * v20._energy_fraction(task)
                for value, task in zip(low_state, low)
            )
            if best_u is None or u_total > best_u:
                best_u = u_total
                best_u_state = (high_state, low_state)
            if best_energy is None or energy > best_energy:
                best_energy = energy
                best_energy_state = (high_state, low_state)
    return best_u, best_energy, best_u_state, best_energy_state


def test_v21_is_isolated_from_frozen_v20p4():
    assert v20.RTA_VERSION == "v20.4"
    assert v21.RTA_VERSION == "v21-local-window"
    task = make_task("t", 10, 2, 0.1, 0)
    result = v20.response_time_bound(
        task,
        [task],
        M=2,
        beta=[0.0] + [100.0] * 10,
        E0=1.0,
        assume_no_overflow=True,
        certified_bounds={},
    )
    assert result.proven
    assert result.response_time_bound == 2


def test_local_workload_uses_x_plus_delta_not_full_w():
    task = make_task("h", 10, 3, 0.2, 0)
    local = v21.local_workload_bound(task, 2, theta=5)
    full = v21.local_workload_bound(task, 12, theta=5)
    assert local == v20.workload_bound(task, 2, 5)
    assert local < full
    assert v21.local_workload_bound(task, 0, theta=3) == 0
    assert v21.local_workload_bound(task, 4, theta=3) <= v21.local_workload_bound(
        task, 5, theta=3
    )


def test_processor_reference_keeps_full_w_and_plus_one():
    high = make_task("h", 10, 4, 0.2, 0)
    target = make_task("k", 20, 3, 0.2, 1)
    tasks = [high, target]
    reference, delay = v21.processor_reference_length(
        target, 3, 1, tasks, {"h": 4}
    )
    # w-Ck+1 == 1 permits one unit of effective interference.
    assert delay == 1
    assert reference == 4


def test_local_omega_flow_matches_complete_tiny_enumeration():
    tasks, target, certified = tiny_taskset()
    for x in range(1, 6):
        for delta in range(3):
            for z in range(min(target.wcet, x) + 1):
                expected = brute_extrema(
                    target, tasks, x, delta, z, 2, certified
                )
                actual = v21.local_omega_extrema_for_z(
                    target, tasks, 10, x, delta, z, 2, certified
                )
                assert actual.feasible == (expected[0] is not None)
                if actual.feasible:
                    assert actual.max_u == expected[0]
                    assert actual.max_energy == expected[1]


def test_local_u_capacity_excludes_v20_window_slack():
    tasks, target, certified = tiny_taskset()
    result = v21.local_omega_extrema_for_z(
        target, tasks, w=12, x=3, delta=0, z=1,
        processors=2, certified_bounds=certified,
    )
    assert result.feasible
    assert result.max_u == 0
    # Frozen v20.4's full-window cap M*(w-x) would be positive here.
    assert 2 * (12 - 3) > result.max_u


def test_u_is_serial_and_not_divided_by_processors():
    tasks, target, certified = tiny_taskset()
    result = v21.local_omega_extrema_for_z(
        target, tasks, w=12, x=1, delta=1, z=0,
        processors=2, certified_bounds=certified,
    )
    assert result.feasible
    assert result.max_u == 2
    beta = [0.0] + [100.0] * 30
    g_value = v21.local_g(
        target, 12, 1, 1, beta, 100.0, tasks, 2, certified,
        processor_delay_value=1,
    )
    assert g_value.value == 2


def test_max_u_and_max_energy_are_both_exact_objectives():
    tasks, target, certified = tiny_taskset()
    expected = brute_extrema(target, tasks, 3, 2, 1, 2, certified)
    actual = v21.local_omega_extrema_for_z(
        target, tasks, 10, 3, 2, 1, 2, certified
    )
    assert actual.max_u == expected[0]
    assert actual.max_energy == expected[1]
    assert expected[2] != expected[3]


def test_local_g_covers_energy_dominant_objective():
    tasks, target, certified = tiny_taskset()
    beta = [float(index) / 10.0 for index in range(101)]
    result = v21.local_g(
        target, 10, 3, 2, beta, 0.0, tasks, 2, certified,
        processor_delay_value=2,
    )
    extrema = [
        v21.local_omega_extrema_for_z(
            target, tasks, 10, 3, 2, z, 2, certified
        )
        for z in range(1, 4)
    ]
    max_u = max(item.max_u for item in extrema if item.feasible)
    assert result.feasible
    assert result.value > max_u


def test_empty_omega_does_not_close_delta_zero():
    calls = []

    def fake_g(*args, **kwargs):
        delta = args[3]
        calls.append(delta)
        if delta == 0:
            return v21.LocalGResult(False)
        return v21.LocalGResult(True, 1)

    task = make_task("k", 10, 2, 0.1, 0)
    result = v21.close_delta(
        task, 4, 1, 2, [0, 1, 2], 0.0, [task], 1, {},
        g_function=fake_g,
    )
    assert result.closed
    assert result.delta == 1
    assert calls == [0, 1]


def test_close_delta_jumps_without_optimistic_timeout_result():
    calls = []

    def fake_g(*args, **kwargs):
        delta = args[3]
        calls.append(delta)
        return v21.LocalGResult(True, 3)

    task = make_task("k", 10, 2, 0.1, 0)
    result = v21.close_delta(
        task, 5, 1, 3, [0, 1, 2, 3], 0.0, [task], 1, {},
        g_function=fake_g,
    )
    assert result.closed
    assert result.delta == 3
    assert calls == [0, 3]


def test_close_delta_preserves_legacy_g_signature_without_profile():
    task = make_task("k", 10, 2, 0.1, 0)

    def legacy_g(
        target,
        w,
        x,
        delta,
        beta,
        e0,
        tasks,
        processors,
        certified_bounds,
        processor_delay_value=None,
        workload_cache=None,
    ):
        return v21.LocalGResult(True, 0)

    result = v21.close_delta(
        task,
        4,
        1,
        2,
        [0.0, 1.0, 2.0],
        0.0,
        [task],
        1,
        {},
        g_function=legacy_g,
    )

    assert result.closed
    assert result.delta == 0
    assert result.g_value == 0


def test_close_delta_preserves_legacy_g_signature_with_profile():
    task = make_task("k", 10, 2, 0.1, 0)
    profile = v21.V21ClosureProfile(task.name)

    def legacy_g(
        target,
        w,
        x,
        delta,
        beta,
        e0,
        tasks,
        processors,
        certified_bounds,
        processor_delay_value=None,
        workload_cache=None,
    ):
        return v21.LocalGResult(True, 0)

    result = v21.close_delta(
        task,
        4,
        1,
        2,
        [0.0, 1.0, 2.0],
        0.0,
        [task],
        1,
        {},
        g_function=legacy_g,
        closure_profile=profile,
    )

    assert result.closed
    assert result.delta == 0
    assert profile.delta_iterations == 1
    assert profile.closed_prefix_count == 1
    assert all(
        isinstance(value, int) and value >= 0
        for field, value in profile.to_dict().items()
        if field != "task_id"
    )


def test_close_delta_passes_profile_to_supported_g_function():
    task = make_task("k", 10, 2, 0.1, 0)
    profile = v21.V21ClosureProfile(task.name)
    received = []

    def profiled_g(
        target,
        w,
        x,
        delta,
        beta,
        e0,
        tasks,
        processors,
        certified_bounds,
        processor_delay_value=None,
        workload_cache=None,
        closure_profile=None,
    ):
        received.append(closure_profile)
        closure_profile.g_loc_calls += 1
        return v21.LocalGResult(True, 0)

    result = v21.close_delta(
        task,
        4,
        1,
        2,
        [0.0, 1.0, 2.0],
        0.0,
        [task],
        1,
        {},
        g_function=profiled_g,
        closure_profile=profile,
    )

    assert result.closed
    assert result.delta == 0
    assert received == [profile]
    assert profile.g_loc_calls == 1


def test_a_greater_than_w_skips_inner_search(monkeypatch):
    task = make_task("k", 5, 2, 0.1, 0)
    monkeypatch.setattr(
        v21, "processor_reference_length", lambda *args: (args[1] + 1, 1)
    )

    def should_not_run(*args, **kwargs):
        raise AssertionError("inner local search must be skipped")

    monkeypatch.setattr(v21, "local_energy_blocking_bound", should_not_run)
    result = v21.response_time_bound_v21(
        task, [task], 1, [0.0] * 10,
        assume_no_overflow=True, certified_bounds={},
    )
    assert not result.proven
    assert result.failure_reason == "A_k^Theta(w) > w"


def test_missing_certified_hp_blocks_lower_priority_proof():
    high = make_task("h", 5, 1, 0.1, 0)
    low = make_task("l", 10, 1, 0.1, 1)
    result = v21.response_time_bound_v21(
        low, [high, low], 2, [0.0] * 20,
        e0=1.0, assume_no_overflow=True, certified_bounds={},
    )
    assert not result.proven
    assert "not certified" in result.failure_reason


def test_cli_version_and_defaults_are_independent():
    parser = v21.build_argument_parser()
    args = parser.parse_args(
        ["--system", "system.yml", "--tasks", "tasks.yml", "--horizon-ms", "10"]
    )
    assert args.rta_initial_energy == 0.0
    assert not args.profile
    assert v21.RTA_VERSION == "v21-local-window"


def test_v21_profile_schema_and_counters_do_not_change_result(tmp_path):
    system_path = tmp_path / "system.yml"
    system_path.write_text(
        yaml.safe_dump(
            {
                "cpu_islands": [
                    {
                        "name": "island0",
                        "numcpus": 1,
                        "base_freq": 8100,
                    }
                ],
                "energy_management": {
                    "initial_energy": 100.0,
                    "max_energy": 100.0,
                    "use_real_solar_data": False,
                    "base_harvesting_rate": 0.0,
                    "scheduler_energy_model": {
                        "base_power": 1.0,
                        "workload_coefficients": {"fixed": 1.0},
                        "frequency_power_ratios": {8100: 1.0},
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    tasks_path = tmp_path / "tasks.yml"
    tasks_path.write_text(
        yaml.safe_dump(
            {
                "taskset": [
                    {
                        "name": "only",
                        "iat": 10,
                        "runtime": 1,
                        "deadline": 10,
                        "params": (
                            "period=10,wcet=1,arrival_offset=0,workload=fixed"
                        ),
                        "code": ["fixed(1, fixed)"],
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    common = {
        "system_yml": str(system_path),
        "tasks_yml": str(tasks_path),
        "horizon_ms": 20,
        "assume_no_overflow": True,
        "harvest_trace": [0.0] * 20,
        "initial_energy": 100.0,
    }

    plain = v21.analyze_taskset_v21(**common, profile_rta=False)
    profiled = v21.analyze_taskset_v21(**common, profile_rta=True)
    plain_payload = plain.to_dict()
    profiled_payload = profiled.to_dict()

    assert profiled.tasks[0].proven == plain.tasks[0].proven
    assert (
        profiled.tasks[0].response_time_bound
        == plain.tasks[0].response_time_bound
    )
    assert profiled.tasks[0].failure_reason == plain.tasks[0].failure_reason
    assert "rta_profile" not in plain_payload["tasks"][0]

    expected_metadata = {
        "theory_family": "local_window_closure",
        "closure_method": "delta_closure",
        "empty_set_guard": True,
        "fallback_guard": True,
        "consistency_guard": True,
        "certified_carry_in_source": "v21_recursive_certification",
        "uses_local_window": True,
        "uses_delta_closure": True,
        "uses_parallel_u_compression": False,
    }
    for field, expected in expected_metadata.items():
        assert profiled_payload[field] == expected

    closure_profile = profiled_payload["tasks"][0]["rta_profile"]
    for field in (
        "delta_iterations",
        "g_loc_calls",
        "omega_feasibility_calls",
        "empty_omega_count",
        "no_closure_count",
        "closed_prefix_count",
        "delta_cap_exceeded_count",
        "max_delta_cap",
        "max_delta_seen",
        "delta_jump_count",
    ):
        assert isinstance(closure_profile[field], int)
        assert closure_profile[field] >= 0
    assert closure_profile["delta_iterations"] > 0
    assert closure_profile["g_loc_calls"] > 0
    assert closure_profile["omega_feasibility_calls"] > 0
