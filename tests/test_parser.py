"""PDF ingestion, line normalisation and redaction."""

from pathlib import Path

import pytest

from app.parsing.normalize import detect_issuer, detect_statement_year, parse_lines
from app.parsing.pdf_parser import StatementParseError, parse_statement_pdf
from app.redaction import is_personal_line, mask_card_numbers, redact_merchant

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.mark.parametrize(
    "filename, issuer, card_id, expected_rows",
    [
        ("dbs_live_fresh_statement.pdf", "DBS", "dbs_live_fresh", 14),
        ("ocbc_365_statement.pdf", "OCBC", "ocbc_365", 12),
        ("uob_one_statement.pdf", "UOB", "uob_one", 10),
    ],
)
def test_sample_statements_parse_exactly(filename, issuer, card_id, expected_rows):
    parsed = parse_statement_pdf((SAMPLES / filename).read_bytes(), filename)
    assert parsed.issuer == issuer
    assert parsed.card_id == card_id
    assert len(parsed.rows) == expected_rows
    assert parsed.quality.lines_skipped == 0
    assert all(row["amount"] > 0 for row in parsed.rows)


def test_personal_details_never_reach_the_rows():
    parsed = parse_statement_pdf((SAMPLES / "dbs_live_fresh_statement.pdf").read_bytes(), "dbs.pdf")
    blob = " ".join(row["merchant"] for row in parsed.rows)
    assert "TAN AH KOW" not in blob
    assert "ANG MO KIO" not in blob
    assert "4123" not in blob and "2345" not in blob


def test_card_override_wins_over_detection():
    parsed = parse_statement_pdf(
        (SAMPLES / "dbs_live_fresh_statement.pdf").read_bytes(), "dbs.pdf", card_id_override="uob_one"
    )
    assert parsed.card_id == "uob_one"
    assert all(row["card_id"] == "uob_one" for row in parsed.rows)


def test_garbage_bytes_raise_a_clear_error():
    with pytest.raises(StatementParseError):
        parse_statement_pdf(b"not a pdf at all", "junk.pdf")


def test_parse_lines_handles_three_layouts_and_skips_furniture():
    lines = [
        "MR TAN AH KOW",
        "TOTAL NEW BALANCE                    1,342.75",
        "02 Aug  SHOPEE SG PTE LTD                  128.90",
        "01/08/2026  02/08/2026  KOUFU CLEMENTI MALL      11.60",
        "02 AUG 2026  03 AUG 2026  SHENG SIONG SUPERMARKET   63.40",
        "05/08/2026  06/08/2026  PAYMENT RECEIVED - THANK YOU   120.00 CR",
        "31 Feb 2026  IMPOSSIBLE DATE                     5.00",
    ]
    rows, skipped = parse_lines(lines, 2026)
    assert [r["merchant"] for r in rows] == ["SHOPEE SG PTE LTD", "KOUFU CLEMENTI MALL", "SHENG SIONG SUPERMARKET"]
    assert [r["date"] for r in rows] == ["2026-08-02", "2026-08-01", "2026-08-02"]
    assert rows[0]["amount"] == 128.9
    assert skipped == 1  # the impossible date


def test_issuer_and_year_detection():
    assert detect_issuer("DBS Bank Ltd statement") == "DBS"
    assert detect_issuer("United Overseas Bank Limited") == "UOB"
    assert detect_issuer("Some Other Bank") is None
    assert detect_statement_year("Statement Date 31 AUG 2026 ... 2025") == 2026


def test_redaction_helpers():
    assert mask_card_numbers("CARD NO 4123 4567 8901 2345") == "CARD NO **** 2345"
    assert redact_merchant("GRABFOOD SINGAPORE REF 8827361192") == "GRABFOOD SINGAPORE REF"
    assert is_personal_line("MR TAN AH KOW")
    assert is_personal_line("BLK 123 ANG MO KIO AVE 3")
    assert is_personal_line("SINGAPORE 560123")
    assert not is_personal_line("NTUC FAIRPRICE 45.20")
