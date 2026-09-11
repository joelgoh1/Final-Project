"""The analysis pipeline: raw rows in, one dashboard payload out.

    raw rows -> redact -> categorize -> score actual -> allocate optimal
             -> leakage summary + wallet cheat sheet

Runs entirely in memory. The only thing kept afterwards is the session entry
in app.session_store (TTL'd, never written to disk).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from . import catalog
from .categorize import categorize_all
from .models import CardProfile, ParseQuality, Transaction, WalletRule
from .redaction import redact_merchant
from .rules_engine import Allocation, allocate_optimal, score_actual
from .wallet import build_wallet_rules

MAX_SUBOPTIMAL_ROWS = 15


@dataclass
class AnalysisResult:
    transactions: list[Transaction]
    payload: dict
    wallet: list[CardProfile] = field(default_factory=list)


def build_transactions(raw_rows: Sequence[dict], source: str) -> list[Transaction]:
    """Redact and normalise raw rows into Transactions with stable ids."""
    transactions = []
    for index, row in enumerate(raw_rows, start=1):
        amount = round(float(row["amount"]), 2)
        transactions.append(
            Transaction(
                id=f"t{index:04d}",
                date=str(row["date"]),
                merchant=redact_merchant(str(row["merchant"])),
                amount=amount,
                card_id=row["card_id"],
                source=source,
            )
        )
    return transactions


def _resolve_wallet(
    transactions: Sequence[Transaction], wallet_ids: Optional[Sequence[str]]
) -> list[CardProfile]:
    """Wallet = the requested cards, plus any card the statement actually used."""
    known = catalog.cards()
    ids: list[str] = []
    for card_id in list(wallet_ids or []) + [t.card_id for t in transactions]:
        if card_id in known and card_id not in ids:
            ids.append(card_id)
    if not ids:
        ids = list(known)
    # Keep cards.json declaration order so ties break deterministically.
    return [known[cid] for cid in known if cid in ids]


def _category_breakdown(transactions: Sequence[Transaction], total_spend: float) -> list[dict]:
    labels = catalog.category_labels()
    spend: dict[str, float] = {}
    counts: dict[str, int] = {}
    for txn in transactions:
        spend[txn.category] = spend.get(txn.category, 0.0) + txn.amount
        counts[txn.category] = counts.get(txn.category, 0) + 1
    rows = []
    for key in sorted(spend, key=lambda k: (-spend[k], k)):
        rows.append(
            {
                "key": key,
                "label": labels.get(key, key),
                "spend": round(spend[key], 2),
                "transaction_count": counts[key],
                "share_pct": round(spend[key] / total_spend * 100, 1) if total_spend else 0.0,
            }
        )
    return rows


def _card_breakdown(
    wallet: Sequence[CardProfile], actual: Allocation, optimal: Allocation
) -> list[dict]:
    rows = []
    for card in wallet:
        actual_ledger = actual.ledgers.get(card.id)
        optimal_ledger = optimal.ledgers.get(card.id)
        actual_spend = actual_ledger.spend if actual_ledger else 0.0
        rows.append(
            {
                "id": card.id,
                "name": card.name,
                "issuer": card.issuer,
                "actual_spend": round(actual_spend, 2),
                "actual_reward": round(actual_ledger.reward if actual_ledger else 0.0, 2),
                "optimal_spend": round(optimal_ledger.spend if optimal_ledger else 0.0, 2),
                "optimal_reward": round(optimal_ledger.reward if optimal_ledger else 0.0, 2),
                "min_spend": card.min_spend,
                "min_spend_met": card.min_spend == 0 or actual_spend + 1e-9 >= card.min_spend,
                "monthly_cap": card.monthly_cap,
                "cap_reached": card.id in actual.capped_cards,
            }
        )
    return rows


def _suboptimal_rows(
    transactions: Sequence[Transaction],
    actual: Allocation,
    optimal: Allocation,
    cards: dict[str, CardProfile],
) -> list[dict]:
    labels = catalog.category_labels()
    rows = []
    for txn in transactions:
        actual_card_id, actual_reward = actual.by_transaction[txn.id]
        best_card_id, best_reward = optimal.by_transaction[txn.id]
        missed = best_reward - actual_reward
        if best_card_id == actual_card_id or missed <= 0.005:
            continue
        rows.append(
            {
                "id": txn.id,
                "date": txn.date,
                "merchant": txn.merchant,
                "category_label": labels.get(txn.category, txn.category),
                "amount": round(txn.amount, 2),
                "actual_card": cards[actual_card_id].name,
                "actual_reward": round(actual_reward, 2),
                "better_card": cards[best_card_id].name,
                "better_reward": round(best_reward, 2),
                "missed": round(missed, 2),
            }
        )
    rows.sort(key=lambda r: (-r["missed"], r["date"]))
    return rows


def _wallet_assumptions(wallet: Sequence[CardProfile]) -> list[str]:
    notes = list(catalog.global_assumptions())
    notes.append(
        "The optimal figure is a cap-aware greedy re-allocation of the same spend "
        "across your wallet, not a proven mathematical optimum."
    )
    for card in wallet:
        for note in card.assumptions:
            notes.append(f"{card.name}: {note}")
    return notes


def analyze(
    raw_rows: Sequence[dict],
    wallet_ids: Optional[Sequence[str]] = None,
    source: str = "fixture",
    source_meta: Optional[dict] = None,
    parse_quality: Optional[ParseQuality] = None,
) -> AnalysisResult:
    """Run the full pipeline and assemble the dashboard payload."""
    transactions = build_transactions(raw_rows, source)
    quality = parse_quality or ParseQuality()
    quality.rows_parsed = len(transactions)
    quality.general_spend_rows = categorize_all(transactions)

    wallet = _resolve_wallet(transactions, wallet_ids)
    cards = catalog.cards()

    actual = score_actual(transactions, cards)
    optimal = allocate_optimal(transactions, wallet)
    already_optimal = optimal.total_reward <= actual.total_reward + 1e-9
    if already_optimal:
        # If the re-allocation cannot beat the statement, report the statement's
        # own allocation rather than a plan that is no better.
        optimal = actual

    total_spend = round(sum(t.amount for t in transactions), 2)
    actual_reward = round(actual.total_reward, 2)
    optimal_reward = round(optimal.total_reward, 2)
    missed = round(max(0.0, optimal_reward - actual_reward), 2)

    wallet_rules: list[WalletRule] = build_wallet_rules(transactions, optimal, cards)
    suboptimal = _suboptimal_rows(transactions, actual, optimal, cards)

    payload = {
        "source": source_meta or {"kind": source},
        "summary": {
            "total_spend": total_spend,
            "transaction_count": len(transactions),
            "actual_rewards": actual_reward,
            "optimal_rewards": optimal_reward,
            "missed_value": missed,
            "actual_yield_pct": round(actual_reward / total_spend * 100, 2) if total_spend else 0.0,
            "optimal_yield_pct": round(optimal_reward / total_spend * 100, 2) if total_spend else 0.0,
            "already_optimal": already_optimal,
        },
        "wallet": [
            {"id": c.id, "name": c.name, "issuer": c.issuer, "min_spend": c.min_spend,
             "monthly_cap": c.monthly_cap}
            for c in wallet
        ],
        "categories": _category_breakdown(transactions, total_spend),
        "cards": _card_breakdown(wallet, actual, optimal),
        "suboptimal": suboptimal[:MAX_SUBOPTIMAL_ROWS],
        "suboptimal_count": len(suboptimal),
        "wallet_rules": [
            {
                "category_label": r.category_label,
                "card_name": r.card_name,
                "rate_label": r.rate_label,
                "condition": r.condition,
                "projected_reward": r.projected_reward,
            }
            for r in wallet_rules
        ],
        "parse_quality": {
            "rows_parsed": quality.rows_parsed,
            "lines_skipped": quality.lines_skipped,
            "general_spend_rows": quality.general_spend_rows,
            "notes": quality.notes,
        },
        "assumptions": _wallet_assumptions(wallet),
    }

    # The CSV export needs the per-transaction verdict, so stash it on the rows.
    for txn in transactions:
        txn_actual_card, txn_actual_reward = actual.by_transaction[txn.id]
        txn_plan_card, txn_plan_reward = optimal.by_transaction[txn.id]
        setattr(txn, "_audit", {
            "actual_card": cards[txn_actual_card].name,
            "actual_reward": round(txn_actual_reward, 2),
            "plan_card": cards[txn_plan_card].name,
            "plan_reward": round(txn_plan_reward, 2),
            "missed": round(max(0.0, txn_plan_reward - txn_actual_reward), 2),
        })

    return AnalysisResult(transactions=transactions, payload=payload, wallet=wallet)
