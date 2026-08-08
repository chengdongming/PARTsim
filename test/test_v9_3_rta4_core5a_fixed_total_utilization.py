from __future__ import annotations

from copy import deepcopy
from fractions import Fraction

import pytest

from experiments.common.exact_service_curve import (
    EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
)
from experiments.v9_3.rta4_formal_config_v3 import (
    RTA4FormalConfigV3Error,
    normalize_rta4_campaign_v3,
    rta4_formal_config_hash_v3,
)
from experiments.v9_3.rta4_formal_config_v5 import (
    CORE5A_FIXED_E0_V1,
    CORE5A_FIXED_TICK_SERVICE_V1,
    CORE5A_SCALED_E0_V1,
    CORE5A_SCALED_LATENCY_SERVICE_V1,
    RTA4FormalConfigV5Error,
    normalize_rta4_campaign_v5,
    rta4_formal_config_hash_v5,
)
from experiments.v9_3.rta4_formal_plan_v3 import (
    describe_formal_plan_v3,
    iter_formal_plan_v3,
)
from experiments.v9_3.rta4_formal_plan_v5 import (
    describe_formal_plan_v5,
    iter_formal_plan_v5,
)
from experiments.v9_3.rta4_task_source_v4 import (
    GENERAL_RANDOM_CONSTRAINED_V1,
    GENERATED_FAMILY,
    PRIORITY_POLICY_RM,
)
from scripts.create_v9_3_rta4_campaign import campaign_template


METHODS = [
    "CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ",
]
SERVICE = {
    "model": EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
    "rate": "1/10",
    "latency": "1",
    "time_unit": "tick",
}


def _legacy_formal_v3_raw() -> dict:
    raw = campaign_template("CORE-5A")
    raw["campaign_id"] = "legacy-core5a-baseline-audit"
    return raw


def _fixed_formal_v3_raw() -> dict:
    raw = campaign_template("CORE-5A")
    raw["campaign_id"] = "core5a-fixed-total-utilization-regression"
    raw["processor_axis"] = {
        **raw["processor_axis"],
        "fixed_total_utilization": "8/5",
    }
    return raw


def _tolerant_formal_v3_raw(tolerance: object = "1/100") -> dict:
    raw = _fixed_formal_v3_raw()
    raw["processor_axis"][
        "fixed_total_utilization_tolerance"
    ] = tolerance
    return raw


def test_legacy_core5a_config_plan_and_stream_identities_are_unchanged():
    scientific = normalize_rta4_campaign_v3(
        _legacy_formal_v3_raw()
    )["normalized_scientific_config"]
    plan = describe_formal_plan_v3(scientific)

    assert "fixed_total_utilization" not in scientific["processor_axis"]
    assert "fixed_total_utilization_tolerance" not in scientific["processor_axis"]
    assert rta4_formal_config_hash_v3(scientific) == (
        "6cd631ee3d4107fc0fd849474fa985f59b9010071d293023668f90f4feb907d1"
    )
    assert plan["plan_sha256"] == (
        "28c83681497c1e5bec8d12247c449ab98a816a5e603a07cd29e0c7466e31c965"
    )
    assert plan["ordered_stream_digest"] == (
        "649b61538a718b9c36822f4d0fa5b2b22beb29a9bdb3b8ed8a7b55641f5ba812"
    )
    assert (
        plan["taskset_skeleton_count"],
        plan["mathematical_request_count"],
        plan["ordered_stream_count"],
    ) == (1100, 4400, 4400)


def test_fixed_total_processor_axis_uses_per_processor_utilization_only_there():
    scientific = normalize_rta4_campaign_v3(
        _fixed_formal_v3_raw()
    )["normalized_scientific_config"]
    plan = describe_formal_plan_v3(scientific)
    records = tuple(iter_formal_plan_v3(scientific))

    assert "fixed_total_utilization_tolerance" not in scientific["processor_axis"]
    assert (
        plan["taskset_skeleton_count"],
        plan["mathematical_request_count"],
        plan["ordered_stream_count"],
    ) == (1100, 4400, 4400)
    processor_records = [
        record for record in records
        if record.material["axis"] == "processor_count"
    ]
    assert {
        (
            record.material["axis_value"],
            record.material["normalized_utilization"],
            record.material["total_utilization"],
        )
        for record in processor_records
    } == {
        ("2", "4/5", "8/5"),
        ("4", "2/5", "8/5"),
        ("8", "1/5", "8/5"),
    }
    assert len(processor_records) == 1200
    task_count_records = [
        record for record in records
        if record.material["axis"] == "task_count"
    ]
    time_scale_records = [
        record for record in records
        if record.material["axis"] == "integer_time_scale"
    ]
    assert len(task_count_records) == 1600
    assert len(time_scale_records) == 1600
    assert all(
        record.material["normalized_utilization"] == "1/2"
        and "total_utilization" not in record.material
        for record in task_count_records + time_scale_records
    )
    assert all(
        "fixed_total_utilization_tolerance" not in record.material
        for record in records
    )


