"""Exact SEQ-PH-LOC mathematical API tests."""

import inspect
from fractions import Fraction
from types import SimpleNamespace

import pytest

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_ph as ph
import asap_block_rta_v9_3_seq as seq
from v9_3_ph_bruteforce_oracle import brute_force_ph


def strict_seq_microcase():
    target = core.V93Task("k", 3, 5, 8, Fraction(3, 2))
    hp_tasks = (core.V93Task("h0", 2, 4, 5, Fraction(1, 4)),)
    lp_tasks = (
        core.V93Task("l0", 1, 4, 4, Fraction(4)),
        core.V93Task("l1", 1, 1, 4, Fraction(1, 3)),
    )
    return (
        target,
        hp_tasks,
        lp_tasks,
        3,
        {"h0": 2},
        Fraction(9, 2),
        tuple(map(Fraction, (0, 6, 6, 9, 11))),
    )


def search_candidate(**overrides):
    fields = {
        "solver_status": seq.SEQSearchStatus.CANDIDATE,
        "candidate_response_time": 1,
        "closing_w": 1,
        "processor_progress_a": 1,
        "maximum_blocking_h": 0,
        "witness_sequence": (0,),
        "witness_h": 0,
        "checked_w_count": 1,
        "checked_h_count": 1,
        "checked_q_count": 1,
        "envelope_call_count": 1,
        "impossible_prefix_count": 0,
        "failure_reason": None,
    }
    fields.update(overrides)
    return seq.SEQSearchResult(**fields)


def forged_closure(**overrides):
    """Build an untrusted hook result without invoking dataclass validation."""

    fields = {
        "status": seq.SEQClosureStatus.CLOSED,
        "witness_sequence": (0,),
        "witness_h": 0,
        "processor_progress_a": 1,
        "maximum_blocking_h": 0,
        "checked_h_count": 1,
        "checked_q_count": 1,
        "envelope_call_count": 1,
        "impossible_prefix_count": 0,
        "failure_reason": None,
    }
    fields.update(overrides)
    result = object.__new__(seq.SEQClosureResult)
    for name, value in fields.items():
        object.__setattr__(result, name, value)
    return result


def publish_hook_closure(closure):
    target = core.V93Task("k", 1, 1, 1, 1)
    return seq.seq_response_time_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0,),
        _closure_checker=lambda **_kwargs: closure,
    )


def test_strict_exact_microcase_has_seq_four_and_ph_five():
    target, hp_tasks, lp_tasks, processors, theta, e0, beta = (
        strict_seq_microcase()
    )
    phase = ph.ph_response_time_v9_3(
        target=target,
        hp_tasks=hp_tasks,
        lp_tasks=lp_tasks,
        processors=processors,
        theta_by_name=theta,
        e0=e0,
        beta=beta,
    )
    sequence = seq.seq_response_time_v9_3(
        target=target,
        hp_tasks=hp_tasks,
        lp_tasks=lp_tasks,
        processors=processors,
        theta_by_name=theta,
        e0=e0,
        beta=beta,
    )
    assert phase.solver_status is ph.PHSearchStatus.CANDIDATE
    assert sequence.solver_status is seq.SEQSearchStatus.CANDIDATE
    assert phase.candidate_response_time == 5
    assert phase.witness_h == 2
    assert sequence.candidate_response_time == 4
    assert sequence.processor_progress_a == 3
    assert sequence.maximum_blocking_h == 1
    assert sequence.witness_sequence == (0, 0, 1)
    assert sequence.witness_h == 1
    assert sequence.candidate_response_time < phase.candidate_response_time


