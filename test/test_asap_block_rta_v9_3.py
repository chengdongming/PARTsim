import os
import random
import sys
import unittest
from decimal import Decimal
from fractions import Fraction
from unittest import mock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asap_block_rta_v9_3 as v93
from v9_3_bruteforce_oracle import (
    brute_force_complete_envelope,
    brute_force_local_envelope,
)


RANDOM_SEED = 0x93A5B10C
RANDOM_ENVELOPE_INSTANCES = 10_000


def task(name, c=1, d=3, t=4, power=1):
    return v93.V93Task(name, c, d, t, Fraction(power))


class WorkloadV93Test(unittest.TestCase):
    def setUp(self):
        self.task = task("i", 3, 5, 7, 1)

    def test_hand_calculated_boundaries(self):
        expected_theta_c = [0, 1, 2, 3, 3, 3, 3, 3, 4]
        self.assertEqual(
            [
                v93.workload_bound_v9_3(self.task, length, 3)
                for length in range(9)
            ],
            expected_theta_c,
        )
        self.assertEqual(v93.workload_bound_v9_3(self.task, 0, 5), 2)
        self.assertEqual(v93.workload_bound_v9_3(self.task, 2, 5), 3)
        self.assertEqual(v93.workload_bound_v9_3(self.task, 7, 5), 5)

    def test_length_and_theta_monotonicity(self):
        for theta in range(self.task.wcet, self.task.deadline + 1):
            values = [
                v93.workload_bound_v9_3(self.task, length, theta)
                for length in range(50)
            ]
            self.assertEqual(values, sorted(values))

        # Frozen parameter domain: exercise every legal C/D/T, theta, and a
        # range spanning multiple period boundaries rather than only examples.
        for c_i in range(1, 5):
            for d_i in range(c_i, 7):
                for t_i in range(d_i, 9):
                    item = task("domain", c_i, d_i, t_i)
                    for theta in range(c_i, d_i + 1):
                        values = [
                            v93.workload_bound_v9_3(item, length, theta)
                            for length in range(3 * t_i + 1)
                        ]
                        self.assertEqual(values, sorted(values))
                    for length in range(3 * t_i + 1):
                        values = [
                            v93.workload_bound_v9_3(item, length, theta)
                            for theta in range(c_i, d_i + 1)
                        ]
                        self.assertEqual(values, sorted(values))
        for length in range(50):
            values = [
                v93.workload_bound_v9_3(self.task, length, theta)
                for theta in range(self.task.wcet, self.task.deadline + 1)
            ]
            self.assertEqual(values, sorted(values))

    def test_theta_c_theta_d_and_period_edges(self):
        self.assertEqual(v93.workload_bound_v9_3(self.task, 0, 3), 0)
        self.assertEqual(v93.workload_bound_v9_3(self.task, 0, 5), 2)
        self.assertEqual(v93.workload_bound_v9_3(self.task, 6, 3), 3)
        self.assertEqual(v93.workload_bound_v9_3(self.task, 7, 3), 3)
        self.assertEqual(v93.workload_bound_v9_3(self.task, 8, 3), 4)

    def test_rejects_invalid_parameters(self):
        with self.assertRaises(v93.V93InputError):
            v93.workload_bound_v9_3(self.task, -1, 3)
        with self.assertRaises(v93.V93InputError):
            v93.workload_bound_v9_3(self.task, 1, 2)
        with self.assertRaises(v93.V93InputError):
            v93.V93Task("bad", 3, 2, 4, 1)
        with self.assertRaises(v93.V93InputError):
            v93.V93Task("zero-c", 0, 1, 1, 1)
        for invalid_power in (0, -1, True, float("inf")):
            with self.subTest(power=invalid_power):
                with self.assertRaises(v93.V93InputError):
                    v93.V93Task("bad-power", 1, 1, 1, invalid_power)