def test_tolerance_changes_config_and_plan_identity_not_request_stream():
    raw = _fixed_formal_v3_raw()
    raw["task_count_axis"] = {"values": [2], "processors": 2, "tasksets": 1}
    raw["processor_axis"].update({"values": [2], "task_count": 2, "tasksets": 1})
    raw["integer_time_scale_axis"] = {"values": [1], "base_tasksets": 1}
    raw["methods"] = [METHODS[0]]
    exact = normalize_rta4_campaign_v3(raw)["normalized_scientific_config"]
    tolerant_raw = deepcopy(raw)
    tolerant_raw["processor_axis"][
        "fixed_total_utilization_tolerance"
    ] = "1/100"
    tolerant = normalize_rta4_campaign_v3(
        tolerant_raw
    )["normalized_scientific_config"]
    exact_plan = describe_formal_plan_v3(exact)
    tolerant_plan = describe_formal_plan_v3(tolerant)

    assert rta4_formal_config_hash_v3(exact) != rta4_formal_config_hash_v3(
        tolerant
    )
    assert exact_plan["plan_sha256"] != tolerant_plan["plan_sha256"]
    assert (
        exact_plan["ordered_stream_digest"]
        == tolerant_plan["ordered_stream_digest"]
    )
    assert all(
        "fixed_total_utilization_tolerance" not in record.material
        for record in iter_formal_plan_v3(tolerant)
    )


def test_fixed_total_processor_axis_requires_positive_exact_capacity():
    raw = _fixed_formal_v3_raw()
    raw["processor_axis"]["fixed_total_utilization"] = 1.6
    with pytest.raises(RTA4FormalConfigV3Error, match="exact rational string"):
        normalize_rta4_campaign_v3(raw)

    raw = _fixed_formal_v3_raw()
    raw["processor_axis"]["fixed_total_utilization"] = "0"
    with pytest.raises(RTA4FormalConfigV3Error, match="greater"):
        normalize_rta4_campaign_v3(raw)

    raw = _fixed_formal_v3_raw()
    raw["processor_axis"]["fixed_total_utilization"] = "9"
    with pytest.raises(RTA4FormalConfigV3Error, match="capacity"):
        normalize_rta4_campaign_v3(raw)


@pytest.mark.parametrize("tolerance", ["1/100", "0"])
def test_fixed_total_processor_tolerance_accepts_canonical_nonnegative_values(
    tolerance,
):
    scientific = normalize_rta4_campaign_v3(
        _tolerant_formal_v3_raw(tolerance)
    )["normalized_scientific_config"]

    assert scientific["processor_axis"][
        "fixed_total_utilization_tolerance"
    ] == tolerance


@pytest.mark.parametrize(
    ("tolerance", "message"),
    [
        (0.01, "exact rational string"),
        ("-1/100", "below its allowed range"),
        ("2/200", "canonical exact rational string"),
    ],
)
def test_fixed_total_processor_tolerance_rejects_invalid_values(
    tolerance, message,
):
    with pytest.raises(RTA4FormalConfigV3Error, match=message):
        normalize_rta4_campaign_v3(_tolerant_formal_v3_raw(tolerance))


def test_fixed_total_processor_tolerance_requires_fixed_total_utilization():
    raw = campaign_template("CORE-5A")
    raw["processor_axis"] = {
        **raw["processor_axis"],
        "fixed_total_utilization_tolerance": "1/100",
    }

    with pytest.raises(RTA4FormalConfigV3Error, match="field set mismatch"):
        normalize_rta4_campaign_v3(raw)


