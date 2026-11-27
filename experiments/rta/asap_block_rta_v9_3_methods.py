"""Canonical method catalog for the v9.3 ASAP-BLOCK mathematical kernels.

The catalog is deliberately independent of experiment configuration, formal
authorization, result schemas, and runners.  Registering a method here makes
it available to the internal task-set adapter only; it does not authorize the
method for any existing formal experiment.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Tuple, Union


class V93MethodRegistryError(ValueError):
    """Raised when a caller supplies a non-canonical method specification."""


class V93MethodId(str, Enum):
    CW_THETA_CW = "CW_THETA_CW"
    LOC_THETA_LOC = "LOC_THETA_LOC"
    PH_THETA_PH = "PH_THETA_PH"
    SEQ_THETA_SEQ = "SEQ_THETA_SEQ"
    CW_D = "CW_D"
    LOC_D = "LOC_D"
    PH_D = "PH_D"
    SEQ_D = "SEQ_D"


class V93Kernel(str, Enum):
    CW = "CW"
    LOC = "LOC"
    PH = "PH"
    SEQ = "SEQ"


class V93CarryPolicy(str, Enum):
    FIXED_D = "FIXED_D"
    SELF_RECURSIVE = "SELF_RECURSIVE"


@dataclass(frozen=True)
class V93MethodSpec:
    method_id: V93MethodId
    display_name: str
    kernel: V93Kernel
    carry_policy: V93CarryPolicy
    dominance_rank: int
    is_final_recursive_method: bool
    is_ablation_method: bool

    def __post_init__(self) -> None:
        if not isinstance(self.method_id, V93MethodId):
            raise V93MethodRegistryError("method_id must be a V93MethodId")
        if not isinstance(self.kernel, V93Kernel):
            raise V93MethodRegistryError("kernel must be a V93Kernel")
        if not isinstance(self.carry_policy, V93CarryPolicy):
            raise V93MethodRegistryError(
                "carry_policy must be a V93CarryPolicy"
            )
        if not isinstance(self.display_name, str) or not self.display_name:
            raise V93MethodRegistryError("display_name must be non-empty")
        if type(self.dominance_rank) is not int or self.dominance_rank < 0:
            raise V93MethodRegistryError(
                "dominance_rank must be a nonnegative plain integer"
            )
        if type(self.is_final_recursive_method) is not bool:
            raise V93MethodRegistryError(
                "is_final_recursive_method must be bool"
            )
        if type(self.is_ablation_method) is not bool:
            raise V93MethodRegistryError("is_ablation_method must be bool")
        recursive = self.carry_policy is V93CarryPolicy.SELF_RECURSIVE
        if self.is_final_recursive_method is not recursive:
            raise V93MethodRegistryError(
                "recursive-method flag must match carry policy"
            )
        if self.is_ablation_method is recursive:
            raise V93MethodRegistryError(
                "only fixed-D methods are ablation methods"
            )


V93_METHOD_SPECS: Tuple[V93MethodSpec, ...] = (
    V93MethodSpec(
        V93MethodId.CW_THETA_CW,
        "CW",
        V93Kernel.CW,
        V93CarryPolicy.SELF_RECURSIVE,
        3,
        True,
        False,
    ),
    V93MethodSpec(
        V93MethodId.LOC_THETA_LOC,
        "LOC",
        V93Kernel.LOC,
        V93CarryPolicy.SELF_RECURSIVE,
        2,
        True,
        False,
    ),
    V93MethodSpec(
        V93MethodId.PH_THETA_PH,
        "PH",
        V93Kernel.PH,
        V93CarryPolicy.SELF_RECURSIVE,
        1,
        True,
        False,
    ),
    V93MethodSpec(
        V93MethodId.SEQ_THETA_SEQ,
        "SEQ",
        V93Kernel.SEQ,
        V93CarryPolicy.SELF_RECURSIVE,
        0,
        True,
        False,
    ),
    V93MethodSpec(
        V93MethodId.CW_D,
        "CW-D",
        V93Kernel.CW,
        V93CarryPolicy.FIXED_D,
        3,
        False,
        True,
    ),
    V93MethodSpec(
        V93MethodId.LOC_D,
        "LOC-D",
        V93Kernel.LOC,
        V93CarryPolicy.FIXED_D,
        2,
        False,
        True,
    ),
    V93MethodSpec(
        V93MethodId.PH_D,
        "PH-D",
        V93Kernel.PH,
        V93CarryPolicy.FIXED_D,
        1,
        False,
        True,
    ),
    V93MethodSpec(
        V93MethodId.SEQ_D,
        "SEQ-D",
        V93Kernel.SEQ,
        V93CarryPolicy.FIXED_D,
        0,
        False,
        True,
    ),
)

_METHOD_REGISTRY = {spec.method_id: spec for spec in V93_METHOD_SPECS}
if len(_METHOD_REGISTRY) != len(V93_METHOD_SPECS):
    raise V93MethodRegistryError("duplicate canonical method ID")

V93_METHOD_REGISTRY: Mapping[V93MethodId, V93MethodSpec] = MappingProxyType(
    _METHOD_REGISTRY
)

MethodReference = Union[V93MethodId, V93MethodSpec, str]


def method_spec_v9_3(method: MethodReference) -> V93MethodSpec:
    """Return one canonical specification without accepting aliases."""

    if isinstance(method, V93MethodSpec):
        canonical = V93_METHOD_REGISTRY.get(method.method_id)
        if canonical != method:
            raise V93MethodRegistryError(
                "method specification is not the canonical registry entry"
            )
        return canonical
    if isinstance(method, V93MethodId):
        return V93_METHOD_REGISTRY[method]
    if isinstance(method, str):
        try:
            method_id = V93MethodId(method)
        except ValueError as exc:
            raise V93MethodRegistryError(
                "unknown canonical v9.3 method ID: {!r}".format(method)
            ) from exc
        return V93_METHOD_REGISTRY[method_id]
    raise V93MethodRegistryError(
        "method must be a canonical ID or V93MethodSpec"
    )


__all__ = [
    "MethodReference",
    "V93CarryPolicy",
    "V93Kernel",
    "V93MethodId",
    "V93MethodRegistryError",
    "V93MethodSpec",
    "V93_METHOD_REGISTRY",
    "V93_METHOD_SPECS",
    "method_spec_v9_3",
]