class ProcessorProgressV93Test(unittest.TestCase):
    def test_no_hp_and_single_core(self):
        target = task("k", 2, 6, 7)
        self.assertEqual(
            v93.processor_delay_v9_3(target, [], 5, 2, {}), 0
        )
        high = task("h", 2, 5, 5)
        self.assertEqual(
            v93.processor_delay_v9_3(
                target, [high], 5, 1, {"h": 5}
            ),
            4,
        )

    def test_multicore_and_effective_truncation(self):
        target = task("k", 3, 8, 9)
        highs = [task("h0", 3, 4, 4), task("h1", 2, 5, 5)]
        bars = v93.effective_hp_workloads_v9_3(
            target, highs, 3, {"h0": 4, "h1": 5}
        )
        self.assertEqual(bars, (1, 1))
        self.assertEqual(
            v93.processor_delay_v9_3(
                target, highs, 3, 2, {"h0": 4, "h1": 5}
            ),
            1,
        )
        self.assertEqual(
            v93.processor_progress_v9_3(
                target, highs, 3, 2, {"h0": 4, "h1": 5}
            ),
            4,
        )

    def test_fast_matches_definition_scan(self):
        rng = random.Random(RANDOM_SEED ^ 0xD)
        for instance in range(2_000):
            target = task(
                "k", rng.randint(1, 3), 5, 6, rng.randint(1, 5)
            )
            highs = []
            theta = {}
            for index in range(rng.randint(0, 5)):
                c_i = rng.randint(1, 3)
                d_i = rng.randint(c_i, 5)
                t_i = rng.randint(d_i, 6)
                high = task(
                    "h{}".format(index),
                    c_i,
                    d_i,
                    t_i,
                    rng.randint(1, 7),
                )
                highs.append(high)
                theta[high.name] = rng.randint(c_i, d_i)
            w = rng.randint(target.wcet, target.deadline)
            processors = rng.randint(1, 4)
            with self.subTest(instance=instance):
                self.assertEqual(
                    v93.processor_delay_v9_3(
                        target, highs, w, processors, theta
                    ),
                    v93.processor_delay_definition_scan_v9_3(
                        target, highs, w, processors, theta
                    ),
                )

    def test_rejects_candidate_windows_outside_c_through_d(self):
        target = task("k", 2, 4, 5)
        for invalid_w in (0, 1, 5):
            with self.subTest(w=invalid_w):
                with self.assertRaises(v93.V93InputError):
                    v93.processor_progress_v9_3(
                        target, [], invalid_w, 1, {}
                    )


