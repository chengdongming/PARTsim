"""Directed exact-greedy and shared-deadline tests for SEQ-PH-LOC."""

from fractions import Fraction
from types import SimpleNamespace

import pytest

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_ph as ph
import asap_block_rta_v9_3_seq as seq


class TriggerClock:
    """Return one exactly at and after a deterministic read boundary."""

    def __init__(self, trigger_read):
        self.trigger_read = trigger_read
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return 1 if self.reads >= self.trigger_read else 0


class ArmableClock:
    def __init__(self):
        self.armed = False

    def __call__(self):
        return 1 if self.armed else 0


def safety_result(safe, reason="ENERGY_BOUND"):
    return SimpleNamespace(
        status=ph.PHSafetyStatus.SAFE if safe else ph.PHSafetyStatus.UNSAFE,
        reason=reason,
    )


def close_matrix(safe_by_q, *, a_value, h_max, checker_hook=None):
    target = core.V93Task("k", a_value, a_value + h_max, a_value + h_max, 1)
    calls = []

    def checker(**kwargs):
        point = (kwargs["q"], kwargs["h"])
        calls.append(point)
        if checker_hook is not None:
            checker_hook(point)
        return safety_result(kwargs["h"] in safe_by_q[kwargs["q"]])

    result = seq.close_seq_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        w=a_value + h_max,
        processors=1,
        theta_by_name={},
        e0=0,
        beta=tuple(Fraction(0) for _ in range(a_value + h_max)),
        _safety_checker=checker,
    )
    return result, calls


def test_case_a_no_common_h_but_seq_finds_prefix_sequence():
    result, calls = close_matrix(
        {1: {1}, 2: {2}, 3: {2}}, a_value=3, h_max=2
    )
    assert result.status is seq.SEQClosureStatus.CLOSED
    assert result.witness_sequence == (1, 2, 2)
    assert not set.intersection({1}, {2}, {2})
    assert calls == [(1, 0), (1, 1), (2, 1), (2, 2), (3, 2)]


def test_case_b_independent_minimum_sort_is_invalid_but_greedy_is_exact():
    result, calls = close_matrix(
        {1: {2}, 2: {1, 3}}, a_value=2, h_max=3
    )
    assert result.status is seq.SEQClosureStatus.CLOSED
    assert result.witness_sequence == (2, 3)
    assert tuple(sorted((2, 1))) == (1, 2)
    assert 1 not in {2}
    assert 2 not in {1, 3}
    assert calls == [(1, 0), (1, 1), (1, 2), (2, 2), (2, 3)]


def test_case_c_greedy_failure_has_no_feasible_sequence_or_fake_witness():
    result, calls = close_matrix(
        {1: {1}, 2: {0}}, a_value=2, h_max=2
    )
    assert result.status is seq.SEQClosureStatus.NOT_CLOSED
    assert result.witness_sequence == ()
    assert result.witness_h is None
    assert calls == [(1, 0), (1, 1), (2, 1), (2, 2)]


def test_case_d_nonmonotone_safety_scans_through_unsafe_middle_values():
    result, calls = close_matrix(
        {1: {0, 2}, 2: {2}}, a_value=2, h_max=2
    )
    assert result.status is seq.SEQClosureStatus.CLOSED
    assert result.witness_sequence == (0, 2)
    assert calls == [(1, 0), (2, 0), (2, 1), (2, 2)]


def test_impossible_prefix_is_counted_as_a_safe_checkpoint():
    target = core.V93Task("k", 1, 1, 1, 1)

    def checker(**_kwargs):
        return safety_result(True, ph.PHEnvelopeStatus.IMPOSSIBLE_PREFIX.value)

    result = seq.close_seq_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        w=1,
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0,),
        _safety_checker=checker,
    )
    assert result.status is seq.SEQClosureStatus.CLOSED
    assert result.witness_sequence == (0,)
    assert result.impossible_prefix_count == 1