def _fixed_total_source(*, processors: int, tasksets: int = 2) -> dict:
    return {
        "mode": GENERATED_FAMILY,
        "family_id": GENERAL_RANDOM_CONSTRAINED_V1,
        "parameters": {
            "processors": processors,
            "priority_policy": PRIORITY_POLICY_RM,
            "task_count": 2,
            "taskset_count": tasksets,
            "base_seed": 100,
            "generation_indices": list(range(tasksets)),
            "task_templates": [
                {
                    "name": "tau_1", "C": [4], "D": [4], "T": [5],
                    "power": ["1/10"],
                },
                {
                    "name": "tau_2", "C": [8], "D": [8], "T": [10],
                    "power": ["1/5"],
                },
            ],
        },
    }


def _small_fixed_v5_raw(*, tolerance: str | None = None) -> dict:
    raw = campaign_template("CORE-5A")
    raw.update({
        "campaign_id": "core5a-fixed-total-v5-regression",
        "baseline": {
            "e0": "0",
            "normalized_utilization": "1/2",
            "service_scale": "1",
            "power_scale": "1",
            "deadline_slack_fraction": "3/4",
        },
        "task_count_axis": {"values": [2], "processors": 2, "tasksets": 2},
        "processor_axis": {
            "values": [2, 4, 8],
            "task_count": 2,
            "tasksets": 2,
            "fixed_total_utilization": "8/5",
        },
        "integer_time_scale_axis": {"values": [1], "base_tasksets": 2},
        "methods": list(METHODS),
        "integer_time_scale_service_semantics": (
            "FIXED_TICK_SERVICE_PARAMETERS_V1"
        ),
        "task_sources": [
            {
                "axis": "task_count", "axis_value": 2,
                "task_source": _fixed_total_source(processors=2),
            },
            *(
                {
                    "axis": "processor_count", "axis_value": processors,
                    "task_source": _fixed_total_source(processors=processors),
                }
                for processors in (2, 4, 8)
            ),
            {
                "axis": "integer_time_scale", "axis_value": 1,
                "task_source": _fixed_total_source(processors=2),
            },
        ],
        "service_curve": deepcopy(SERVICE),
        "runtime": {},
    })
    if tolerance is not None:
        raw["processor_axis"][
            "fixed_total_utilization_tolerance"
        ] = tolerance
    return raw


def _processor_source(raw: dict, processors: int) -> dict:
    return next(
        row["task_source"] for row in raw["task_sources"]
        if row["axis"] == "processor_count" and row["axis_value"] == processors
    )


def _set_processor_second_task(
    raw: dict, *, C: int, D: int, T: int,
) -> None:
    for processors in (2, 4, 8):
        template = _processor_source(raw, processors)["parameters"][
            "task_templates"
        ][1]
        template.update({"C": [C], "D": [D], "T": [T]})


def test_v5_fixed_total_processor_sources_pair_exact_tasks_across_m():
    normalized = normalize_rta4_campaign_v5(_small_fixed_v5_raw())
    processor_sources = [
        binding.source for binding in normalized["task_sources"]
        if binding.axis == "processor_count"
    ]

    assert [source.processors for source in processor_sources] == [2, 4, 8]
    for replicate in range(2):
        reference = processor_sources[0].taskset(replicate).material(
            include_identity=False
        )
        assert all(
            source.taskset(replicate).material(include_identity=False)
            == reference
            for source in processor_sources
        )
        assert {
            taskset.taskset(replicate).source_seed
            for taskset in processor_sources
        } == {100 + replicate}


@pytest.mark.parametrize(
    ("C", "T", "observed_total", "difference"),
    [
        (159, 200, Fraction(319, 200), Fraction(1, 200)),
        (79, 100, Fraction(159, 100), Fraction(1, 100)),
    ],
)
def test_v5_fixed_total_processor_tolerance_accepts_paired_integer_rounding(
    C, T, observed_total, difference,
):
    raw = _small_fixed_v5_raw(tolerance="1/100")
    _set_processor_second_task(raw, C=C, D=C, T=T)

    normalized = normalize_rta4_campaign_v5(raw)
    processor_sources = [
        binding.source for binding in normalized["task_sources"]
        if binding.axis == "processor_count"
    ]

    for replicate in range(2):
        reference = processor_sources[0].taskset(replicate)
        actual_total = sum(
            (Fraction(task.C, task.T) for task in reference.tasks),
            Fraction(0),
        )
        assert actual_total == observed_total
        assert actual_total != Fraction(8, 5)
        assert abs(actual_total - Fraction(8, 5)) == difference
        assert all(
            source.taskset(replicate).material(include_identity=False)
            == reference.material(include_identity=False)
            and source.taskset(replicate).source_seed == reference.source_seed
            for source in processor_sources
        )