def test_strict_microcase_matrix_is_exact_and_independently_bruteforced():
    target, hp_tasks, lp_tasks, processors, theta, e0, beta = (
        strict_seq_microcase()
    )
    w_value = 4
    assert core.processor_progress_v9_3(
        target, hp_tasks, w_value, processors, theta
    ) == 3
    expected_safe = {
        1: (True, True),
        2: (True, False),
        3: (False, True),
    }
    expected_energy = {
        (1, 0): Fraction(7, 4),
        (1, 1): Fraction(73, 12),
        (2, 0): Fraction(47, 6),
        (2, 1): Fraction(71, 6),
        (3, 0): Fraction(40, 3),
        (3, 1): Fraction(40, 3),
    }
    for q_value in range(1, 4):
        for h_value in range(2):
            brute_energy, _best_z, _witnesses = brute_force_ph(
                (
                    target,
                    hp_tasks,
                    lp_tasks,
                    w_value,
                    q_value,
                    h_value,
                    processors,
                    theta,
                )
            )
            assert brute_energy == expected_energy[(q_value, h_value)]
            actual = ph.phase_safe_v9_3(
                target=target,
                hp_tasks=hp_tasks,
                lp_tasks=lp_tasks,
                w=w_value,
                q=q_value,
                h=h_value,
                processors=processors,
                theta_by_name=theta,
                e0=e0,
                beta=beta,
            )
            assert (actual.status is ph.PHSafetyStatus.SAFE) == (
                expected_safe[q_value][h_value]
            )
            assert actual.envelope.energy == brute_energy
    assert not any(
        all(expected_safe[q_value][h_value] for q_value in range(1, 4))
        for h_value in range(2)
    )
    closure = seq.close_seq_v9_3(
        target=target,
        hp_tasks=hp_tasks,
        lp_tasks=lp_tasks,
        w=w_value,
        processors=processors,
        theta_by_name=theta,
        e0=e0,
        beta=beta,
    )
    assert closure.status is seq.SEQClosureStatus.CLOSED
    assert closure.witness_sequence == (0, 0, 1)
    assert all(
        expected_safe[q_value][closure.witness_sequence[q_value - 1]]
        for q_value in range(1, 4)
    )


def test_response_search_scans_every_w_and_returns_first_closure():
    target = core.V93Task("k", 2, 4, 4, 1)
    visited = []

    def checker(**kwargs):
        visited.append((kwargs["w"], kwargs["q"], kwargs["h"]))
        safe = kwargs["w"] >= 3 and kwargs["h"] == 1
        return SimpleNamespace(
            status=(
                ph.PHSafetyStatus.SAFE if safe else ph.PHSafetyStatus.UNSAFE
            ),
            reason="ENERGY_BOUND" if safe else "ENERGY_EXCEEDS_SERVICE",
        )

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
    assert result.candidate_response_time == 3
    assert result.checked_w_count == 2
    assert result.witness_sequence == (1, 1)
    assert visited == [
        (2, 1, 0),
        (3, 1, 0),
        (3, 1, 1),
        (3, 2, 1),
    ]


def test_no_candidate_has_no_fallback_or_witness():
    target = core.V93Task("k", 1, 3, 3, 2)

    def checker(**_kwargs):
        return SimpleNamespace(
            status=ph.PHSafetyStatus.UNSAFE,
            reason="ENERGY_EXCEEDS_SERVICE",
        )

    result = seq.seq_response_time_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0, 0, 0),
        _safety_checker=checker,
    )
    assert result.solver_status is seq.SEQSearchStatus.NO_CANDIDATE
    assert result.candidate_response_time is None
    assert result.closing_w is None
    assert result.processor_progress_a is None
    assert result.maximum_blocking_h is None
    assert result.witness_sequence == ()
    assert result.witness_h is None
    assert result.checked_w_count == 3


