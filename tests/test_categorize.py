"""Deterministic categorization and the General Spend guardrail."""

import pytest

from app.analysis import analyze
from app.categorize import FALLBACK_CATEGORY, categorize, normalize_merchant


@pytest.mark.parametrize(
    "merchant, category",
    [
        ("NTUC FAIRPRICE FINEST #02-1", "groceries"),
        ("GRABFOOD SINGAPORE", "dining"),
        ("SHOPEE SG PTE LTD", "shopping"),
        ("SP SERVICES LTD", "utilities"),
        ("SIMPLYGO*BUS/MRT", "transport"),
        ("Shell Alexandra", "transport"),
        ("EZ-LINK TOP UP", "transport"),
        ("M1 LIMITED", "utilities"),
        ("H&M ION ORCHARD", "shopping"),
        ("AMAZON.SG MARKETPLACE", "shopping"),
        ("ZIG BY CDG", "transport"),
        ("PUB WATER BILL", "utilities"),
    ],
)
def test_known_merchants(merchant, category):
    got, rule = categorize(merchant)
    assert got == category
    assert rule is not None


@pytest.mark.parametrize("merchant", ["Quirky Unknown Merchant 99", "", "PUBLIC LIBRARY FINE", "   "])
def test_unknown_merchants_hit_the_guardrail(merchant):
    assert categorize(merchant) == (FALLBACK_CATEGORY, None)


def test_keyword_matches_on_word_boundaries_only():
    # "pub" (utilities) must not match inside "public", and no other keyword
    # applies, so these land on the guardrail.
    assert categorize("PUBLIC LIBRARY")[0] == FALLBACK_CATEGORY
    assert categorize("REPUBLIC PLAZA CARPARK")[0] == FALLBACK_CATEGORY
    assert categorize("PUB BILL AUG")[0] == "utilities"


def test_normalize_strips_statement_noise():
    assert normalize_merchant("  NTUC   FAIRPRICE*#03-11 ") == "ntuc fairprice 03 11"


def test_all_unmapped_statement_still_completes():
    """Secondary success metric: 100% completion with unfamiliar merchants."""
    rows = [
        {"date": "2026-08-01", "merchant": "ZZZ MYSTERY VENDOR", "amount": 12.5, "card_id": "dbs_live_fresh"},
        {"date": "2026-08-02", "merchant": "ANOTHER UNKNOWN 123", "amount": 700.0, "card_id": "dbs_live_fresh"},
        {"date": "2026-08-03", "merchant": "", "amount": 3.0, "card_id": "uob_one"},
    ]
    result = analyze(rows, ["dbs_live_fresh", "uob_one"])
    payload = result.payload
    assert payload["summary"]["transaction_count"] == 3
    assert payload["parse_quality"]["general_spend_rows"] == 3
    assert payload["categories"][0]["key"] == "general_spend"
    assert payload["summary"]["actual_rewards"] >= 0