@pytest.mark.parametrize(
    ("trigger_read", "checkpoint"),
    [
        (5, "after_progress"),
        (7, "h_entry"),
        (11, "h_entry"),
        (11, "before_closed"),
    ],
)
def test_deterministic_closure_clock_boundaries_fail_closed(
    trigger_read, checkpoint
):
    # trigger=11 has two distinct paths: three unsafe points expire at the
    # third h entry, whereas one successful q expires at CLOSED publication.
    safe_by_q = {1: set()} if checkpoint == "h_entry" else {1: {0}}
    h_max = 3 if trigger_read == 11 and checkpoint == "h_entry" else 0
    target = core.V93Task("k", 1, 1 + h_max, 1 + h_max, 1)
    clock = TriggerClock(trigger_read)

    def checker(**kwargs):
        return safety_result(kwargs["h"] in safe_by_q[kwargs["q"]])

    result = seq.close_seq_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        w=1 + h_max,
        processors=1,
        theta_by_name={},
        e0=0,
        beta=tuple(0 for _ in range(1 + h_max)),
        timeout_seconds=1,
        clock=clock,
        _safety_checker=checker,
    )
    assert result.status is seq.SEQClosureStatus.UNPROVEN_TIMEOUT
    assert checkpoint in result.failure_reason
    assert result.witness_sequence == ()


def test_safe_return_expiring_before_selection_is_timeout():
    target = core.V93Task("k", 1, 1, 1, 1)
    clock = ArmableClock()

    def checker(**_kwargs):
        clock.armed = True
        return safety_result(True)

    result = seq.close_seq_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        w=1,
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0,),
        timeout_seconds=1,
        clock=clock,
        _safety_checker=checker,
    )
    assert result.status is seq.SEQClosureStatus.UNPROVEN_TIMEOUT
    assert "safety_returned" in result.failure_reason


def test_progress_expiration_has_priority_over_a_greater_than_w(monkeypatch):
    target = core.V93Task("k", 1, 1, 1, 1)
    clock = ArmableClock()

    def progress(*_args, **_kwargs):
        clock.armed = True
        return 2

    monkeypatch.setattr(core, "processor_progress_v9_3", progress)
    result = seq.close_seq_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        w=1,
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0,),
        timeout_seconds=1,
        clock=clock,
    )
    assert result.status is seq.SEQClosureStatus.UNPROVEN_TIMEOUT
    assert "after_progress" in result.failure_reason


def test_closure_return_expiration_prevents_candidate_publication():
    target = core.V93Task("k", 1, 1, 1, 1)
    clock = ArmableClock()

    def closure(**_kwargs):
        clock.armed = True
        return seq.SEQClosureResult(
            seq.SEQClosureStatus.CLOSED,
            (0,),
            0,
            1,
            0,
            1,
            1,
            1,
            0,
        )

    result = seq.seq_response_time_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0,),
        timeout_seconds=1,
        clock=clock,
        _closure_checker=closure,
    )
    assert result.solver_status is seq.SEQSearchStatus.UNPROVEN_TIMEOUT
    assert "closure_returned" in result.failure_reason
    assert result.candidate_response_time is None


def test_rebinding_progress_expiration_prevents_candidate_publication(monkeypatch):
    target = core.V93Task("k", 1, 1, 1, 1)
    clock = ArmableClock()
    original_progress = core.processor_progress_v9_3

    def progress(*args, **kwargs):
        value = original_progress(*args, **kwargs)
        clock.armed = True
        return value

    monkeypatch.setattr(core, "processor_progress_v9_3", progress)

    def closure(**_kwargs):
        return seq.SEQClosureResult(
            seq.SEQClosureStatus.CLOSED,
            (0,),
            0,
            1,
            0,
            1,
            1,
            1,
            0,
        )

    result = seq.seq_response_time_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0,),
        timeout_seconds=1,
        clock=clock,
        _closure_checker=closure,
    )
    assert result.solver_status is seq.SEQSearchStatus.UNPROVEN_TIMEOUT
    assert "after_rebinding_progress" in result.failure_reason
    assert result.candidate_response_time is None