@pytest.mark.parametrize(
    ("safety_status", "closure_status", "search_status"),
    [
        (
            ph.PHSafetyStatus.UNPROVEN_TIMEOUT,
            seq.SEQClosureStatus.UNPROVEN_TIMEOUT,
            seq.SEQSearchStatus.UNPROVEN_TIMEOUT,
        ),
        (
            ph.PHSafetyStatus.UNPROVEN_NUMERIC,
            seq.SEQClosureStatus.UNPROVEN_NUMERIC,
            seq.SEQSearchStatus.UNPROVEN_NUMERIC,
        ),
        (
            ph.PHSafetyStatus.UNPROVEN_INTERNAL,
            seq.SEQClosureStatus.UNPROVEN_INTERNAL,
            seq.SEQSearchStatus.UNPROVEN_INTERNAL,
        ),
    ],
)
def test_unproven_ph_safety_statuses_propagate_without_later_calls(
    safety_status, closure_status, search_status
):
    target = core.V93Task("k", 1, 2, 2, 1)
    calls = []

    def checker(**kwargs):
        calls.append((kwargs["w"], kwargs["q"], kwargs["h"]))
        return SimpleNamespace(status=safety_status, reason=safety_status.value)

    closure = seq.close_seq_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        w=2,
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0, 0),
        _safety_checker=checker,
    )
    assert closure.status is closure_status
    assert calls == [(2, 1, 0)]
    calls.clear()
    response = seq.seq_response_time_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0, 0),
        _safety_checker=checker,
    )
    assert response.solver_status is search_status
    assert calls == [(1, 1, 0)]


def test_invalid_exact_energy_cannot_hide_behind_progress_failure(monkeypatch):
    target = core.V93Task("k", 1, 1, 1, 1)
    monkeypatch.setattr(core, "processor_progress_v9_3", lambda *_args: 2)
    result = seq.close_seq_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        w=1,
        processors=1,
        theta_by_name={},
        e0=0.0,
        beta=(0,),
    )
    assert result.status is seq.SEQClosureStatus.UNPROVEN_NUMERIC


def test_result_structures_reject_fake_or_malformed_witnesses():
    with pytest.raises(core.V93InputError):
        seq.SEQClosureResult(
            seq.SEQClosureStatus.CLOSED, (1,), 1, 2, 2, 0, 0, 0, 0
        )
    with pytest.raises(core.V93InputError):
        seq.SEQClosureResult(
            seq.SEQClosureStatus.NOT_CLOSED, (0,), 0, 1, 0, 0, 0, 0, 0
        )
    with pytest.raises(core.V93InputError):
        seq.SEQSearchResult(
            solver_status=seq.SEQSearchStatus.NO_CANDIDATE,
            candidate_response_time=None,
            closing_w=None,
            processor_progress_a=None,
            maximum_blocking_h=None,
            witness_sequence=(0,),
            witness_h=0,
            checked_w_count=0,
            checked_h_count=0,
            checked_q_count=0,
            envelope_call_count=0,
            impossible_prefix_count=0,
        )


def test_search_candidate_carries_a_h_and_accepts_one_valid_certificate():
    result = search_candidate()
    assert result.processor_progress_a == 1
    assert result.maximum_blocking_h == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"witness_sequence": (99,), "witness_h": 99},
        {
            "candidate_response_time": 2,
            "closing_w": 2,
            "processor_progress_a": 1,
            "maximum_blocking_h": 0,
        },
        {
            "candidate_response_time": 2,
            "closing_w": 2,
            "processor_progress_a": 2,
            "maximum_blocking_h": 0,
            "witness_sequence": (0,),
        },
        {
            "candidate_response_time": 2,
            "closing_w": 2,
            "maximum_blocking_h": 1,
            "witness_sequence": (-1,),
            "witness_h": -1,
        },
        {
            "candidate_response_time": 2,
            "closing_w": 2,
            "maximum_blocking_h": 1,
            "witness_sequence": (2,),
            "witness_h": 2,
        },
        {
            "candidate_response_time": 3,
            "closing_w": 3,
            "processor_progress_a": 2,
            "maximum_blocking_h": 1,
            "witness_sequence": (1, 0),
            "witness_h": 0,
        },
        {"witness_h": 1},
        {"closing_w": 2},
        {"failure_reason": "CANDIDATE may not carry failure state"},
    ],
)
def test_search_candidate_rejects_malformed_certificate(overrides):
    with pytest.raises(core.V93InputError):
        search_candidate(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "solver_status": seq.SEQSearchStatus.NO_CANDIDATE,
            "candidate_response_time": None,
            "closing_w": None,
            "processor_progress_a": 1,
            "maximum_blocking_h": 0,
            "witness_sequence": (),
            "witness_h": None,
        },
        {
            "solver_status": seq.SEQSearchStatus.UNPROVEN_INTERNAL,
            "candidate_response_time": None,
            "closing_w": None,
            "processor_progress_a": None,
            "maximum_blocking_h": None,
            "witness_sequence": (0,),
            "witness_h": 0,
        },
    ],
)
def test_non_candidate_rejects_partial_certificate(overrides):
    with pytest.raises(core.V93InputError):
        search_candidate(**overrides)