class EnvelopeExactnessV93Test(unittest.TestCase):
    def assertBothOraclesEqual(
        self, target, highs, lows, w, q, h, processors, theta
    ):
        complete = v93.complete_window_envelope_v9_3(
            target, highs, lows, w, q, h, processors, theta
        )
        local = v93.local_window_envelope_v9_3(
            target, highs, lows, w, q, h, processors, theta
        )
        self.assertEqual(
            complete,
            brute_force_complete_envelope(
                target, highs, lows, w, q, h, processors, theta
            ),
        )
        self.assertEqual(
            local,
            brute_force_local_envelope(
                target, highs, lows, w, q, h, processors, theta
            ),
        )
        self.assertLessEqual(local, complete)

    def test_named_boundary_and_saturation_cases(self):
        cases = [
            # no hp; no lp; target energy must not be omitted
            (task("k", 2, 3, 4, 7), [], [], 2, 2, 0, 2, {}),
            # no hp, lp capacity can saturate
            (
                task("k", 2, 4, 5, 2),
                [],
                [task("l", 2, 4, 5, 9)],
                3,
                2,
                0,
                2,
                {},
            ),
            # no lp; y_k=0 is optimal due to high hp power
            (
                task("k", 1, 3, 4, 1),
                [task("h", 1, 3, 4, 20)],
                [],
                1,
                1,
                0,
                1,
                {"h": 3},
            ),
            # y_k>0 is optimal; tied powers
            (
                task("k", 2, 4, 5, 20),
                [task("h", 2, 3, 4, 3)],
                [task("l", 2, 4, 5, 3)],
                3,
                2,
                0,
                2,
                {"h": 2},
            ),
            # heterogeneous power and total processor capacity saturation
            (
                task("k", 2, 5, 6, 5),
                [task("h0", 2, 4, 5, 17), task("h1", 2, 5, 6, 1)],
                [task("l0", 2, 5, 6, 13), task("l1", 1, 3, 4, 2)],
                4,
                2,
                1,
                2,
                {"h0": 3, "h1": 5},
            ),
        ]
        for index, case in enumerate(cases):
            with self.subTest(case=index):
                self.assertBothOraclesEqual(*case)
        self.assertEqual(
            v93.complete_window_envelope_v9_3(*cases[0]), Fraction(14)
        )
        self.assertEqual(
            v93.complete_window_envelope_v9_3(*cases[2]), Fraction(20)
        )
        self.assertEqual(
            v93.complete_window_envelope_v9_3(*cases[1]), Fraction(22)
        )
        self.assertEqual(
            v93.complete_window_envelope_v9_3(*cases[3]), Fraction(46)
        )

    def test_direct_vector_oracle_has_independent_hand_cases(self):
        target_only = (task("k", 2, 3, 4, 7), [], [], 2, 2, 0, 2, {})
        lp_saturation = (
            task("k", 2, 4, 5, 2),
            [],
            [task("l", 2, 4, 5, 9)],
            3,
            2,
            0,
            2,
            {},
        )
        self.assertEqual(
            brute_force_complete_envelope(*target_only), Fraction(14)
        )
        self.assertEqual(
            brute_force_complete_envelope(*lp_saturation), Fraction(22)
        )
        with mock.patch.object(
            v93,
            "exact_energy_envelope_v9_3",
            side_effect=AssertionError("fast path must not be called"),
        ), mock.patch.object(
            v93,
            "_bounded_prefix_value",
            side_effect=AssertionError("prefix helper must not be called"),
        ):
            self.assertEqual(
                brute_force_local_envelope(*target_only), Fraction(14)
            )

    def test_frozen_small_domain_exhaustive(self):
        checked = 0
        for processors in (1, 2):
            for target_power in (1, 3):
                for high_count in (0, 1):
                    for low_count in (0, 1):
                        target = task("k", 1, 3, 4, target_power)
                        highs = (
                            [task("h", 1, 3, 4, 2)] if high_count else []
                        )
                        lows = (
                            [task("l", 1, 3, 4, 4)] if low_count else []
                        )
                        theta = {item.name: 2 for item in highs}
                        for w in range(1, 4):
                            for q in range(1, w + 1):
                                for h in range(0, w - q + 1):
                                    self.assertBothOraclesEqual(
                                        target,
                                        highs,
                                        lows,
                                        w,
                                        q,
                                        h,
                                        processors,
                                        theta,
                                    )
                                    checked += 1
        self.assertEqual(checked, 160)

    def test_ten_thousand_seeded_random_instances(self):
        rng = random.Random(RANDOM_SEED)
        completed = 0
        covered = set()
        for instance in range(RANDOM_ENVELOPE_INSTANCES):
            c_k = rng.randint(1, 2)
            d_k = rng.randint(c_k, 4)
            target = task(
                "k", c_k, d_k, rng.randint(d_k, 5),
                Fraction(rng.randint(1, 9), rng.randint(1, 3))
            )
            highs = []
            lows = []
            theta = {}
            for prefix, output in (("h", highs), ("l", lows)):
                for index in range(rng.randint(0, 2)):
                    c_i = rng.randint(1, 2)
                    d_i = rng.randint(c_i, 4)
                    item = task(
                        "{}{}".format(prefix, index),
                        c_i,
                        d_i,
                        rng.randint(d_i, 5),
                        Fraction(rng.randint(1, 9), rng.randint(1, 3)),
                    )
                    output.append(item)
                    if prefix == "h":
                        theta[item.name] = rng.randint(c_i, d_i)
            w = rng.randint(c_k, d_k)
            q = rng.randint(1, w)
            h = rng.randint(0, w - q)
            processors = rng.randint(1, 3)
            covered.add("single_core" if processors == 1 else "multicore")
            covered.add("no_hp" if not highs else "has_hp")
            covered.add("no_lp" if not lows else "has_lp")
            if highs and lows:
                covered.add("hp_and_lp")
            covered.add("full_coverage" if q + h == w else "local_prefix")
            powers = [target.power]
            powers.extend(item.power for item in highs)
            powers.extend(item.power for item in lows)
            if len(set(powers)) < len(powers):
                covered.add("tied_power")
            if len(set(powers)) > 1:
                covered.add("heterogeneous_power")
            with self.subTest(
                instance=instance,
                seed=RANDOM_SEED,
                target=target,
                highs=highs,
                lows=lows,
                w=w,
                q=q,
                h=h,
                processors=processors,
                theta=theta,
            ):
                self.assertBothOraclesEqual(
                    target, highs, lows, w, q, h, processors, theta
                )
            completed += 1
        self.assertEqual(completed, RANDOM_ENVELOPE_INSTANCES)
        self.assertTrue(
            {
                "single_core",
                "multicore",
                "no_hp",
                "has_hp",
                "no_lp",
                "has_lp",
                "hp_and_lp",
                "full_coverage",
                "local_prefix",
                "tied_power",
                "heterogeneous_power",
            }.issubset(covered)
        )

    def test_local_uses_q_plus_h_workload_index(self):
        target = task("k", 1, 5, 6)
        high = task("h", 1, 5, 6, 2)
        low = task("l", 1, 5, 6, 3)
        original = v93.workload_bound_v9_3
        lengths = []

        def recording_workload(item, length, theta):
            lengths.append(length)
            return original(item, length, theta)

        with mock.patch.object(
            v93, "workload_bound_v9_3", side_effect=recording_workload
        ):
            v93.local_window_envelope_v9_3(
                target, [high], [low], 5, 2, 1, 2, {"h": 4}
            )
        self.assertTrue(lengths)
        self.assertEqual(set(lengths), {3})

    def test_rejects_float_and_invalid_envelope_parameters(self):
        with self.assertRaises(v93.V93InputError):
            v93.V93Task("float", 1, 2, 3, 0.5)
        target = task("k", 1, 2, 3)
        with self.assertRaises(v93.V93InputError):
            v93.complete_window_envelope_v9_3(
                target, [], [], 2, 2, 1, 1, {}
            )
        with self.assertRaises(v93.V93InputError):
            v93.complete_window_envelope_v9_3(
                target, [], [], 2, 1, 0, 0, {}
            )


