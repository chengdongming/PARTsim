"""Explicit, identity-bound energy-service inputs for RTA4 formal V4."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Mapping

from .rta4_formal_config import domain_hash, fraction_text


EXACT_LINEAR_SERVICE_V1 = "EXACT_LINEAR_SERVICE_V1"
VERIFIED_SHARED_ENERGY_MATERIAL_V1 = "VERIFIED_SHARED_ENERGY_MATERIAL_V1"
ENERGY_SERVICE_SCHEMA_V4 = "ASAP_BLOCK_V9_3_RTA4_ENERGY_SERVICE_V4"
ENERGY_SERVICE_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4:ENERGY_SERVICE:v4"
EXACT_SERVICE_MATERIAL_DOMAIN_V4 = (
    "ASAP_BLOCK:V9.3:RTA4:EXACT_LINEAR_SERVICE_MATERIAL:v4"
)


class RTA4EnergyServiceV4Error(ValueError):
    """Raised when a V4 service model is missing, implicit, or inexact."""


def _sha(value: Any, label: str) -> str:
    if (
        type(value) is not str or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RTA4EnergyServiceV4Error(f"{label} must be a lowercase SHA-256")
    return value


def _field_set(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        actual = set(value) if isinstance(value, Mapping) else set()
        raise RTA4EnergyServiceV4Error(
            f"{label} field set mismatch; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )
    return value


@dataclass(frozen=True)
class EnergyServiceV4:
    model: str
    normalized_config: Mapping[str, Any]
    identity: str

    def beta(self, length: int) -> Fraction:
        if self.model != EXACT_LINEAR_SERVICE_V1:
            raise RTA4EnergyServiceV4Error(
                "external shared service needs its verified runtime material"
            )
        if type(length) is not int or length < 0:
            raise RTA4EnergyServiceV4Error(
                "service length must be a nonnegative plain integer"
            )
        rate = Fraction(self.normalized_config["rate"])
        if rate == Fraction(1, 10):
            # This is the paper-mainline contract.  Keep the construction
            # visibly independent of any materialized/binary64 prefix.
            return Fraction(length, 10)
        return Fraction(length * rate.numerator, rate.denominator)


@dataclass(frozen=True)
class ExactServiceMaterialV4:
    service_model: str
    rate: str
    maximum_length: int
    beta_prefix: tuple[Fraction, ...]
    configured_service_identity: str
    material_identity: str


def normalize_energy_service_v4(raw: Any) -> EnergyServiceV4:
    if not isinstance(raw, Mapping):
        raise RTA4EnergyServiceV4Error("energy_service must be a mapping")
    model = raw.get("model")
    if model == EXACT_LINEAR_SERVICE_V1:
        row = _field_set(raw, {"model", "rate"}, "energy_service")
        if type(row["rate"]) is not str or not row["rate"]:
            raise RTA4EnergyServiceV4Error(
                "exact linear rate must be an exact rational string"
            )
        try:
            rate = Fraction(row["rate"])
        except (ValueError, ZeroDivisionError) as exc:
            raise RTA4EnergyServiceV4Error(
                "exact linear rate is not rational"
            ) from exc
        if rate <= 0:
            raise RTA4EnergyServiceV4Error(
                "exact linear rate must be positive"
            )
        canonical = fraction_text(rate)
        if row["rate"] != canonical:
            raise RTA4EnergyServiceV4Error(
                f"exact linear rate must be canonical: {canonical}"
            )
        normalized = {
            "schema": ENERGY_SERVICE_SCHEMA_V4,
            "model": EXACT_LINEAR_SERVICE_V1,
            "version": "1",
            "rate": canonical,
            "beta_contract": "Fraction(rate) * plain_integer_length",
            "float_conversion_allowed": False,
        }
    elif model == VERIFIED_SHARED_ENERGY_MATERIAL_V1:
        row = _field_set(raw, {
            "model", "material_schema", "service_material_identity",
            "beta_material_identity", "production_build_manifest_identity",
            "source_closure_identity",
        }, "energy_service")
        if type(row["material_schema"]) is not str or not row["material_schema"]:
            raise RTA4EnergyServiceV4Error("shared material schema is required")
        normalized = {
            "schema": ENERGY_SERVICE_SCHEMA_V4,
            "model": VERIFIED_SHARED_ENERGY_MATERIAL_V1,
            "version": "1",
            "material_schema": row["material_schema"],
            "service_material_identity": _sha(
                row["service_material_identity"], "service material identity",
            ),
            "beta_material_identity": _sha(
                row["beta_material_identity"], "beta material identity",
            ),
            "production_build_manifest_identity": _sha(
                row["production_build_manifest_identity"],
                "production build manifest identity",
            ),
            "source_closure_identity": _sha(
                row["source_closure_identity"], "source closure identity",
            ),
            "implicit_solar_fallback_allowed": False,
        }
    else:
        raise RTA4EnergyServiceV4Error(
            "missing or unknown energy_service.model"
        )
    return EnergyServiceV4(
        str(model), normalized,
        domain_hash(ENERGY_SERVICE_DOMAIN_V4, normalized),
    )


def exact_service_material_v4(
    service: EnergyServiceV4, maximum_length: int,
) -> ExactServiceMaterialV4:
    if type(service) is not EnergyServiceV4 or service.model != EXACT_LINEAR_SERVICE_V1:
        raise RTA4EnergyServiceV4Error(
            "exact service material requires EXACT_LINEAR_SERVICE_V1"
        )
    if type(maximum_length) is not int or maximum_length < 0:
        raise RTA4EnergyServiceV4Error(
            "maximum service length must be a nonnegative plain integer"
        )
    rate = Fraction(service.normalized_config["rate"])
    prefix = tuple(service.beta(length) for length in range(maximum_length + 1))
    material = {
        "service_model": EXACT_LINEAR_SERVICE_V1,
        "rate": fraction_text(rate),
        "maximum_length": maximum_length,
        "beta_prefix": [fraction_text(value) for value in prefix],
        "configured_service_identity": service.identity,
    }
    return ExactServiceMaterialV4(
        EXACT_LINEAR_SERVICE_V1, fraction_text(rate), maximum_length, prefix,
        service.identity,
        domain_hash(EXACT_SERVICE_MATERIAL_DOMAIN_V4, material),
    )


def validate_bound_shared_material_v4(
    service: EnergyServiceV4, *, service_material_identity: str,
    beta_material_identity: str, production_build_manifest_identity: str,
) -> None:
    if type(service) is not EnergyServiceV4 or service.model != VERIFIED_SHARED_ENERGY_MATERIAL_V1:
        raise RTA4EnergyServiceV4Error(
            "shared material binding requires its explicit service model"
        )
    expected = service.normalized_config
    observed = {
        "service_material_identity": _sha(
            service_material_identity, "observed service identity",
        ),
        "beta_material_identity": _sha(
            beta_material_identity, "observed beta identity",
        ),
        "production_build_manifest_identity": _sha(
            production_build_manifest_identity,
            "observed build manifest identity",
        ),
    }
    if any(expected[key] != value for key, value in observed.items()):
        raise RTA4EnergyServiceV4Error("shared service runtime material drift")


__all__ = [
    "ENERGY_SERVICE_SCHEMA_V4", "EXACT_LINEAR_SERVICE_V1",
    "EXACT_SERVICE_MATERIAL_DOMAIN_V4", "EnergyServiceV4",
    "ExactServiceMaterialV4", "RTA4EnergyServiceV4Error",
    "VERIFIED_SHARED_ENERGY_MATERIAL_V1", "exact_service_material_v4",
    "normalize_energy_service_v4", "validate_bound_shared_material_v4",
]
