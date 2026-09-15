"""Parsers for the human-written ranges in the industry packs.

The source data was written for people to read: gross margin appears as
``"35-45%"`` and revenue as ``"$100K-$500K"``. Controls need numbers, and the
conversion has to be exact and total, because a benchmark that silently parses
to ``None`` produces a control that silently never fires.

Two details cost real accuracy if missed:

* The separator is an en dash, not a hyphen, and a negative lower bound is
  written with an ASCII hyphen. ``"-10-15%"`` means minus ten to plus fifteen,
  not minus ten to minus fifteen.
* Open-ended ranges appear as ``"<$100K"`` and ``"$10M+"``. Both are real tiers
  and neither can be dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from ..money import Money

__all__ = ["Range", "MoneyRange", "parse_percent_range", "parse_money_range"]

# U+2013 en dash, U+2014 em dash, and the ASCII hyphen, in that preference order.
_DASHES = ("–", "—", "-")

_NUMBER = r"-?\d+(?:\.\d+)?"
_MULTIPLIERS = {"K": Decimal(1_000), "M": Decimal(1_000_000), "B": Decimal(1_000_000_000)}


@dataclass(frozen=True)
class Range:
    """An inclusive numeric range. ``None`` on either side means open-ended."""

    low: Decimal | None
    high: Decimal | None
    source: str = ""

    def contains(self, value: Decimal) -> bool:
        if self.low is not None and value < self.low:
            return False
        if self.high is not None and value > self.high:
            return False
        return True

    def position(self, value: Decimal) -> str:
        """Where a value sits: ``below``, ``within`` or ``above``."""
        if self.low is not None and value < self.low:
            return "below"
        if self.high is not None and value > self.high:
            return "above"
        return "within"

    def shortfall(self, value: Decimal) -> Decimal:
        """How far below the low bound, or zero when at or above it."""
        if self.low is None or value >= self.low:
            return Decimal(0)
        return self.low - value

    def excess(self, value: Decimal) -> Decimal:
        """How far above the high bound, or zero when at or below it."""
        if self.high is None or value <= self.high:
            return Decimal(0)
        return value - self.high

    @property
    def midpoint(self) -> Decimal | None:
        if self.low is None or self.high is None:
            return None
        return (self.low + self.high) / Decimal(2)

    def format_percent(self) -> str:
        def one(v: Decimal | None) -> str:
            return "—" if v is None else f"{v * 100:.0f}%"

        if self.low is None:
            return f"up to {one(self.high)}"
        if self.high is None:
            return f"{one(self.low)} and above"
        return f"{one(self.low)} to {one(self.high)}"


@dataclass(frozen=True)
class MoneyRange:
    """A money range with the same open-ended semantics as :class:`Range`."""

    low: Money | None
    high: Money | None
    source: str = ""

    def contains(self, value: Money) -> bool:
        if self.low is not None and value < self.low:
            return False
        if self.high is not None and value > self.high:
            return False
        return True

    def position(self, value: Money) -> str:
        if self.low is not None and value < self.low:
            return "below"
        if self.high is not None and value > self.high:
            return "above"
        return "within"

    def describe(self) -> str:
        if self.low is None and self.high is not None:
            return f"under {self.high.format()}"
        if self.high is None and self.low is not None:
            return f"{self.low.format()} and above"
        if self.low is not None and self.high is not None:
            return f"{self.low.format()} to {self.high.format()}"
        return "any size"


def _split_on_dash(text: str) -> tuple[str, str] | None:
    """Split a range on its separator, respecting a leading negative sign."""
    for dash in _DASHES:
        # Search from index 1 so an ASCII hyphen at the start reads as a sign.
        idx = text.find(dash, 1)
        while idx != -1:
            left, right = text[:idx], text[idx + len(dash) :]
            if left.strip() and right.strip():
                return left.strip(), right.strip()
            idx = text.find(dash, idx + 1)
    return None


def parse_percent_range(text: str | None) -> Range | None:
    """Parse ``"35-45%"`` into a fractional range, or ``None`` if unparseable.

    Values are returned as fractions, so 35% becomes ``Decimal("0.35")``, which
    is what every ratio in the engine already uses.
    """
    if not text:
        return None
    cleaned = text.replace("%", "").replace(" ", "")
    if not cleaned:
        return None

    parts = _split_on_dash(cleaned)
    if parts is None:
        m = re.fullmatch(_NUMBER, cleaned)
        if not m:
            return None
        value = Decimal(cleaned) / Decimal(100)
        return Range(low=value, high=value, source=text)

    left, right = parts
    if not re.fullmatch(_NUMBER, left) or not re.fullmatch(_NUMBER, right):
        return None
    low = Decimal(left) / Decimal(100)
    high = Decimal(right) / Decimal(100)
    if low > high:
        low, high = high, low
    return Range(low=low, high=high, source=text)


def _parse_money_token(token: str, currency: str) -> Money | None:
    token = token.replace("$", "").replace(",", "").replace(" ", "").upper()
    if not token:
        return None
    multiplier = Decimal(1)
    if token[-1] in _MULTIPLIERS:
        multiplier = _MULTIPLIERS[token[-1]]
        token = token[:-1]
    if not re.fullmatch(_NUMBER, token):
        return None
    return Money.from_decimal(Decimal(token) * multiplier, currency)


def parse_money_range(text: str | None, currency: str = "CAD") -> MoneyRange | None:
    """Parse ``"$100K-$500K"``, ``"<$100K"`` or ``"$10M+"``."""
    if not text:
        return None
    cleaned = text.strip()

    if cleaned.startswith("<"):
        high = _parse_money_token(cleaned[1:], currency)
        return MoneyRange(low=None, high=high, source=text) if high else None
    if cleaned.endswith("+"):
        low = _parse_money_token(cleaned[:-1], currency)
        return MoneyRange(low=low, high=None, source=text) if low else None

    parts = _split_on_dash(cleaned)
    if parts is None:
        single = _parse_money_token(cleaned, currency)
        return MoneyRange(low=single, high=single, source=text) if single else None

    low = _parse_money_token(parts[0], currency)
    high = _parse_money_token(parts[1], currency)
    if low is None or high is None:
        return None
    if low > high:
        low, high = high, low
    return MoneyRange(low=low, high=high, source=text)