@pytest.mark.parametrize(
    ("tolerance", "C", "message"),
    [
        ("1/100", 78, "exceeds allowed tolerance"),
        ("0", 79, "exceeds allowed tolerance"),
        (None, 79, "utilization mismatch"),
    ],
)
def test_v5_fixed_total_processor_tolerance_rejects_outside_contract(
    tolerance, C, message,
):
    raw = _small_fixed_v5_raw(tolerance=tolerance)
    _set_processor_second_task(raw, C=C, D=C, T=100)

    with pytest.raises(RTA4FormalConfigV5Error, match=message):
        normalize_rta4_campaign_v5(raw)


@pytest.mark.parametrize(
    "field", ["name", "C", "D", "T", "power", "source_seed"],
)
def test_v5_fixed_total_processor_source_rejects_any_pairing_drift(field):
    raw = _small_fixed_v5_raw(tolerance="1/100")
    source = _processor_source(raw, 8)
    if field == "source_seed":
        source["parameters"]["base_seed"] += 1
    else:
        template = source["parameters"]["task_templates"][0]
        if field == "name":
            template[field] = "tau_changed"
        elif field == "power":
            template[field] = ["1/9"]
        elif field == "C":
            template[field] = [3]
        else:
            template[field] = [template[field][0] + 1]
    with pytest.raises(
        RTA4FormalConfigV5Error,
        match="pair exact tasksets|differ beyond processors",
    ):
        normalize_rta4_campaign_v5(raw)


def test_v5_fixed_total_processor_source_rejects_nonprocessor_config_drift():
    raw = _small_fixed_v5_raw(tolerance="1/100")
    _processor_source(raw, 8)["parameters"]["generation_indices"] = [1, 0]

    with pytest.raises(
        RTA4FormalConfigV5Error,
        match="differ beyond processors",
    ):
        normalize_rta4_campaign_v5(raw)


def test_v5_fixed_total_processor_source_rejects_wrong_total_utilization():
    raw = _small_fixed_v5_raw()
    for processors in (2, 4, 8):
        source = _processor_source(raw, processors)
        source["parameters"]["task_templates"][0]["C"] = [3]
        source["parameters"]["task_templates"][0]["D"] = [4]
    with pytest.raises(RTA4FormalConfigV5Error, match="utilization mismatch"):
        normalize_rta4_campaign_v5(raw)


def _a3_task_source(
    *, processors: int, task_count: int, tasksets: int, time_scale: int = 1,
) -> dict:
    return {
        "mode": GENERATED_FAMILY,
        "family_id": GENERAL_RANDOM_CONSTRAINED_V1,
        "parameters": {
            "processors": processors,
            "priority_policy": PRIORITY_POLICY_RM,
            "task_count": task_count,
            "taskset_count": tasksets,
            "base_seed": 500,
            "generation_indices": list(range(tasksets)),
            "task_templates": [
                {
                    "name": f"tau_{index + 1}",
                    "C": [time_scale],
                    "D": [(index + 1) * 8 * time_scale],
                    "T": [(index + 1) * 10 * time_scale],
                    "power": ["10"],
                }
                for index in range(task_count)
            ],
        },
    }


