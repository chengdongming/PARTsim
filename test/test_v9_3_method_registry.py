from dataclasses import replace

import pytest

import asap_block_rta_v9_3_methods as methods
import asap_block_rta_v9_3_taskset as taskset
import asap_block_v9_3_runner as runner
from experiments.v9_3 import config


EXPECTED = (
    (
        "CW_THETA_CW",
        methods.V93Kernel.CW,
        methods.V93CarryPolicy.SELF_RECURSIVE,
        "CW",
        3,
    ),
    (
        "LOC_THETA_LOC",
        methods.V93Kernel.LOC,
        methods.V93CarryPolicy.SELF_RECURSIVE,
        "LOC",
        2,
    ),
    (
        "PH_THETA_PH",
        methods.V93Kernel.PH,
        methods.V93CarryPolicy.SELF_RECURSIVE,
        "PH",
        1,
    ),
    (
        "SEQ_THETA_SEQ",
        methods.V93Kernel.SEQ,
        methods.V93CarryPolicy.SELF_RECURSIVE,
        "SEQ",
        0,
    ),
    (
        "CW_D",
        methods.V93Kernel.CW,
        methods.V93CarryPolicy.FIXED_D,
        "CW-D",
        3,
    ),
    (
        "LOC_D",
        methods.V93Kernel.LOC,
        methods.V93CarryPolicy.FIXED_D,
        "LOC-D",
        2,
    ),
    (
        "PH_D",
        methods.V93Kernel.PH,
        methods.V93CarryPolicy.FIXED_D,
        "PH-D",
        1,
    ),
    (
        "SEQ_D",
        methods.V93Kernel.SEQ,
        methods.V93CarryPolicy.FIXED_D,
        "SEQ-D",
        0,
    ),
)


def test_registry_has_exact_canonical_order_and_metadata():
    observed = tuple(
        (
            spec.method_id.value,
            spec.kernel,
            spec.carry_policy,
            spec.display_name,
            spec.dominance_rank,
        )
        for spec in methods.V93_METHOD_SPECS
    )
    assert observed == EXPECTED
    assert tuple(methods.V93_METHOD_REGISTRY) == tuple(
        spec.method_id for spec in methods.V93_METHOD_SPECS
    )


def test_recursive_and_ablation_flags_are_derived_from_carry_policy():
    for spec in methods.V93_METHOD_SPECS:
        recursive = (
            spec.carry_policy is methods.V93CarryPolicy.SELF_RECURSIVE
        )
        assert spec.is_final_recursive_method is recursive
        assert spec.is_ablation_method is not recursive


@pytest.mark.parametrize(
    "policy",
    (
        methods.V93CarryPolicy.FIXED_D,
        methods.V93CarryPolicy.SELF_RECURSIVE,
    ),
)
def test_each_policy_has_complete_strict_dominance_rank_chain(policy):
    by_kernel = {
        spec.kernel: spec.dominance_rank
        for spec in methods.V93_METHOD_SPECS
        if spec.carry_policy is policy
    }
    assert by_kernel == {
        methods.V93Kernel.CW: 3,
        methods.V93Kernel.LOC: 2,
        methods.V93Kernel.PH: 1,
        methods.V93Kernel.SEQ: 0,
    }


def test_lookup_accepts_only_exact_ids_and_canonical_specs():
    expected = methods.V93_METHOD_SPECS[0]
    assert methods.method_spec_v9_3(expected) is expected
    assert methods.method_spec_v9_3(expected.method_id) is expected
    assert methods.method_spec_v9_3(expected.method_id.value) is expected
    for alias in ("cw", "CW", "CW-Theta^cw", "PH-D"):
        with pytest.raises(
            methods.V93MethodRegistryError, match="unknown canonical"
        ):
            methods.method_spec_v9_3(alias)
    with pytest.raises(
        methods.V93MethodRegistryError, match="not the canonical"
    ):
        methods.method_spec_v9_3(replace(expected, dominance_rank=99))


def test_registry_mapping_is_read_only():
    with pytest.raises(TypeError):
        methods.V93_METHOD_REGISTRY[methods.V93MethodId.CW_D] = (
            methods.V93_METHOD_SPECS[0]
        )


def test_formal_runner_order_is_exactly_unchanged():
    assert runner.VARIANT_ORDER == (
        taskset.AnalysisVariant.CW_D,
        taskset.AnalysisVariant.LOC_D,
        taskset.AnalysisVariant.CW_THETA_CW,
        taskset.AnalysisVariant.LOC_THETA_CW,
        taskset.AnalysisVariant.LOC_THETA_LOC,
    )
    assert taskset.AnalysisVariant.PH_THETA_PH not in runner.VARIANT_ORDER
    assert taskset.AnalysisVariant.SEQ_THETA_SEQ not in runner.VARIANT_ORDER
    assert not any(
        variant.name in {"PH_D", "SEQ_D"}
        for variant in runner.VARIANT_ORDER
    )


def test_existing_experiment_allowlist_is_not_expanded():
    assert config.KNOWN_VARIANTS == {
        "CW_D",
        "LOC_D",
        "CW_THETA_CW",
        "LOC_THETA_CW",
        "LOC_THETA_LOC",
    }
    assert set(spec.method_id.value for spec in methods.V93_METHOD_SPECS) - (
        config.KNOWN_VARIANTS
    ) == {"PH_THETA_PH", "SEQ_THETA_SEQ", "PH_D", "SEQ_D"}
