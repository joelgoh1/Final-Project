"""Core data shapes for the reward optimizer.

Everything here is a plain dataclass: the analysis pipeline is pure Python and
holds no database or ORM state. Nothing is persisted to disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass(frozen=True)
class CardProfile:
    """A card's reward rules, as modelled in data/cards.json."""

    id: str
    name: str
    issuer: str
    base_rate: float
    category_rates: dict[str, float]
    min_spend: float = 0.0
    monthly_cap: Optional[float] = None
    reward_unit: str = "cashback"
    assumptions: list[str] = field(default_factory=list)

    def rate_for(self, category: str) -> float:
        """Bonus rate for a category, or the card's base rate."""
        return self.category_rates.get(category, self.base_rate)

    def is_bonus_category(self, category: str) -> bool:
        return self.category_rates.get(category, self.base_rate) > self.base_rate


@dataclass
class Transaction:
    """One statement line, already redacted."""

    id: str
    date: str  # ISO-8601 date, e.g. "2026-08-14"
    merchant: str
    amount: float
    card_id: str  # the card the spend actually went on
    category: str = "general_spend"
    matched_rule: Optional[str] = None  # None => the General Spend guardrail fired
    source: str = "fixture"  # "fixture" or "pdf"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WalletRule:
    """One line of the 3-rule wallet cheat sheet for next cycle."""

    category_label: str
    card_name: str
    rate_label: str
    condition: str
    projected_reward: float


@dataclass
class ParseQuality:
    """Pipeline transparency: what got read, guardrailed, or skipped."""

    rows_parsed: int = 0
    lines_skipped: int = 0
    general_spend_rows: int = 0
    notes: list[str] = field(default_factory=list)