@pytest.mark.parametrize(
    ("trigger_read", "checkpoint"),
    [
        (8, "before_certificate_compare"),
        (9, "before_candidate"),
    ],
)
def test_rebinding_publication_boundaries_prefer_timeout(
    trigger_read, checkpoint
):
    target = core.V93Task("k", 1, 1, 1, 1)
    clock = TriggerClock(trigger_read)

    def closure(**_kwargs):
        return seq.SEQClosureResult(
            seq.SEQClosureStatus.CLOSED,
            (0,),
            0,
            1,
            0,
            1,
            1,
            1,
            0,
        )

    result = seq.seq_response_time_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0,),
        timeout_seconds=1,
        clock=clock,
        _closure_checker=closure,
    )
    assert result.solver_status is seq.SEQSearchStatus.UNPROVEN_TIMEOUT
    assert checkpoint in result.failure_reason
    assert result.candidate_response_time is None


def test_certificate_mismatch_loses_to_simultaneous_timeout():
    target = core.V93Task("k", 1, 1, 1, 1)
    clock = TriggerClock(9)

    def closure(**_kwargs):
        return seq.SEQClosureResult(
            seq.SEQClosureStatus.CLOSED,
            (99,),
            99,
            1,
            99,
            1,
            1,
            1,
            0,
        )

    result = seq.seq_response_time_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0,),
        timeout_seconds=1,
        clock=clock,
        _closure_checker=closure,
    )
    assert result.solver_status is seq.SEQSearchStatus.UNPROVEN_TIMEOUT
    assert "certificate_mismatch" in result.failure_reason
    assert result.candidate_response_time is None


def test_candidate_construction_expiration_is_timeout(monkeypatch):
    target = core.V93Task("k", 1, 1, 1, 1)
    clock = ArmableClock()
    original_post_init = seq.SEQSearchResult.__post_init__
    candidate_construction_clock_values = []

    def wrapped_post_init(result):
        original_post_init(result)
        if result.solver_status is seq.SEQSearchStatus.CANDIDATE:
            candidate_construction_clock_values.append(clock())
            clock.armed = True

    monkeypatch.setattr(seq.SEQSearchResult, "__post_init__", wrapped_post_init)

    def closure(**_kwargs):
        return seq.SEQClosureResult(
            seq.SEQClosureStatus.CLOSED,
            (0,),
            0,
            1,
            0,
            1,
            1,
            1,
            0,
        )

    result = seq.seq_response_time_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0,),
        timeout_seconds=1,
        clock=clock,
        _closure_checker=closure,
    )
    assert candidate_construction_clock_values == [0]
    assert result.solver_status is seq.SEQSearchStatus.UNPROVEN_TIMEOUT
    assert "candidate_constructed" in result.failure_reason
    assert result.candidate_response_time is None
    assert result.closing_w is None
    assert result.processor_progress_a is None
    assert result.maximum_blocking_h is None
    assert result.witness_sequence == ()
    assert result.witness_h is None


def test_final_no_candidate_boundary_is_checked_without_sleep():
    target = core.V93Task("k", 1, 1, 1, 1)
    clock = TriggerClock(7)

    def closure(**_kwargs):
        return seq.SEQClosureResult(
            seq.SEQClosureStatus.NOT_CLOSED,
            (),
            None,
            2,
            -1,
            0,
            0,
            0,
            0,
        )

    result = seq.seq_response_time_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0,),
        timeout_seconds=1,
        clock=clock,
        _closure_checker=closure,
    )
    assert result.solver_status is seq.SEQSearchStatus.UNPROVEN_TIMEOUT
    assert "before_no_candidate" in result.failure_reason


def test_shared_deadline_object_is_identical_for_every_safety_call():
    target = core.V93Task("k", 2, 4, 4, 1)
    observed = []

    def checker(**kwargs):
        observed.append(kwargs["_deadline"])
        return safety_result(kwargs["h"] == kwargs["q"] - 1)

    result = seq.seq_response_time_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0, 0, 0, 0),
        _safety_checker=checker,
    )
    assert result.solver_status is seq.SEQSearchStatus.CANDIDATE
    assert observed
    assert len({id(deadline) for deadline in observed}) == 1
