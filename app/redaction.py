"""In-memory redaction, applied the moment text leaves the PDF or fixture.

v1 keeps exactly three things per line - date, merchant, amount. Cardholder
names, addresses and full card numbers are removed here, before anything is
categorized, scored, cached in a session, or written to a CSV export.
"""

from __future__ import annotations

import re

# 12-19 digit card numbers, optionally split by spaces or dashes.
_CARD_NUMBER = re.compile(r"\b(?:\d[ -]?){11,18}\d\b")
# Any other long digit run (account/reference numbers).
_LONG_DIGITS = re.compile(r"\b\d{7,}\b")

# Lines in a statement header that carry personal details rather than spend.
_PII_LINE_PATTERNS = [
    re.compile(r"^\s*(mr|mrs|ms|mdm|dr|miss)\b[\. ]", re.I),
    re.compile(r"^\s*(blk|block|apt|unit|no\.)\s*\d", re.I),
    re.compile(r"\bsingapore\s+\d{6}\b", re.I),
    re.compile(r"^\s*#\d{1,3}[- ]\d{1,4}\s*$"),
    re.compile(r"\b(statement address|account holder|card ?holder name|nric|fin)\b", re.I),
]

CARD_NUMBER_MASK = "**** {last4}"


def mask_card_numbers(text: str) -> str:
    """Replace any full card number with a masked last-4 form."""

    def _mask(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return CARD_NUMBER_MASK.format(last4=digits[-4:])

    return _CARD_NUMBER.sub(_mask, text)


def is_personal_line(line: str) -> bool:
    """True for statement lines that carry names, addresses or identifiers."""
    return any(pattern.search(line) for pattern in _PII_LINE_PATTERNS)


def redact_line(line: str) -> str:
    """Mask identifiers in a raw statement line before it is parsed."""
    return mask_card_numbers(line)


def redact_merchant(merchant: str) -> str:
    """Merchant descriptors can carry reference numbers - strip them."""
    text = mask_card_numbers(merchant)
    text = _LONG_DIGITS.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip(" -*")


def redact_lines(lines: list[str]) -> tuple[list[str], int]:
    """Drop personal lines and mask the rest. Returns (kept_lines, dropped_count)."""
    kept, dropped = [], 0
    for line in lines:
        if is_personal_line(line):
            dropped += 1
            continue
        kept.append(redact_line(line))
    return kept, dropped
