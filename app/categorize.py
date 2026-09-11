"""Deterministic merchant categorization with a General Spend guardrail.

Rule-based only: an ordered keyword map from data/merchant_rules.json, matched
on word boundaries so "pub" does not match "public". Any merchant that matches
nothing lands in "general_spend", so an unfamiliar merchant can never fail the
pipeline.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable, Optional

from .catalog import merchant_rules
from .models import Transaction

FALLBACK_CATEGORY = "general_spend"

_NOISE = re.compile(r"[^a-z0-9&.' ]+")


def normalize_merchant(merchant: str) -> str:
    """Lowercase and strip statement noise so keywords match reliably."""
    text = _NOISE.sub(" ", merchant.lower())
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=1)
def _compiled_rules() -> list[tuple[str, str, re.Pattern]]:
    """(rule_id, category, pattern) in declaration order."""
    compiled = []
    for rule in merchant_rules()["rules"]:
        keywords = [normalize_merchant(k) for k in rule["keywords"]]
        keywords = [k for k in keywords if k]
        if not keywords:
            continue
        alternation = "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True))
        pattern = re.compile(rf"(?<![a-z0-9])(?:{alternation})(?![a-z0-9])")
        compiled.append((rule["id"], rule["category"], pattern))
    return compiled


def categorize(merchant: str) -> tuple[str, Optional[str]]:
    """Return (category, matched_rule_id). matched_rule_id is None for the guardrail."""
    haystack = normalize_merchant(merchant)
    for rule_id, category, pattern in _compiled_rules():
        if pattern.search(haystack):
            return category, rule_id
    return FALLBACK_CATEGORY, None


def categorize_all(transactions: Iterable[Transaction]) -> int:
    """Categorize in place; returns how many rows used the General Spend guardrail."""
    guardrailed = 0
    for txn in transactions:
        txn.category, txn.matched_rule = categorize(txn.merchant)
        if txn.matched_rule is None:
            guardrailed += 1
    return guardrailed