def _a3_v5_raw(*, scaled: bool, full_grid: bool = False) -> dict:
    raw = campaign_template("CORE-5A")
    if not full_grid:
        raw["task_count_axis"] = {
            "values": [2], "processors": 2, "tasksets": 1,
        }
        raw["processor_axis"] = {
            "values": [2], "task_count": 2, "tasksets": 1,
        }
        raw["integer_time_scale_axis"] = {
            "values": [1, 2, 4, 8], "base_tasksets": 1,
        }
        raw["methods"] = [METHODS[0]]
    raw.update({
        "campaign_id": "core5a-a3-scaled-time-semantics-regression",
        "baseline": {
            "e0": "37",
            "normalized_utilization": "1/2",
            "service_scale": "1",
            "power_scale": "1",
            "deadline_slack_fraction": "3/4",
        },
        "service_curve": {
            "model": EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
            "rate": "11/2",
            "latency": "2/5",
            "time_unit": "tick",
        },
        "integer_time_scale_service_semantics": (
            CORE5A_SCALED_LATENCY_SERVICE_V1
            if scaled else CORE5A_FIXED_TICK_SERVICE_V1
        ),
        "runtime": {},
    })
    if scaled:
        raw["integer_time_scale_e0_semantics"] = CORE5A_SCALED_E0_V1

    task_axis = raw["task_count_axis"]
    processor_axis = raw["processor_axis"]
    time_axis = raw["integer_time_scale_axis"]
    raw["task_sources"] = [
        *(
            {
                "axis": "task_count",
                "axis_value": task_count,
                "task_source": _a3_task_source(
                    processors=task_axis["processors"],
                    task_count=task_count,
                    tasksets=task_axis["tasksets"],
                ),
            }
            for task_count in task_axis["values"]
        ),
        *(
            {
                "axis": "processor_count",
                "axis_value": processors,
                "task_source": _a3_task_source(
                    processors=processors,
                    task_count=processor_axis["task_count"],
                    tasksets=processor_axis["tasksets"],
                ),
            }
            for processors in processor_axis["values"]
        ),
        *(
            {
                "axis": "integer_time_scale",
                "axis_value": scale,
                "task_source": _a3_task_source(
                    processors=task_axis["processors"],
                    task_count=processor_axis["task_count"],
                    tasksets=time_axis["base_tasksets"],
                    time_scale=scale,
                ),
            }
            for scale in time_axis["values"]
        ),
    ]
    return raw


def _normalize_a3(*, scaled: bool, full_grid: bool = False):
    normalized = normalize_rta4_campaign_v5(
        _a3_v5_raw(scaled=scaled, full_grid=full_grid)
    )
    scientific = normalized["normalized_scientific_config"]
    records = tuple(iter_formal_plan_v5(
        scientific, normalized["task_sources"], normalized["service_curve"],
    ))
    return normalized, records


def test_v5_legacy_fixed_e0_normalized_and_plan_identities_are_unchanged():
    normalized = normalize_rta4_campaign_v5(_small_fixed_v5_raw())
    scientific = normalized["normalized_scientific_config"]
    plan = describe_formal_plan_v5(
        scientific, normalized["task_sources"], normalized["service_curve"],
    )

    assert "integer_time_scale_e0_semantics" not in scientific
    assert "integer_time_scale_e0_scaling_explicit" not in scientific[
        "fixed_semantics"
    ]
    assert rta4_formal_config_hash_v5(scientific) == (
        "6d01978b74b757fcfdc6ff1d6603506788569b360e5d76f39a109bea7fea7c99"
    )
    assert plan["plan_sha256"] == (
        "2503f951e25f67bbacab2bccd80672611153ada0461b54533b8da0b72521e3e2"
    )
    assert plan["ordered_stream_digest"] == (
        "fe6d52e5ac8b9820bc1867d57c92658ee75ee5bf372d59d2e8d4e9db40e51c53"
    )


def test_v5_legacy_missing_e0_semantics_keeps_a3_e0_fixed():
    normalized, records = _normalize_a3(scaled=False)
    scientific = normalized["normalized_scientific_config"]
    a3 = [
        record for record in records
        if record.material["v3_grid_material"]["axis"] == "integer_time_scale"
    ]

    assert "integer_time_scale_e0_semantics" not in scientific
    assert scientific["fixed_semantics"]["e0_auto_scaling_allowed"] is False
    assert {
        record.material["v3_grid_material"]["axis_value"]: record.material[
            "v3_grid_material"
        ]["exact_e0"]
        for record in a3
    } == {"1": "37", "2": "37", "4": "37", "8": "37"}


