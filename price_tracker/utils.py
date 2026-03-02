from __future__ import annotations

from typing import Optional, Tuple


def compute_normalized_unit_price(
    price_cents: int,
    size: float,
    unit: str,
) -> Optional[Tuple[float, str]]:
    """
    Returns (value, normalized_unit)
    e.g. (5.29, 'l') or (8.45, 'kg')
    """

    if price_cents is None or size <= 0:
        return None

    eur = price_cents / 100.0
    unit = unit.lower()

    if unit == "l":
        return eur / size, "l"

    if unit == "ml":
        return eur / (size / 1000.0), "l"

    if unit == "kg":
        return eur / size, "kg"

    if unit == "g":
        return eur / (size / 1000.0), "kg"

    if unit == "pcs":
        return eur / size, "pcs"

    return None