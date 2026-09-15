"""Exact money arithmetic for ForgeOS.

Constitution rule #2 (deterministic financial math): models never become the
calculator of record. Every currency amount in ForgeOS is stored and computed as
an integer number of *minor units* (cents for CAD/USD), never as a float.

Design notes
------------
* ``Money`` is an immutable value object of ``(minor_units, currency)``.
* Addition/subtraction require identical currency. Mixing currencies raises
  ``CurrencyMismatch`` rather than silently coercing.
* Multiplication by a rate uses :class:`decimal.Decimal` and an *explicit*
  rounding mode. The default is ``ROUND_HALF_UP`` because that is the
  convention used by CRA/IRS guidance and by QuickBooks Online for tax and
  allocation math. ``ROUND_HALF_EVEN`` is available where a statistical
  allocation is wanted; the caller must ask for it.
* Division for allocation uses :meth:`Money.allocate`, which distributes the
  remainder deterministically (largest-remainder method) so that the parts
  always sum exactly back to the whole. This is the single most common source
  of one-cent close differences in hand-rolled systems.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, localcontext

__all__ = ["Money", "CurrencyMismatch", "MINOR_UNITS", "ROUND_HALF_UP", "ROUND_HALF_EVEN"]


class CurrencyMismatch(ValueError):
    """Raised when arithmetic is attempted across two different currencies."""


# Currencies whose minor unit is not 1/100. Extend deliberately, with a test.
MINOR_UNITS: dict[str, int] = {
    "CAD": 2,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "JPY": 0,
    "KWD": 3,
}


def _exponent(currency: str) -> int:
    try:
        return MINOR_UNITS[currency]
    except KeyError as exc:  # pragma: no cover - guarded by validation
        raise ValueError(f"Unknown currency {currency!r}; add it to MINOR_UNITS with a test") from exc


@dataclass(frozen=True, order=False)
class Money:
    """An exact monetary amount.

    ``minor_units`` is a signed integer. ``Money(-1250, "CAD")`` is -$12.50.
    """

    minor_units: int
    currency: str = "CAD"

    def __post_init__(self) -> None:
        if not isinstance(self.minor_units, int) or isinstance(self.minor_units, bool):
            raise TypeError(f"minor_units must be int, got {type(self.minor_units).__name__}")
        cur = self.currency.upper()
        _exponent(cur)
        object.__setattr__(self, "currency", cur)

    # ---- constructors -------------------------------------------------

    @classmethod
    def zero(cls, currency: str = "CAD") -> Money:
        return cls(0, currency)

    @classmethod
    def from_decimal(
        cls, value: Decimal | str | int, currency: str = "CAD", rounding: str = ROUND_HALF_UP
    ) -> Money:
        """Build from a major-unit decimal, e.g. ``"1234.56"`` -> 123456 cents.

        Floats are rejected on purpose: ``0.1 + 0.2`` is not 0.3 and a finance
        system must never let that enter the ledger. Pass a str or Decimal.
        """
        if isinstance(value, float):
            raise TypeError("Refusing to build Money from float; pass str or Decimal")
        cur = currency.upper()
        exp = _exponent(cur)
        with localcontext() as ctx:
            ctx.prec = 34
            dec = Decimal(value) if not isinstance(value, Decimal) else value
            scaled = (dec * (Decimal(10) ** exp)).quantize(Decimal(1), rounding=rounding)
        return cls(int(scaled), cur)

    # ---- conversion ---------------------------------------------------

    def to_decimal(self) -> Decimal:
        """Major units as an exact Decimal, e.g. ``Decimal('1234.56')``."""
        exp = _exponent(self.currency)
        return (Decimal(self.minor_units) / (Decimal(10) ** exp)).quantize(
            Decimal(1).scaleb(-exp)
        )

    def __str__(self) -> str:
        return f"{self.to_decimal()} {self.currency}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Money({self.minor_units!r}, {self.currency!r})"

    def format(self, symbol: str = "$") -> str:
        """Human display with thousands separators and a leading minus."""
        exp = _exponent(self.currency)
        sign = "-" if self.minor_units < 0 else ""
        whole, frac = divmod(abs(self.minor_units), 10**exp) if exp else (abs(self.minor_units), 0)
        body = f"{whole:,}" + (f".{frac:0{exp}d}" if exp else "")
        return f"{sign}{symbol}{body}"

    # ---- arithmetic ---------------------------------------------------

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.minor_units + other.minor_units, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.minor_units - other.minor_units, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.minor_units, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.minor_units), self.currency)

    def __mul__(self, factor: int | Decimal | str) -> Money:
        return self.scale(factor)

    __rmul__ = __mul__

    def scale(self, factor: int | Decimal | str, rounding: str = ROUND_HALF_UP) -> Money:
        """Multiply by a rate with explicit rounding (tax, FX, allocation %)."""
        if isinstance(factor, float):
            raise TypeError("Refusing to scale Money by float; pass int, str or Decimal")
        with localcontext() as ctx:
            ctx.prec = 34
            dec = Decimal(factor) if not isinstance(factor, Decimal) else factor
            result = (Decimal(self.minor_units) * dec).quantize(Decimal(1), rounding=rounding)
        return Money(int(result), self.currency)

    def ratio_to(self, other: Money) -> Decimal | None:
        """Exact ratio as a Decimal, or ``None`` when the denominator is zero."""
        self._check(other)
        if other.minor_units == 0:
            return None
        with localcontext() as ctx:
            ctx.prec = 28
            return Decimal(self.minor_units) / Decimal(other.minor_units)

    def allocate(self, weights: Sequence[int | Decimal | str]) -> list[Money]:
        """Split across ``weights`` so the parts sum exactly back to ``self``.

        Uses the largest-remainder method; ties break toward the earlier index,
        which makes the result stable and reproducible across runs.
        """
        if not weights:
            raise ValueError("allocate requires at least one weight")
        decs = [Decimal(w) if not isinstance(w, Decimal) else w for w in weights]
        if any(d < 0 for d in decs):
            raise ValueError("allocate weights must be non-negative")
        total_w = sum(decs)
        if total_w == 0:
            raise ValueError("allocate weights must not sum to zero")
        with localcontext() as ctx:
            ctx.prec = 34
            exact = [Decimal(self.minor_units) * d / total_w for d in decs]
        floors = [int(e.to_integral_value(rounding="ROUND_FLOOR")) for e in exact]
        remainder = self.minor_units - sum(floors)
        # Distribute the remaining units to the largest fractional parts.
        order = sorted(
            range(len(decs)), key=lambda i: (exact[i] - floors[i], -i), reverse=True
        )
        step = 1 if remainder >= 0 else -1
        for k in range(abs(remainder)):
            floors[order[k % len(order)]] += step
        return [Money(f, self.currency) for f in floors]

    # ---- comparison ---------------------------------------------------

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.minor_units < other.minor_units

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.minor_units <= other.minor_units

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.minor_units > other.minor_units

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.minor_units >= other.minor_units

    def __bool__(self) -> bool:
        return self.minor_units != 0

    @property
    def is_zero(self) -> bool:
        return self.minor_units == 0


def msum(items: Iterable[Money], currency: str = "CAD") -> Money:
    """Sum an iterable of Money, returning zero in ``currency`` when empty."""
    total: Money | None = None
    for m in items:
        total = m if total is None else total + m
    return total if total is not None else Money.zero(currency)
