"""CSV audit export: every categorized row plus its reward verdict."""

from __future__ import annotations

import csv
import io
from typing import Optional, Sequence

from . import catalog
from .models import Transaction

COLUMNS = [
    "date",
    "merchant",
    "amount_sgd",
    "category",
    "categorization",
    "card_used",
    "reward_earned",
    "card_under_optimal_plan",
    "reward_under_optimal_plan",
    "missed_value",
]


def transactions_csv(
    transactions: Sequence[Transaction],
    wallet_rules: Optional[Sequence[dict]] = None,
    advisor: Optional[dict] = None,
) -> str:
    """One row per transaction, then the wallet cheat sheet as a short appendix."""
    labels = catalog.category_labels()
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)

    for txn in transactions:
        audit = getattr(txn, "_audit", {})
        writer.writerow(
            [
                txn.date,
                txn.merchant,
                f"{txn.amount:.2f}",
                labels.get(txn.category, txn.category),
                "rule: " + txn.matched_rule if txn.matched_rule else "General Spend guardrail",
                audit.get("actual_card", ""),
                f"{audit.get('actual_reward', 0):.2f}",
                audit.get("plan_card", ""),
                f"{audit.get('plan_reward', 0):.2f}",
                f"{audit.get('missed', 0):.2f}",
            ]
        )

    if wallet_rules:
        writer.writerow([])
        writer.writerow(["Wallet strategy for next cycle"])
        writer.writerow(["category", "card", "rate", "condition", "projected_reward"])
        for rule in wallet_rules:
            writer.writerow(
                [
                    rule["category_label"],
                    rule["card_name"],
                    rule["rate_label"],
                    rule["condition"],
                    f"{rule['projected_reward']:.2f}",
                ]
            )

    if advisor and advisor.get("mode") == "agent":
        writer.writerow([])
        writer.writerow([f"AI strategist plan ({advisor.get('model', '')})"])
        writer.writerow(["category", "card", "rationale"])
        for rule in advisor.get("rules", []):
            writer.writerow([rule["category_label"], rule["card_name"], rule["rationale"]])
        writer.writerow(["everything else", advisor.get("default_card_name", ""), ""])
        writer.writerow(
            ["projected cycle rewards (engine-verified)", f"{advisor.get('verified_rewards', 0):.2f}"]
        )

    return buffer.getvalue()