def test_valid_hook_closure_is_rebound_and_published_with_real_a_h():
    result = publish_hook_closure(forged_closure())
    assert result.solver_status is seq.SEQSearchStatus.CANDIDATE
    assert result.candidate_response_time == 1
    assert result.processor_progress_a == 1
    assert result.maximum_blocking_h == 0
    assert result.witness_sequence == (0,)


@pytest.mark.parametrize(
    "closure",
    [
        forged_closure(
            processor_progress_a=2,
            maximum_blocking_h=0,
            witness_sequence=(0, 0),
            witness_h=0,
        ),
        forged_closure(
            maximum_blocking_h=99,
            witness_sequence=(99,),
            witness_h=99,
        ),
        forged_closure(witness_sequence=(), witness_h=None),
        forged_closure(witness_sequence=(-1,), witness_h=-1),
        forged_closure(witness_sequence=(1,), witness_h=1),
        forged_closure(witness_h=1),
        forged_closure(failure_reason="spoofed CLOSED failure"),
    ],
)
def test_response_rejects_spoofed_closure_certificates(closure):
    result = publish_hook_closure(closure)
    assert result.solver_status is seq.SEQSearchStatus.UNPROVEN_INTERNAL
    assert result.candidate_response_time is None
    assert result.closing_w is None
    assert result.processor_progress_a is None
    assert result.maximum_blocking_h is None
    assert result.witness_sequence == ()
    assert result.witness_h is None
    assert "closure certificate mismatch" in result.failure_reason


def test_response_rejects_descending_spoofed_closure_sequence():
    target = core.V93Task("k", 2, 3, 3, 1)
    descending = forged_closure(
        processor_progress_a=2,
        maximum_blocking_h=1,
        witness_sequence=(1, 0),
        witness_h=0,
    )

    def closure(**kwargs):
        if kwargs["w"] == 2:
            return seq.SEQClosureResult(
                seq.SEQClosureStatus.NOT_CLOSED,
                (),
                None,
                2,
                0,
                0,
                0,
                0,
                0,
            )
        return descending

    result = seq.seq_response_time_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        processors=1,
        theta_by_name={},
        e0=0,
        beta=(0, 0, 0),
        _closure_checker=closure,
    )
    assert result.solver_status is seq.SEQSearchStatus.UNPROVEN_INTERNAL
    assert result.candidate_response_time is None
    assert result.witness_sequence == ()
    assert "closure certificate mismatch" in result.failure_reason


def test_production_default_is_exact_ph_safety_and_has_no_energy_model_copy():
    assert (
        inspect.signature(seq.close_seq_v9_3)
        .parameters["_safety_checker"]
        .default
        is ph.phase_safe_v9_3
    )
    source = inspect.getsource(seq)
    for forbidden in (
        "def _build_branch",
        "def _witness_from_flow",
        "def phase_energy_envelope_v9_3",
    ):
        assert forbidden not in source
