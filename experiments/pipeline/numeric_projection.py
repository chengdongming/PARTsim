"""Deterministic decimal rendering of exact energy values."""

from decimal import Decimal, localcontext
from fractions import Fraction

class RTA4Core3ContractV7Error(ValueError):
    """Raised before an ambiguous CORE-3 V7 projection can be used."""


def canonical_binary64_decimal_v7(value: Fraction) -> str:
    """Use the repository's established 17-digit Fraction renderer."""

    if type(value) is not Fraction:
        raise RTA4Core3ContractV7Error(
            "binary64 projection source must be an exact Fraction"
        )
    if value == 0:
        return "0"
    with localcontext() as context:
        context.prec = 17
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered
