"""Turn raw statement text lines into date / merchant / amount rows.

Bank e-statements differ in column layout but share a shape: a leading
transaction date, a merchant description, and a trailing amount. These helpers
match that shape for DBS, OCBC and UOB layouts and skip everything else
(headers, totals, payments) rather than guessing.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from ..redaction import is_personal_line, redact_line

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

ISSUER_KEYWORDS = {
    "DBS": ["dbs bank", "dbs card", "posb", "dbs live fresh", "dbs altitude"],
    "OCBC": ["ocbc bank", "oversea-chinese", "ocbc 365", "ocbc card"],
    "UOB": ["united overseas bank", "uob card", "uob one", "uob lady"],
}

# Lines that are statement furniture, not spend.
_SKIP_KEYWORDS = [
    "total", "balance", "previous", "minimum payment", "payment due", "interest",
    "late charge", "annual fee", "credit limit", "available credit", "statement of account",
    "brought forward", "sub-total", "subtotal", "gst", "cash rebate", "rebate earned",
    "payment received", "autopay", "thank you", "page ", "transaction date", "description",
    "new balance", "amount (sgd)", "opening balance",
]

_AMOUNT = re.compile(r"(-?\$?\d{1,3}(?:,\d{3})*\.\d{2})\s*(CR|DR)?\s*$", re.I)
_DATE_DMY = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
_DATE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\b")
_DATE_DMON = re.compile(r"^(\d{1,2})\s*[- ]\s*([A-Za-z]{3})[A-Za-z]*\.?\s*(\d{2,4})?\b")
_YEAR = re.compile(r"\b(20\d{2})\b")


def detect_issuer(text: str) -> Optional[str]:
    """Which bank issued this statement, from its header text."""
    haystack = text.lower()
    for issuer, keywords in ISSUER_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return issuer
    return None


def detect_statement_year(text: str) -> int:
    """Latest 4-digit year in the document, falling back to this year."""
    years = [int(match) for match in _YEAR.findall(text)]
    plausible = [y for y in years if 2000 <= y <= dt.date.today().year + 1]
    return max(plausible) if plausible else dt.date.today().year


def _parse_date(line: str, year: int) -> Optional[tuple[Optional[str], str]]:
    """Return (iso_date, rest_of_line) if the line starts with a date."""
    match = _DATE_ISO.match(line)
    if match:
        y, m, d = (int(g) for g in match.groups())
        return _iso(y, m, d), line[match.end():]

    match = _DATE_DMY.match(line)
    if match:
        d, m, y = (int(g) for g in match.groups())
        if y < 100:
            y += 2000
        return _iso(y, m, d), line[match.end():]

    match = _DATE_DMON.match(line)
    if match:
        day, month_name, maybe_year = match.groups()
        month = MONTHS.get(month_name.lower()[:3])
        if month is None:
            return None
        y = int(maybe_year) if maybe_year else year
        if y < 100:
            y += 2000
        return _iso(y, month, int(day)), line[match.end():]
    return None


def _iso(year: int, month: int, day: int) -> Optional[str]:
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def _is_skippable(line: str) -> bool:
    lowered = line.lower()
    return any(keyword in lowered for keyword in _SKIP_KEYWORDS)


def parse_lines(lines: list[str], statement_year: int) -> tuple[list[dict], int]:
    """Extract (date, merchant, amount) rows. Returns (rows, skipped_line_count)."""
    rows: list[dict] = []
    skipped = 0
    for raw_line in lines:
        line = raw_line.strip()
        if not line or is_personal_line(line):
            continue
        line = redact_line(line)

        amount_match = _AMOUNT.search(line)
        parsed_date = _parse_date(line, statement_year)
        if not amount_match or not parsed_date:
            continue  # not a transaction line (header, total, marketing copy)
        if parsed_date[0] is None:
            skipped += 1  # a date-shaped prefix that is not a real date
            continue
        if _is_skippable(line):
            continue
        if (amount_match.group(2) or "").upper() == "CR":
            continue  # refunds and rebates are not spend

        iso_date, remainder = parsed_date
        merchant = remainder[: amount_match.start() - (len(line) - len(remainder))]
        # A second date column (posting date) sometimes follows the first.
        second = _parse_date(merchant.strip(), statement_year)
        if second is not None and second[0] is not None:
            merchant = second[1]
        merchant = re.sub(r"\s{2,}", " ", merchant).strip(" .-*")
        amount = float(amount_match.group(1).replace(",", "").replace("$", ""))
        if not merchant or amount <= 0:
            skipped += 1
            continue
        rows.append({"date": iso_date, "merchant": merchant, "amount": round(amount, 2)})
    return rows, skipped