class DominanceV93Test(unittest.TestCase):
    def test_complete_close_implies_local_close(self):
        rng = random.Random(RANDOM_SEED ^ 0xC10E)
        checked = 0
        complete_closures = 0
        for instance in range(1_000):
            target = task("k", 1, 4, 5, rng.randint(1, 5))
            highs = [task("h", 1, 3, 4, rng.randint(1, 7))]
            lows = [task("l", 1, 4, 5, rng.randint(1, 7))]
            theta = {"h": rng.randint(1, 3)}
            processors = rng.randint(1, 2)
            w = rng.randint(1, 4)
            a_value = v93.processor_progress_v9_3(
                target, highs, w, processors, theta
            )
            checked += 1
            if a_value > w:
                # Both closure predicates are false when A_k^Theta(w) > w.
                continue
            e0 = Fraction(rng.randint(0, 4))
            rate = Fraction(rng.randint(0, 6))

            def closes(kind):
                for h in range(w - a_value + 1):
                    valid = True
                    for q in range(1, a_value + 1):
                        envelope = v93.exact_energy_envelope_v9_3(
                            kind,
                            target,
                            highs,
                            lows,
                            w,
                            q,
                            h,
                            processors,
                            theta,
                        )
                        if envelope > e0 + rate * (h + q - 1):
                            valid = False
                            break
                    if valid:
                        return True
                return False

            complete_closed = closes(v93.EnvelopeKind.COMPLETE)
            local_closed = closes(v93.EnvelopeKind.LOCAL)
            complete_closures += int(complete_closed)
            with self.subTest(instance=instance):
                self.assertFalse(complete_closed and not local_closed)
        self.assertEqual(checked, 1_000)
        self.assertGreater(complete_closures, 0)
        print("N_cw_closed={}".format(complete_closures))


