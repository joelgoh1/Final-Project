"""Builds the 3-rule wallet cheat sheet for the upcoming cycle."""

from __future__ import annotations

from typing import Sequence

from .catalog import category_labels
from .models import CardProfile, Transaction, WalletRule
from .rules_engine import Allocation

MAX_RULES = 3


def _rate_label(card: CardProfile, category: str) -> str:
    rate = card.rate_for(category)
    return f"{rate * 100:.3g}% cashback"


def _condition(card: CardProfile) -> str:
    parts = []
    if card.min_spend > 0:
        parts.append(f"needs ${card.min_spend:,.0f}/mo total spend on this card")
    if card.monthly_cap is not None:
        parts.append(f"bonus capped at ${card.monthly_cap:,.0f}/mo")
    return "; ".join(parts) if parts else "no minimum spend, no cap"


def build_wallet_rules(
    transactions: Sequence[Transaction],
    optimal: Allocation,
    cards: dict[str, CardProfile],
) -> list[WalletRule]:
    """Top categories by reward in the optimal allocation, one card each.

    One rule per category, highest projected reward first, capped at three so
    the cheat sheet stays memorable.
    """
    labels = category_labels()
    pair_reward: dict[tuple[str, str], float] = {}
    for txn in transactions:
        card_id, reward = optimal.by_transaction[txn.id]
        key = (txn.category, card_id)
        pair_reward[key] = pair_reward.get(key, 0.0) + reward

    best_by_category: dict[str, tuple[str, float]] = {}
    for (category, card_id), reward in pair_reward.items():
        current = best_by_category.get(category)
        if current is None or reward > current[1]:
            best_by_category[category] = (card_id, reward)

    ranked = sorted(
        best_by_category.items(), key=lambda item: (-item[1][1], item[0])
    )[:MAX_RULES]

    rules: list[WalletRule] = []
    for category, (card_id, reward) in ranked:
        card = cards[card_id]
        rules.append(
            WalletRule(
                category_label=labels.get(category, category),
                card_name=card.name,
                rate_label=_rate_label(card, category),
                condition=_condition(card),
                projected_reward=round(reward, 2),
            )
        )
    return rules
