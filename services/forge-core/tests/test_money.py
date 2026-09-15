"""Money must be exact. These tests exist because a float here is a lost client."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

import pytest

from forge.money import CurrencyMismatch, Money, msum


def test_float_is_rejected_at_construction():
    with pytest.raises(TypeError):
        Money.from_decimal(1234.56)
    with pytest.raises(TypeError):
        Money(1000, "CAD").scale(1.5)


def test_decimal_round_trip_is_exact():
    m = Money.from_decimal("1234.56", "CAD")
    assert m.minor_units == 123456
    assert m.to_decimal() == Decimal("1234.56")
    assert m.format() == "$1,234.56"


def test_the_classic_float_failure_does_not_happen():
    total = Money.from_decimal("0.10") + Money.from_decimal("0.20")
    assert total == Money.from_decimal("0.30")
    assert total.minor_units == 30


def test_currency_mixing_raises_rather_than_coercing():
    with pytest.raises(CurrencyMismatch):
        Money(100, "CAD") + Money(100, "USD")


def test_zero_decimal_currency_is_supported():
    yen = Money.from_decimal("1500", "JPY")
    assert yen.minor_units == 1500
    assert yen.format("¥") == "¥1,500"


@pytest.mark.parametrize(
    "total,weights",
    [
        ("100.00", [1, 1, 1]),
        ("0.05", [1, 1, 1, 1, 1, 1]),
        ("1234.57", [3, 5, 7, 11]),
        ("-99.99", [2, 3]),
    ],
)
def test_allocation_always_sums_back_to_the_whole(total, weights):
    amount = Money.from_decimal(total)
    parts = amount.allocate(weights)
    assert msum(parts) == amount, "largest-remainder allocation must not lose a cent"
    assert len(parts) == len(weights)


def test_allocation_is_stable_across_runs():
    amount = Money.from_decimal("10.00")
    assert amount.allocate([1, 1, 1]) == amount.allocate([1, 1, 1])


def test_allocation_rejects_degenerate_weights():
    with pytest.raises(ValueError):
        Money.from_decimal("10.00").allocate([])
    with pytest.raises(ValueError):
        Money.from_decimal("10.00").allocate([0, 0])
    with pytest.raises(ValueError):
        Money.from_decimal("10.00").allocate([-1, 2])


def test_scale_rounding_mode_is_explicit():
    half_cent = Money(5, "CAD")
    assert half_cent.scale(Decimal("0.5")).minor_units == 3  # half up
    assert half_cent.scale(Decimal("0.5"), rounding=ROUND_HALF_EVEN).minor_units == 2


def test_ratio_to_zero_is_none_not_an_exception():
    assert Money(100).ratio_to(Money(0)) is None


def test_sales_tax_split_is_exact():
    gross = Money.from_decimal("1130.00")
    net = gross.scale(Decimal(1) / Decimal("1.13"))
    tax = gross - net
    assert net + tax == gross
    assert net == Money.from_decimal("1000.00")
