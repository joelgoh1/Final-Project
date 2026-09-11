"""Wallet workbench: cards the UI edited or invented, validated into engine profiles.

The dashboard lets people drag cards in and out of their wallet and rewrite a
card's rules on its back. Those cards arrive here as `CustomCard`s. Reusing a
bundled id (e.g. ``uob_one``) replaces that card's rules for the analysis, so
the statement's own spend is re-scored under the edited terms; any other id
adds a brand-new card to the wallet. Nothing is persisted server-side.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from . import catalog
from .models import CardProfile

MAX_CUSTOM_CARDS = 12
CARD_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class CustomCard(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=60)
    issuer: str = Field(default="Custom", max_length=30)
    base_rate: float = Field(ge=0, le=1)
    category_rates: dict[str, float] = Field(default_factory=dict)
    min_spend: float = Field(default=0.0, ge=0, le=1_000_000)
    monthly_cap: Optional[float] = Field(default=None, ge=0, le=1_000_000)


def custom_card_profiles(custom_cards: Optional[list[CustomCard]]) -> dict[str, CardProfile]:
    """Validate workbench cards into engine profiles keyed by id."""
    if not custom_cards:
        return {}
    if len(custom_cards) > MAX_CUSTOM_CARDS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_CUSTOM_CARDS} custom cards per analysis.")
    known_categories = set(catalog.category_labels()) - {"general_spend"}
    bundled = catalog.cards()
    out: dict[str, CardProfile] = {}
    for card in custom_cards:
        if not CARD_ID_RE.match(card.id):
            raise HTTPException(
                status_code=422, detail=f"Card id '{card.id}' must be lowercase letters, digits, _ or -."
            )
        bad = sorted(set(card.category_rates) - known_categories)
        if bad:
            raise HTTPException(status_code=422, detail=f"Unknown categories on {card.name}: {', '.join(bad)}.")
        for key, rate in card.category_rates.items():
            if not 0 <= rate <= 1:
                raise HTTPException(status_code=422, detail=f"{card.name}: rate for {key} must be between 0 and 100%.")
        if card.id in out:
            raise HTTPException(status_code=422, detail=f"Card id '{card.id}' appears twice.")
        original = bundled.get(card.id)
        out[card.id] = CardProfile(
            id=card.id,
            name=card.name.strip() or card.id,
            issuer=card.issuer.strip() or "Custom",
            base_rate=float(card.base_rate),
            category_rates={k: float(v) for k, v in card.category_rates.items()},
            min_spend=float(card.min_spend),
            monthly_cap=None if card.monthly_cap is None else float(card.monthly_cap),
            assumptions=list(original.assumptions) if original else [],
        )
    return out


def parse_custom_cards_form(raw: Optional[str]) -> Optional[list[CustomCard]]:
    """The upload endpoint receives the cards as one JSON form field."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("expected a JSON list")
        return [CustomCard.model_validate(item) for item in data]
    except (ValueError, TypeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"custom_cards is not valid: {exc}")