class CanonicalSearchV93Test(unittest.TestCase):
    def test_a_greater_than_w_skips_only_that_w_without_envelope_calls(self):
        target = task("k", 1, 2, 3)
        high = task("h", 2, 2, 2)
        calls = []
        result = v93.canonical_closure_search_v9_3(
            v93.EnvelopeKind.COMPLETE,
            target,
            [high],
            [],
            1,
            {"h": 2},
            0,
            lambda length: 0 if length == 0 else 100,
            envelope_function=lambda **kwargs: calls.append(kwargs) or 0,
        )
        self.assertEqual(result.solver_status, v93.V93SolverStatus.NO_CANDIDATE)
        self.assertEqual(result.checked_w_count, 2)
        self.assertEqual(result.checked_h_count, 0)
        self.assertEqual(result.checked_q_count, 0)
        self.assertEqual(calls, [])

    def test_smaller_h_failure_does_not_skip_larger_h(self):
        target = task("k", 1, 3, 4)
        calls = []

        def envelope(**kwargs):
            calls.append((kwargs["w"], kwargs["h"], kwargs["q"]))
            return 0 if (kwargs["w"], kwargs["h"]) == (2, 1) else 1

        result = v93.canonical_closure_search_v9_3(
            v93.EnvelopeKind.COMPLETE,
            target,
            [],
            [],
            1,
            {},
            0,
            lambda _length: 0,
            envelope_function=envelope,
        )
        self.assertEqual(result.solver_status, v93.V93SolverStatus.CANDIDATE)
        self.assertEqual((result.closing_w, result.witness_h), (2, 1))
        self.assertEqual(calls, [(1, 0, 1), (2, 0, 1), (2, 1, 1)])

    def test_all_w_h_and_successful_h_q_are_visited(self):
        target = task("k", 1, 4, 5)
        calls = []

        def failing(**kwargs):
            calls.append((kwargs["w"], kwargs["h"], kwargs["q"]))
            return 1

        result = v93.canonical_closure_search_v9_3(
            v93.EnvelopeKind.LOCAL,
            target,
            [],
            [],
            1,
            {},
            0,
            lambda _length: 0,
            envelope_function=failing,
        )
        self.assertEqual(
            result.solver_status, v93.V93SolverStatus.NO_CANDIDATE
        )
        self.assertEqual(result.checked_w_count, 4)
        self.assertEqual(result.checked_h_count, 10)
        self.assertEqual(result.checked_q_count, 10)
        self.assertEqual(len(calls), 10)

        service_indices = []
        target_two = task("k2", 2, 2, 3)
        success = v93.canonical_closure_search_v9_3(
            v93.EnvelopeKind.LOCAL,
            target_two,
            [],
            [],
            1,
            {},
            0,
            lambda length: service_indices.append(length) or 0,
            envelope_function=lambda **_kwargs: 0,
        )
        self.assertEqual(success.candidate_response_time, 2)
        self.assertEqual(success.checked_q_count, 2)
        # The formal validator reads the complete required prefix twice to
        # reject stateful callbacks before the frozen values enter the scan.
        self.assertEqual(service_indices, [0, 1, 0, 1])

    def test_q_failure_breaks_only_current_h_and_next_h_restarts_at_q1(self):
        target = task("k", 3, 5, 6)
        calls = []

        def envelope(**kwargs):
            visit = (kwargs["w"], kwargs["h"], kwargs["q"])
            calls.append(visit)
            if (kwargs["w"], kwargs["h"]) != (5, 2) and kwargs["q"] == 2:
                return 1
            return 0

        result = v93.canonical_closure_search_v9_3(
            v93.EnvelopeKind.LOCAL,
            target,
            [],
            [],
            1,
            {},
            0,
            lambda _length: 0,
            envelope_function=envelope,
        )
        self.assertEqual((result.closing_w, result.witness_h), (5, 2))
        self.assertEqual(
            calls,
            [
                (3, 0, 1),
                (3, 0, 2),
                (4, 0, 1),
                (4, 0, 2),
                (4, 1, 1),
                (4, 1, 2),
                (5, 0, 1),
                (5, 0, 2),
                (5, 1, 1),
                (5, 1, 2),
                (5, 2, 1),
                (5, 2, 2),
                (5, 2, 3),
            ],
        )
        self.assertNotIn((3, 0, 3), calls)
        self.assertNotIn((4, 0, 3), calls)
        self.assertNotIn((5, 1, 3), calls)
        self.assertGreaterEqual(
            v93.processor_progress_v9_3(target, [], 3, 1, {}),
            target.wcet,
        )

    def test_timeout_numeric_and_overflow_never_return_candidate(self):
        target = task("k", 1, 2, 3)
        common = (
            v93.EnvelopeKind.COMPLETE,
            target,
            [],
            [],
            1,
            {},
            0,
            lambda length: 0 if length == 0 else 100,
        )
        timeout = v93.canonical_closure_search_v9_3(
            *common, timeout_seconds=0
        )
        self.assertEqual(
            timeout.solver_status, v93.V93SolverStatus.UNPROVEN_TIMEOUT
        )
        self.assertIsNone(timeout.candidate_response_time)

        clock_values = iter([0, 0, 0, 0, 1])
        elapsed_during_envelope = v93.canonical_closure_search_v9_3(
            *common,
            envelope_function=lambda **_kwargs: 0,
            timeout_seconds=1,
            clock=lambda: next(clock_values),
        )
        self.assertEqual(
            elapsed_during_envelope.solver_status,
            v93.V93SolverStatus.UNPROVEN_TIMEOUT,
        )
        self.assertIsNone(elapsed_during_envelope.candidate_response_time)

        numeric = v93.canonical_closure_search_v9_3(
            *common, envelope_function=lambda **_kwargs: float("nan")
        )
        self.assertEqual(
            numeric.solver_status, v93.V93SolverStatus.UNPROVEN_NUMERIC
        )
        self.assertIsNone(numeric.candidate_response_time)

        def overflow(**_kwargs):
            raise OverflowError("synthetic checked-integer overflow")

        overflowed = v93.canonical_closure_search_v9_3(
            *common, envelope_function=overflow
        )
        self.assertEqual(
            overflowed.solver_status,
            v93.V93SolverStatus.UNPROVEN_OVERFLOW,
        )
        self.assertIsNone(overflowed.candidate_response_time)

        beta_numeric = v93.canonical_closure_search_v9_3(
            *common[:-1],
            lambda _length: Decimal("NaN"),
            envelope_function=lambda **_kwargs: 0,
        )
        self.assertEqual(
            beta_numeric.solver_status,
            v93.V93SolverStatus.UNPROVEN_NUMERIC,
        )

        for invalid_envelope in (Fraction(-1),):
            with self.subTest(envelope=invalid_envelope):
                result = v93.canonical_closure_search_v9_3(
                    *common,
                    envelope_function=lambda **_kwargs: invalid_envelope,
                )
                self.assertEqual(
                    result.solver_status,
                    v93.V93SolverStatus.UNPROVEN_NUMERIC,
                )
                self.assertIsNone(result.candidate_response_time)

        negative_beta = v93.canonical_closure_search_v9_3(
            *common[:-1],
            lambda _length: Fraction(-1),
            envelope_function=lambda **_kwargs: 0,
        )
        self.assertEqual(
            negative_beta.solver_status,
            v93.V93SolverStatus.UNPROVEN_NUMERIC,
        )
        self.assertIsNone(negative_beta.candidate_response_time)

        for reading in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(clock_reading=reading):
                invalid_clock = v93.canonical_closure_search_v9_3(
                    *common,
                    envelope_function=lambda **_kwargs: 0,
                    timeout_seconds=1,
                    clock=lambda: reading,
                )
                self.assertEqual(
                    invalid_clock.solver_status,
                    v93.V93SolverStatus.UNPROVEN_NUMERIC,
                )
                self.assertIsNone(invalid_clock.candidate_response_time)

    def test_service_curve_validation_rejects_every_frozen_p0_case(self):
        target = task("k", 1, 3, 4)
        invalid_curves = (
            [1, 1, 1],
            [0, 2, 1],
            [0, -1, 0],
            [0, 1.0, 2],
            [0, True, 2],
            [0, Decimal("NaN"), 2],
            [0, Decimal("Infinity"), 2],
            [0, 1],
        )
        for beta in invalid_curves:
            with self.subTest(beta=beta):
                result = v93.canonical_closure_search_v9_3(
                    v93.EnvelopeKind.COMPLETE,
                    target,
                    [],
                    [],
                    1,
                    {},
                    0,
                    beta,
                    envelope_function=lambda **_kwargs: 0,
                )
                self.assertEqual(
                    result.solver_status,
                    v93.V93SolverStatus.UNPROVEN_NUMERIC,
                )
                self.assertIsNone(result.candidate_response_time)

    def test_service_curve_validation_freezes_exact_deterministic_prefix(self):
        valid_curves = (
            ([0], 0),
            ([0, 0, 0], 2),
            ([0, 1, 1], 2),
            ([0, 1, 2, 4], 3),
            ([Fraction(0), Fraction(1, 3), Fraction(2, 3)], 2),
        )
        for beta, horizon in valid_curves:
            with self.subTest(beta=beta):
                frozen = v93.validate_service_curve_v9_3(beta, horizon)
                self.assertEqual(frozen[0], 0)
                self.assertTrue(
                    all(a <= b for a, b in zip(frozen, frozen[1:]))
                )

        calls = {0: 0, 1: 0, 2: 0}

        def nondeterministic(length):
            calls[length] += 1
            return length + (
                1 if length == 2 and calls[length] > 1 else 0
            )

        with self.assertRaises(v93.V93NumericError):
            v93.validate_service_curve_v9_3(nondeterministic, 2)

        def failing(length):
            if length == 1:
                raise RuntimeError("callback failure")
            return 0

        with self.assertRaises(v93.V93NumericError):
            v93.validate_service_curve_v9_3(failing, 2)

    def test_first_closing_w_is_returned_with_exact_counts(self):
        target = task("k", 1, 3, 4, 2)
        result = v93.canonical_closure_search_v9_3(
            v93.EnvelopeKind.COMPLETE,
            target,
            [],
            [],
            1,
            {},
            0,
            [0, 2, 4, 6],
        )
        self.assertEqual(result.candidate_response_time, 2)
        self.assertEqual(result.closing_w, 2)
        self.assertEqual(result.witness_h, 1)

    def test_repeatability_includes_result_and_all_counters(self):
        target = task("k", 2, 4, 5, 3)
        arguments = (
            v93.EnvelopeKind.LOCAL,
            target,
            [task("h", 1, 3, 4, 5)],
            [task("l", 1, 4, 5, 2)],
            2,
            {"h": 2},
            1,
            [0, 1, 2, 3, 4, 5, 6, 7],
        )
        first = v93.canonical_closure_search_v9_3(*arguments)
        second = v93.canonical_closure_search_v9_3(*arguments)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
