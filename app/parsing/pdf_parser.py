"""Unlocked PDF e-statement ingestion (DBS / OCBC / UOB text layouts).

Out of scope for v1, by design: password-protected PDFs and scanned or
image-only statements. Both fail with a clear message instead of a guess.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Optional

from .. import catalog
from ..models import ParseQuality
from .normalize import detect_issuer, detect_statement_year, parse_lines

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class StatementParseError(Exception):
    """Raised when a PDF cannot be read as an unlocked text statement."""


@dataclass
class ParsedStatement:
    rows: list[dict]
    issuer: Optional[str]
    card_id: str
    quality: ParseQuality = field(default_factory=ParseQuality)

    def merge_quality(self, previous: Optional[ParseQuality]) -> ParseQuality:
        """Combine this file's parse stats with the files already read."""
        if previous is None:
            return self.quality
        previous.lines_skipped += self.quality.lines_skipped
        previous.notes.extend(self.quality.notes)
        return previous


def _extract_text(data: bytes, filename: str) -> str:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise StatementParseError("pdfplumber is not installed.") from exc

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as exc:
        message = str(exc).lower()
        if "password" in message or "encrypt" in message:
            raise StatementParseError(
                f"{filename} is password-protected. v1 needs an unlocked PDF - "
                "open it in your PDF reader, save an unlocked copy, and try again."
            ) from exc
        raise StatementParseError(f"{filename} could not be read as a PDF ({exc}).") from exc


def parse_statement_pdf(
    data: bytes, filename: str, card_id_override: Optional[str] = None
) -> ParsedStatement:
    """Extract redacted transaction rows from one unlocked PDF e-statement."""
    text = _extract_text(data, filename)
    if not text.strip():
        raise StatementParseError(
            f"{filename} has no extractable text - it looks like a scan or image. "
            "v1 does not do OCR; use a digital e-statement or a demo fixture."
        )

    issuer = detect_issuer(text)
    rows, skipped = parse_lines(text.splitlines(), detect_statement_year(text))

    card_id = card_id_override or _default_card_for(issuer)
    for row in rows:
        row["card_id"] = card_id

    quality = ParseQuality(rows_parsed=len(rows), lines_skipped=skipped)
    card_name = catalog.cards()[card_id].name
    if card_id_override:
        quality.notes.append(f"{filename}: rows assigned to {card_name} (chosen by you).")
    elif issuer:
        quality.notes.append(f"{filename}: detected {issuer}, using {card_name} rules.")
    else:
        quality.notes.append(
            f"{filename}: issuer not recognised, defaulted to {card_name}. "
            "Pick the right card above and re-run for accurate rates."
        )
    return ParsedStatement(rows=rows, issuer=issuer, card_id=card_id, quality=quality)


def _default_card_for(issuer: Optional[str]) -> str:
    defaults = catalog.issuer_default_card()
    if issuer and issuer in defaults:
        return defaults[issuer]
    return next(iter(catalog.cards()))