def test_v5_explicit_scaled_a3_e0_and_service_are_exact_and_isolated():
    normalized, records = _normalize_a3(scaled=True)
    scientific = normalized["normalized_scientific_config"]
    expected = {
        "1": ("37", "37", "11/2", "2/5"),
        "2": ("37", "74", "11/2", "4/5"),
        "4": ("37", "148", "11/2", "8/5"),
        "8": ("37", "296", "11/2", "16/5"),
    }

    assert scientific["integer_time_scale_e0_semantics"] == (
        CORE5A_SCALED_E0_V1
    )
    assert scientific["fixed_semantics"][
        "integer_time_scale_e0_scaling_explicit"
    ] is True
    for record in records:
        grid = record.material["v3_grid_material"]
        service = record.material["effective_service_curve"]
        if grid["axis"] == "integer_time_scale":
            assert (
                grid["e0"], grid["exact_e0"], service["rate"],
                service["latency"],
            ) == expected[grid["axis_value"]]
            assert record.material["integer_time_scale_e0_semantics"] == (
                CORE5A_SCALED_E0_V1
            )
        else:
            assert grid["axis"] in {"task_count", "processor_count"}
            assert grid["e0"] == "37"
            assert grid["exact_e0"] == "37"
            assert service["rate"] == "11/2"
            assert service["latency"] == "2/5"


def test_v5_scaled_a3_semantics_change_scientific_and_math_identities():
    legacy, legacy_records = _normalize_a3(scaled=False)
    scaled, scaled_records = _normalize_a3(scaled=True)
    legacy_a3 = {
        record.material["v3_grid_material"]["axis_value"]: record
        for record in legacy_records
        if record.material["v3_grid_material"]["axis"] == "integer_time_scale"
    }
    scaled_a3 = {
        record.material["v3_grid_material"]["axis_value"]: record
        for record in scaled_records
        if record.material["v3_grid_material"]["axis"] == "integer_time_scale"
    }

    assert rta4_formal_config_hash_v5(
        legacy["normalized_scientific_config"]
    ) != rta4_formal_config_hash_v5(scaled["normalized_scientific_config"])
    assert set(legacy_a3) == set(scaled_a3) == {"1", "2", "4", "8"}
    for scale in legacy_a3:
        assert (
            legacy_a3[scale].mathematical_request_id
            != scaled_a3[scale].mathematical_request_id
        )
        assert scaled_a3[scale].material["v3_grid_material"]["exact_e0"] == (
            str(37 * int(scale))
        )


def test_v5_explicit_fixed_e0_semantics_is_accepted_without_scaling():
    raw = _a3_v5_raw(scaled=False)
    raw["integer_time_scale_e0_semantics"] = CORE5A_FIXED_E0_V1
    normalized = normalize_rta4_campaign_v5(raw)
    scientific = normalized["normalized_scientific_config"]
    records = iter_formal_plan_v5(
        scientific, normalized["task_sources"], normalized["service_curve"],
    )

    assert scientific["integer_time_scale_e0_semantics"] == CORE5A_FIXED_E0_V1
    assert all(
        record.material["v3_grid_material"]["exact_e0"] == "37"
        for record in records
    )


@pytest.mark.parametrize("value", ["UNKNOWN", True, 1])
def test_v5_rejects_invalid_explicit_integer_time_e0_semantics(value):
    raw = _a3_v5_raw(scaled=False)
    raw["integer_time_scale_e0_semantics"] = value
    with pytest.raises(RTA4FormalConfigV5Error, match="integer-time E0 semantics"):
        normalize_rta4_campaign_v5(raw)


def test_v5_rejects_integer_time_e0_semantics_for_non_core5a():
    raw = campaign_template("CORE-1")
    raw.update({
        "processors": 2,
        "task_count": 2,
        "normalized_utilization": ["1/2"],
        "tasksets_per_utilization": 1,
        "e0": ["0"],
        "methods": [METHODS[0]],
        "task_source": _a3_task_source(
            processors=2, task_count=2, tasksets=1,
        ),
        "service_curve": {
            "model": EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
            "rate": "11/2",
            "latency": "2/5",
            "time_unit": "tick",
        },
        "integer_time_scale_e0_semantics": CORE5A_FIXED_E0_V1,
        "runtime": {},
    })
    with pytest.raises(RTA4FormalConfigV5Error, match="field set mismatch"):
        normalize_rta4_campaign_v5(raw)


def test_v5_scaled_a3_full_plan_preserves_frozen_request_counts():
    normalized = normalize_rta4_campaign_v5(
        _a3_v5_raw(scaled=True, full_grid=True)
    )
    plan = describe_formal_plan_v5(
        normalized["normalized_scientific_config"],
        normalized["task_sources"],
        normalized["service_curve"],
    )

    assert (
        plan["taskset_skeleton_count"],
        plan["mathematical_request_count"],
        plan["ordered_stream_count"],
    ) == (1100, 4400, 4400)
