import itertools
import os
import sys
import tempfile
import unittest

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from asap_block_rta import (
    InputValidationError,
    RTATask,
    _build_argument_parser,
    _deadline_energy_states_for_z,
    _format_report,
    _processor_workloads,
    analyze_taskset,
    beta_inverse,
    build_energy_service_curve,
    energy_blocking_bound,
    hp,
    load_system_config,
    load_tasks,
    prefix_energy_upper_bound,
    processor_delay,
    response_time_bound,
    rm_order,
    workload_bound,
)


class ASAPBlockRTATest(unittest.TestCase):
    def write_yaml(self, contents):
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        )
        with handle:
            yaml.safe_dump(contents, handle, sort_keys=False)
        self.addCleanup(
            lambda: os.path.exists(handle.name) and os.unlink(handle.name)
        )
        return handle.name

    def system_file(self, num_cores=1, max_energy=100.0):
        return self.write_yaml(
            {
                "cpu_islands": [
                    {
                        "name": "island0",
                        "numcpus": num_cores,
                        "base_freq": 8100,
                    }
                ],
                "energy_management": {
                    "initial_energy": 99.0,
                    "max_energy": max_energy,
                    "use_real_solar_data": False,
                    "day_of_year": 1,
                    "time_of_day_ms": 0,
                    "scheduler_energy_model": {
                        "base_power": 1.0,
                        "workload_coefficients": {
                            "low": 1.0,
                            "high": 2.0,
                            "very_high": 5.0,
                            "idle": 0.1,
                            "control": 0.1,
                        },
                        "frequency_power_ratios": {8100: 1.0},
                    },
                },
            }
        )

    @staticmethod
    def task_spec(
        name,
        period,
        wcet,
        deadline=None,
        workload="low",
        code=None,
        params=None,
    ):
        if deadline is None:
            deadline = period
        if params is None:
            params = (
                "period={},wcet={},arrival_offset=0,workload={}".format(
                    period, wcet, workload
                )
            )
        if code is None:
            code = ["fixed({}, {})".format(wcet, workload)]
        return {
            "name": name,
            "iat": period,
            "runtime": wcet,
            "deadline": deadline,
            "params": params,
            "code": code,
        }

    def tasks_file(self, specs, resources=None):
        document = {"taskset": specs}
        if resources is not None:
            document["resources"] = resources
        return self.write_yaml(document)

    @staticmethod
    def attach(tasks, cores):
        taskset = tuple(tasks)
        for task in tasks:
            task._taskset = taskset
            task._num_cores = cores
        return tasks

    def test_rm_order_uses_yaml_order_for_equal_periods(self):
        tasks = self.attach(
            [
                RTATask("late", 10, 1, 10, "low", 2),
                RTATask("first_equal", 5, 1, 5, "low", 0),
                RTATask("second_equal", 5, 1, 5, "low", 1),
            ],
            1,
        )
        self.assertEqual(
            [task.name for task in rm_order(tasks)],
            ["first_equal", "second_equal", "late"],
        )
        self.assertEqual(
            [task.name for task in hp(tasks, tasks[0])],
            ["first_equal", "second_equal"],
        )

    def test_load_tasks_accepts_consistent_single_fixed_task(self):
        path = self.tasks_file([self.task_spec("t0", 10, 3)])
        task = load_tasks(path)[0]
        self.assertEqual((task.period, task.wcet, task.deadline), (10, 3, 10))
        self.assertEqual(task.workload, "low")

    def test_load_tasks_rejects_field_mismatch(self):
        spec = self.task_spec(
            "t0",
            10,
            3,
            params="period=10,wcet=2,arrival_offset=0,workload=low",
        )
        with self.assertRaisesRegex(InputValidationError, "inconsistent runtime"):
            load_tasks(self.tasks_file([spec]))

    def test_energy_model_key_priority_and_legacy_fallback(self):
        canonical_path = self.write_yaml(
            {
                "cpu_islands": [{"numcpus": 1, "base_freq": 8100}],
                "energy_management": {
                    "scheduler_energy_model": {
                        "base_power": 2.0,
                        "workload_coefficients": {"low": 3.0},
                        "frequency_power_ratios": {8100: 4.0},
                        "frequency_scaling": {8100: 8.0},
                    },
                    "consumption_model": {
                        "base_power": 9.0,
                        "workload_coefficients": {"low": 9.0},
                        "frequency_scaling": {8100: 9.0},
                    },
                },
            }
        )
        with self.assertWarns(RuntimeWarning):
            canonical = load_system_config(canonical_path)
        self.assertAlmostEqual(canonical.task_energy_per_tick("low"), 0.024)

        legacy_path = self.write_yaml(
            {
                "cpu_islands": [{"numcpus": 1, "base_freq": 8100}],
                "energy_management": {
                    "consumption_model": {
                        "base_power": 2.0,
                        "workload_coefficients": {"low": 3.0},
                        "frequency_scaling": {8100: 4.0},
                    }
                },
            }
        )
        legacy = load_system_config(legacy_path)
        self.assertAlmostEqual(legacy.task_energy_per_tick("low"), 0.024)

    def test_workload_bound_is_deadline_parameterized(self):
        task = RTATask("t0", 10, 3, 7, "low", 0)
        self.assertEqual(workload_bound(task, 1), 3)
        self.assertEqual(workload_bound(task, 6), 3)
        self.assertEqual(workload_bound(task, 7), 4)
        self.assertEqual(workload_bound(task, 8), 5)
        self.assertEqual(workload_bound(task, 9), 6)

        tight_deadline = RTATask("tight", 10, 3, 3, "low", 0)
        self.assertEqual(workload_bound(tight_deadline, 0), 0)

    def test_processor_delay_single_core(self):
        high = RTATask("high", 5, 2, 5, "low", 0)
        target = RTATask("target", 10, 3, 10, "low", 1)
        tasks = self.attach([high, target], 1)
        self.assertEqual(processor_delay(target, 3, 1, tasks), 1)

    def test_processor_delay_multicore_respects_sequential_tasks(self):
        high1 = RTATask("high1", 5, 3, 5, "low", 0)
        high2 = RTATask("high2", 6, 3, 6, "low", 1)
        target = RTATask("target", 10, 3, 10, "low", 2)
        tasks = self.attach([high1, high2, target], 2)
        self.assertEqual(processor_delay(target, 5, 2, tasks), 3)
        self.assertEqual(
            processor_delay(target, 5, 2, [high1, target]),
            0,
        )

    def test_processor_workload_uses_discrete_completion_boundary(self):
        high = RTATask("high", 10, 3, 10, "low", 0)
        target = RTATask("target", 20, 3, 20, "low", 1)
        tasks = self.attach([high, target], 1)

        self.assertEqual(_processor_workloads(target, 2, tasks)["high"], 0)
        self.assertEqual(_processor_workloads(target, 3, tasks)["high"], 1)

    def test_processor_delay_is_not_total_workload_divided_by_cores(self):
        high = RTATask("high", 10, 4, 10, "low", 0)
        target = RTATask("target", 20, 1, 20, "low", 1)
        tasks = self.attach([high, target], 2)
        bars = _processor_workloads(target, 4, tasks)

        self.assertEqual(sum(bars.values()) // 2, 2)
        self.assertEqual(processor_delay(target, 4, 2, tasks), 0)

    def brute_prefix_energy(self, tasks, target, w, x, cores):
        if x == 0:
            return 0.0
        high = hp(tasks, target)
        ordered = rm_order(tasks)
        target_pos = ordered.index(target)
        low = ordered[target_pos + 1 :]
        delay = processor_delay(target, w, cores, tasks)
        bars = {
            task.name: min(
                workload_bound(task, w),
                max(w - target.wcet + 1, 0),
            )
            for task in high
        }
        best = 0.0
        for z in range(max(0, x - delay), min(target.wcet, x) + 1):
            a_ranges = [
                range(min(workload_bound(task, w), x - z, bars[task.name]) + 1)
                for task in high
            ]
            c_ranges = [
                range(min(workload_bound(task, w), z) + 1)
                for task in low
            ]
            for a_values in itertools.product(*a_ranges):
                if sum(a_values) != cores * (x - z):
                    continue
                b_ranges = [
                    range(min(workload_bound(task, w) - a, z) + 1)
                    for task, a in zip(high, a_values)
                ]
                for b_values in itertools.product(*b_ranges):
                    u_ranges = [
                        range(workload_bound(task, w) - a - b + 1)
                        for task, a, b in zip(high, a_values, b_values)
                    ]
                    for c_values in itertools.product(*c_ranges):
                        if sum(b_values) + sum(c_values) > (cores - 1) * z:
                            continue
                        for u_values in itertools.product(*u_ranges):
                            energy = z * target.energy_per_tick
                            energy += sum(
                                (a + b + u) * task.energy_per_tick
                                for task, a, b, u in zip(
                                    high, a_values, b_values, u_values
                                )
                            )
                            energy += sum(
                                c * task.energy_per_tick
                                for task, c in zip(low, c_values)
                            )
                            best = max(best, energy)
        return best

    def brute_energy_blocking(self, tasks, target, w, beta, cores, e0=0.0):
        high = hp(tasks, target)
        ordered = rm_order(tasks)
        target_pos = ordered.index(target)
        low = ordered[target_pos + 1 :]
        delay = processor_delay(target, w, cores, tasks)
        reference = target.wcet + delay
        if reference <= 0:
            return 0
        bars = {
            task.name: min(
                workload_bound(task, w),
                max(w - target.wcet + 1, 0),
            )
            for task in high
        }
        best = 0
        for x in range(1, reference + 1):
            for z in range(max(0, x - delay), min(target.wcet, x) + 1):
                a_ranges = [
                    range(min(workload_bound(task, w), x - z, bars[task.name]) + 1)
                    for task in high
                ]
                c_ranges = [
                    range(min(workload_bound(task, w), z) + 1)
                    for task in low
                ]
                for a_values in itertools.product(*a_ranges):
                    if sum(a_values) != cores * (x - z):
                        continue
                    b_ranges = [
                        range(min(workload_bound(task, w) - a, z) + 1)
                        for task, a in zip(high, a_values)
                    ]
                    for b_values in itertools.product(*b_ranges):
                        u_ranges = [
                            range(workload_bound(task, w) - a - b + 1)
                            for task, a, b in zip(high, a_values, b_values)
                        ]
                        for c_values in itertools.product(*c_ranges):
                            if sum(b_values) + sum(c_values) > (cores - 1) * z:
                                continue
                            for u_values in itertools.product(*u_ranges):
                                energy = z * target.energy_per_tick
                                energy += sum(
                                    (a + b + u) * task.energy_per_tick
                                    for task, a, b, u in zip(
                                        high,
                                        a_values,
                                        b_values,
                                        u_values,
                                    )
                                )
                                energy += sum(
                                    c * task.energy_per_tick
                                    for task, c in zip(low, c_values)
                                )
                                delta = beta_inverse(
                                    beta, max(energy - e0, 0.0)
                                )
                                if delta is None:
                                    return None
                                best = max(
                                    best,
                                    max(sum(u_values), max(delta - x, 0)),
                                )
        return best

    def test_prefix_energy_matches_small_brute_force_case(self):
        high = RTATask("high", 5, 2, 5, "high", 0, 2.0)
        target = RTATask("target", 10, 2, 10, "low", 1, 1.0)
        low = RTATask("low", 20, 2, 20, "very_high", 2, 5.0)
        tasks = self.attach([high, target, low], 2)
        for x in range(0, 3):
            expected = self.brute_prefix_energy(tasks, target, 3, x, 2)
            actual = prefix_energy_upper_bound(target, 3, x, tasks, 2)
            self.assertAlmostEqual(actual, expected)

    def test_low_priority_high_energy_contributes_only_to_b_pool(self):
        target = RTATask("target", 10, 2, 10, "low", 0, 1.0)
        low = RTATask("low", 20, 5, 20, "very_high", 1, 10.0)
        tasks = self.attach([target, low], 2)
        # x=1 forces z=1, so B has one slot and the low task may contribute.
        self.assertEqual(
            prefix_energy_upper_bound(target, 2, 1, tasks, 2),
            11.0,
        )
        # With no target execution, B has zero capacity; low priority cannot use A.
        self.assertEqual(
            prefix_energy_upper_bound(target, 2, 0, tasks, 2),
            0.0,
        )

    def test_low_priority_task_cannot_enter_nonempty_a_pool(self):
        high1 = RTATask("high1", 5, 1, 5, "high", 0, 10.0)
        high2 = RTATask("high2", 6, 1, 6, "high", 1, 10.0)
        target = RTATask("target", 10, 2, 10, "low", 2, 1.0)
        low = RTATask("low", 20, 5, 20, "very_high", 3, 11.0)
        tasks = self.attach([high1, high2, target, low], 2)

        self.assertEqual(processor_delay(target, 2, 1, tasks), 2)
        # With one core, B has zero capacity for every z. The high-priority
        # jobs and target may contribute, but the low-priority job cannot
        # enter the A pool.
        self.assertEqual(
            prefix_energy_upper_bound(target, 2, 1, tasks, 1),
            41.0,
        )

    def test_energy_blocking_matches_brute_force_when_u_dominates(self):
        high = RTATask("high", 5, 2, 5, "high", 0, 0.01)
        target = RTATask("target", 10, 1, 10, "low", 1, 0.01)
        tasks = self.attach([high, target], 1)
        beta = build_energy_service_curve([10.0] * 10, 10)

        expected = self.brute_energy_blocking(tasks, target, 1, beta, 1)
        actual = energy_blocking_bound(target, 1, beta, tasks=tasks, M=1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, 2)

    def test_energy_blocking_matches_brute_force_when_beta_delay_dominates(self):
        target = RTATask("target", 10, 1, 10, "low", 0, 3.0)
        tasks = self.attach([target], 1)
        beta = build_energy_service_curve([1.0] * 10, 10)

        expected = self.brute_energy_blocking(tasks, target, 1, beta, 1)
        actual = energy_blocking_bound(target, 1, beta, tasks=tasks, M=1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, 2)

    def test_v20_1_omega_reduction_matches_complete_tiny_brute_force(self):
        high1 = RTATask("high1", 5, 2, 5, "high", 0, 2.0)
        high2 = RTATask("high2", 6, 1, 6, "low", 1, 1.0)
        target = RTATask("target", 10, 2, 10, "low", 2, 1.5)
        low = RTATask("low", 20, 2, 20, "very_high", 3, 3.0)
        tasks = self.attach([high1, high2, target, low], 2)
        beta = build_energy_service_curve([2.0] * 40, 40)

        expected = self.brute_energy_blocking(
            tasks, target, 3, beta, 2, e0=1.0
        )
        actual = energy_blocking_bound(
            target, 3, beta, E0=1.0, tasks=tasks, M=2
        )
        self.assertEqual(actual, expected)

    def test_v20_1_exact_reduction_matches_brute_force_grid(self):
        scenarios = [
            (
                1,
                [
                    RTATask("h1", 4, 1, 4, "high", 0, 1.5),
                    RTATask("target", 8, 1, 8, "low", 1, 1.0),
                    RTATask("low", 12, 1, 12, "very_high", 2, 2.0),
                ],
            ),
            (
                2,
                [
                    RTATask("h1", 4, 1, 4, "high", 0, 2.0),
                    RTATask("h2", 5, 1, 5, "low", 1, 1.0),
                    RTATask("target", 8, 2, 8, "low", 2, 1.5),
                    RTATask("low", 12, 2, 12, "very_high", 3, 3.0),
                ],
            ),
        ]
        beta = build_energy_service_curve([2.0] * 40, 40)

        for cores, scenario in scenarios:
            tasks = self.attach(scenario, cores)
            target = next(task for task in tasks if task.name == "target")
            for w in range(1, 4):
                for e0 in (0.0, 0.5, 2.0):
                    with self.subTest(cores=cores, w=w, e0=e0):
                        self.assertEqual(
                            energy_blocking_bound(
                                target,
                                w,
                                beta,
                                E0=e0,
                                tasks=tasks,
                                M=cores,
                            ),
                            self.brute_energy_blocking(
                                tasks, target, w, beta, cores, e0=e0
                            ),
                        )

    def test_omega_reduction_with_two_hp_two_lp_and_feasible_positive_b(self):
        high1 = RTATask("high1", 4, 1, 4, "high", 0, 2.0)
        high2 = RTATask("high2", 5, 1, 5, "low", 1, 1.0)
        target = RTATask("target", 8, 1, 8, "low", 2, 1.5)
        low1 = RTATask("low1", 12, 1, 12, "very_high", 3, 5.0)
        low2 = RTATask("low2", 13, 1, 13, "high", 4, 4.0)
        tasks = self.attach([high1, high2, target, low1, low2], 2)
        beta = build_energy_service_curve([3.0] * 40, 40)

        # At x=z=1, b_high1=1 is a feasible full-Omega state because the
        # concurrent capacity is one. The closed form instead moves that unit
        # to u_high1 and may use the released slot for either low task.
        self.assertGreaterEqual(workload_bound(high1, 1), 1)
        self.assertEqual((2 - 1) * 1, 1)
        for e0 in (0.0, 1.0):
            with self.subTest(e0=e0):
                self.assertEqual(
                    energy_blocking_bound(
                        target, 2, beta, E0=e0, tasks=tasks, M=2
                    ),
                    self.brute_energy_blocking(
                        tasks, target, 2, beta, 2, e0=e0
                    ),
                )

    def test_v20_1_omega_requires_exact_a_capacity_and_unique_x_zero(self):
        high = RTATask("high", 5, 2, 5, "high", 0, 2.0)
        target = RTATask("target", 10, 1, 10, "low", 1, 1.0)
        tasks = self.attach([high, target], 2)
        bars = _processor_workloads(target, 2, tasks)

        self.assertEqual(
            _deadline_energy_states_for_z(
                target, tasks, 2, 0, 0, 2, bars
            ),
            [(0, 0)],
        )
        # One sequential high-priority task cannot fill two cores for one
        # complete processor-wait unit, so this Omega slice is infeasible.
        self.assertEqual(
            _deadline_energy_states_for_z(
                target, tasks, 2, 1, 0, 2, bars
            ),
            [],
        )

    def test_u_is_serial_sum_and_is_not_parallel_compressed(self):
        high1 = RTATask("high1", 5, 1, 5, "high", 0, 0.01)
        high2 = RTATask("high2", 6, 1, 6, "high", 1, 0.01)
        target = RTATask("target", 10, 1, 10, "low", 2, 0.01)
        tasks = self.attach([high1, high2, target], 2)
        beta = build_energy_service_curve([10.0] * 20, 20)

        self.assertEqual(
            energy_blocking_bound(target, 1, beta, tasks=tasks, M=2),
            2,
        )

    def test_energy_blocking_max_form_honors_initial_energy(self):
        target = RTATask("target", 10, 1, 10, "low", 0, 3.0)
        tasks = self.attach([target], 1)
        beta = build_energy_service_curve([1.0] * 10, 10)

        self.assertEqual(
            energy_blocking_bound(
                target, 1, beta, E0=1.0, tasks=tasks, M=1
            ),
            1,
        )
        self.assertEqual(
            energy_blocking_bound(
                target, 1, beta, E0=3.0, tasks=tasks, M=1
            ),
            0,
        )

        high = RTATask("high", 5, 2, 5, "high", 0, 0.01)
        target = RTATask("target", 10, 1, 10, "low", 1, 0.01)
        tasks = self.attach([high, target], 1)
        self.assertEqual(
            energy_blocking_bound(
                target, 1, beta, E0=100.0, tasks=tasks, M=1
            ),
            2,
        )

    def test_constant_zero_and_step_service_curves(self):
        constant = build_energy_service_curve([2.0] * 4, 4)
        self.assertEqual(list(constant), [0.0, 2.0, 4.0, 6.0, 8.0])
        self.assertEqual(beta_inverse(constant, 5.0), 3)

        zero = build_energy_service_curve([0.0] * 4, 4)
        self.assertIsNone(beta_inverse(zero, 0.1))
        self.assertEqual(beta_inverse(zero, 0.0), 0)

        step = build_energy_service_curve([0.0, 1.0, 2.0], 3)
        self.assertEqual(list(step), [0.0, 0.0, 1.0, 3.0])
        self.assertEqual(beta_inverse(step, 0.5), 2)
        self.assertEqual(beta_inverse(step, 2.0), 3)

    def test_beta_inverse_uses_strict_discrete_boundaries(self):
        beta = build_energy_service_curve([1.0, 1.0], 2)
        just_above_one = 1.0 + 5e-13

        self.assertEqual(beta_inverse(beta, 0.0), 0)
        self.assertEqual(beta_inverse(beta, 1.0), 1)
        self.assertEqual(beta_inverse(beta, just_above_one), 2)
        self.assertEqual(beta_inverse(beta, 2.0), 2)
        self.assertIsNone(beta_inverse(beta, 2.0 + 5e-13))

        plain_beta = [0.0, 1.0, 2.0]
        self.assertEqual(beta_inverse(plain_beta, 1.0), 1)
        self.assertEqual(beta_inverse(plain_beta, just_above_one), 2)

    def test_beta_inverse_fast_path_and_general_path_match_linear_scan(self):
        for trace in ([0.0, 1.0, 2.0, 3.0], [3.0, 0.0, 2.0, 1.0]):
            beta = build_energy_service_curve(trace, len(trace))
            for demand in (0.0, 0.5, 1.0, 2.0, 3.0, 7.0):
                expected = next(
                    (
                        delta
                        for delta in range(len(beta))
                        if beta[delta] >= demand
                    ),
                    None,
                )
                self.assertEqual(beta_inverse(beta, demand), expected)
                self.assertEqual(beta_inverse(beta, demand), expected)

    def test_abundant_energy_reduces_to_cpu_only_bound(self):
        target = RTATask("target", 10, 2, 10, "low", 0, 0.001)
        tasks = self.attach([target], 1)
        beta = build_energy_service_curve([1.0] * 20, 20)
        result = response_time_bound(
            target, tasks, 1, beta, assume_no_overflow=True
        )
        self.assertTrue(result.proven)
        expected_cpu_only = target.wcet + processor_delay(
            target, result.response_time_bound, 1, tasks
        )
        self.assertEqual(result.response_time_bound, expected_cpu_only)
        self.assertEqual(
            energy_blocking_bound(
                target,
                result.response_time_bound,
                beta,
                tasks=tasks,
                M=1,
            ),
            0,
        )

    def test_insufficient_energy_returns_unproven(self):
        target = RTATask("target", 10, 2, 10, "low", 0, 1.0)
        tasks = self.attach([target], 1)
        beta = build_energy_service_curve([0.0] * 10, 10)
        result = response_time_bound(
            target, tasks, 1, beta, assume_no_overflow=True
        )
        self.assertFalse(result.proven)
        self.assertIsNone(result.response_time_bound)
        self.assertIn("insufficient", result.failure_reason)

    def test_positive_energy_blocking_can_converge_successfully(self):
        target = RTATask("target", 10, 1, 10, "low", 0, 1.0)
        tasks = self.attach([target], 1)
        beta = build_energy_service_curve([0.5] * 10, 10)

        self.assertEqual(
            energy_blocking_bound(target, 1, beta, tasks=tasks, M=1),
            1,
        )
        result = response_time_bound(
            target, tasks, 1, beta, assume_no_overflow=True
        )
        self.assertTrue(result.proven)
        self.assertEqual(result.response_time_bound, 2)
        self.assertGreater(result.response_time_bound, target.wcet)

    def test_response_bound_cannot_exceed_harvesting_horizon(self):
        target = RTATask("target", 20, 1, 20, "low", 0, 3.0)
        tasks = self.attach([target], 1)
        long_beta = build_energy_service_curve([1.0] * 20, 20)
        unconstrained_result = response_time_bound(
            target, tasks, 1, long_beta, assume_no_overflow=True
        )
        self.assertTrue(unconstrained_result.proven)
        self.assertEqual(unconstrained_result.response_time_bound, 3)

        beta = build_energy_service_curve([1.0, 1.0], 2)

        result = response_time_bound(
            target, tasks, 1, beta, assume_no_overflow=True
        )
        self.assertFalse(result.proven)
        self.assertIn("finite energy service", result.failure_reason)

        longer_than_horizon = RTATask(
            "long", 20, 3, 20, "low", 0, 0.001
        )
        self.attach([longer_than_horizon], 1)
        initial_result = response_time_bound(
            longer_than_horizon,
            [longer_than_horizon],
            1,
            beta,
            assume_no_overflow=True,
        )
        self.assertFalse(initial_result.proven)
        self.assertIn(
            "exceeds harvesting horizon", initial_result.failure_reason
        )

    def test_initial_energy_yaml_is_not_used_as_e0(self):
        system = self.system_file()
        tasks = self.tasks_file([self.task_spec("t0", 10, 1)])
        report = analyze_taskset(
            system,
            tasks,
            horizon_ms=10,
            assume_no_overflow=True,
            harvest_trace=[0.0] * 10,
        )
        self.assertEqual(report.e0, 0.0)
        self.assertFalse(report.tasks[0].proven)

    def test_explicit_rta_initial_energy_is_independent_and_used(self):
        system = self.system_file()
        tasks = self.tasks_file([self.task_spec("t0", 10, 1)])
        report = analyze_taskset(
            system,
            tasks,
            horizon_ms=10,
            assume_no_overflow=True,
            harvest_trace=[0.0] * 10,
            initial_energy=0.001,
        )

        self.assertEqual(report.e0, 0.001)
        self.assertTrue(report.tasks[0].proven)
        self.assertIn("E0 is 0.001 J", report.assumptions[2])

    def test_rta_initial_energy_must_be_a_physical_lower_bound(self):
        system = self.system_file(max_energy=2.0)
        tasks = self.tasks_file([self.task_spec("t0", 10, 1)])

        for value in (-1.0, float("inf"), 2.1):
            with self.subTest(value=value):
                with self.assertRaises(InputValidationError):
                    analyze_taskset(
                        system,
                        tasks,
                        horizon_ms=10,
                        assume_no_overflow=True,
                        harvest_trace=[1.0] * 10,
                        initial_energy=value,
                    )

    def test_rta_cli_defaults_and_optional_profile_output(self):
        parser = _build_argument_parser()
        defaults = parser.parse_args(
            ["--system", "system.yml", "--tasks", "tasks.yml",
             "--horizon-ms", "10"]
        )
        self.assertEqual(defaults.rta_initial_energy, 0.0)
        self.assertFalse(defaults.profile_rta)
        help_text = parser.format_help()
        normalized_help = " ".join(help_text.split())
        self.assertIn(
            "absolute energy lower bound E0 in joules", normalized_help
        )
        self.assertIn("not a battery ratio", normalized_help)
        self.assertIn("every target-job release", normalized_help)
        self.assertIn(
            "inherit the simulator's --initial-energy value",
            normalized_help,
        )
        self.assertIn("example, 1.0 means 1 J", normalized_help)
        self.assertIn("full-battery ratio", normalized_help)

        target = RTATask("target", 10, 1, 10, "low", 0, 0.01)
        tasks = self.attach([target], 1)
        beta = build_energy_service_curve([1.0] * 10, 10)
        plain = response_time_bound(
            target, tasks, 1, beta, assume_no_overflow=True
        ).to_dict()
        profiled = response_time_bound(
            target,
            tasks,
            1,
            beta,
            assume_no_overflow=True,
            profile_rta=True,
        ).to_dict()

        self.assertNotIn("rta_profile", plain)
        profile = profiled["rta_profile"]
        for key in (
            "task_id",
            "total_time_sec",
            "fixed_point_iterations",
            "processor_delay_time_sec",
            "energy_blocking_time_sec",
            "energy_state_dp_time_sec",
            "beta_inverse_time_sec",
            "beta_inverse_calls",
            "beta_inverse_cache_hits",
            "beta_inverse_cache_misses",
            "max_frontier_size",
            "total_states_generated",
            "x_values_checked",
            "z_values_checked",
            "deadline_energy_state_calls",
        ):
            self.assertIn(key, profile)
        self.assertEqual(profile["task_id"], "target")
        self.assertGreater(profile["beta_inverse_calls"], 0)

    def test_no_overflow_acknowledgement_is_required_for_success(self):
        system = self.system_file()
        tasks = self.tasks_file([self.task_spec("t0", 10, 1)])
        report = analyze_taskset(
            system,
            tasks,
            horizon_ms=10,
            assume_no_overflow=False,
            harvest_trace=[1.0] * 10,
        )
        self.assertFalse(report.tasks[0].proven)
        self.assertIn("no-overflow", report.tasks[0].failure_reason)

    def test_success_report_is_explicitly_conditional(self):
        report = analyze_taskset(
            self.system_file(),
            self.tasks_file([self.task_spec("t0", 10, 1)]),
            horizon_ms=10,
            assume_no_overflow=True,
            harvest_trace=[1.0] * 10,
        )
        payload = report.to_dict()
        self.assertTrue(payload["conditional"])
        self.assertTrue(payload["proven_under_assumptions"])
        self.assertFalse(payload["absolute_schedulability_claim"])
        self.assertIn("battery does not overflow", payload["assumptions"][0])
        self.assertTrue(payload["tasks"][0]["conditional"])
        self.assertTrue(
            payload["tasks"][0]["proven_under_assumptions"]
        )

        text = _format_report(report)
        self.assertIn("CONDITIONAL ANALYSIS ONLY", text)
        self.assertIn("no absolute schedulability claim", text)
        self.assertIn("taskset proven_under_assumptions=true", text)

    def test_rejects_nonperiodic_dag_resource_and_task_frequency_models(self):
        sporadic = self.task_spec("sporadic", 10, 1)
        sporadic["type"] = "sporadic"
        with self.assertRaisesRegex(InputValidationError, "non-periodic"):
            load_tasks(self.tasks_file([sporadic]))

        for field in ("dependencies", "edges"):
            dag_task = self.task_spec("dag_task", 10, 1)
            dag_task[field] = ["other"]
            with self.subTest(field=field):
                with self.assertRaisesRegex(InputValidationError, "DAG"):
                    load_tasks(self.tasks_file([dag_task]))

        resource_task = self.task_spec("resource_task", 10, 1)
        resource_task["resources"] = ["r0"]
        with self.assertRaisesRegex(InputValidationError, "resource/lock"):
            load_tasks(self.tasks_file([resource_task]))

        lock_task = self.task_spec(
            "lock_task", 10, 1, code=["lock(r0)"]
        )
        with self.assertRaisesRegex(InputValidationError, "resource/lock"):
            load_tasks(self.tasks_file([lock_task]))

        frequency_task = self.task_spec(
            "frequency_task",
            10,
            1,
            params=(
                "period=10,wcet=1,arrival_offset=0,workload=low,"
                "frequency_mhz=8100"
            ),
        )
        with self.assertRaisesRegex(
            InputValidationError, "task-level frequency"
        ):
            load_tasks(self.tasks_file([frequency_task]))

    def test_rejects_deadline_multisegment_resources_and_missing_horizon(self):
        invalid_specs = [
            self.task_spec("deadline", 10, 2, deadline=11),
            self.task_spec("wcet_deadline", 10, 2, deadline=1),
            self.task_spec(
                "multi",
                10,
                2,
                code=["fixed(1, low)", "fixed(1, low)"],
            ),
        ]
        with self.assertRaisesRegex(InputValidationError, "greater than period"):
            load_tasks(self.tasks_file([invalid_specs[0]]))
        with self.assertRaisesRegex(InputValidationError, "C_i <= D_i <= T_i"):
            load_tasks(self.tasks_file([invalid_specs[1]]))
        with self.assertRaisesRegex(InputValidationError, "exactly one"):
            load_tasks(self.tasks_file([invalid_specs[2]]))
        missing_workload = self.task_spec(
            "missing_workload",
            10,
            2,
            params="period=10,wcet=2,arrival_offset=0",
        )
        with self.assertRaisesRegex(InputValidationError, "missing workload"):
            load_tasks(self.tasks_file([missing_workload]))
        with self.assertRaisesRegex(InputValidationError, "resource/lock"):
            load_tasks(
                self.tasks_file(
                    [self.task_spec("t0", 10, 2)],
                    resources=[{"name": "r", "initial_state": "unlocked"}],
                )
            )
        with self.assertRaisesRegex(InputValidationError, "horizon-ms"):
            analyze_taskset(
                self.system_file(),
                self.tasks_file([self.task_spec("t0", 10, 1)]),
                horizon_ms=None,
                assume_no_overflow=True,
            )


if __name__ == "__main__":
    unittest.main()
