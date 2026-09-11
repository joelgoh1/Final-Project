"""Loads the local, bundled rule data (cards + merchant map).

These are the only files the app reads. There are no network calls and no
live reward-rule APIs in v1 - everything ships in data/.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .models import CardProfile

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CARDS_FILE = DATA_DIR / "cards.json"
MERCHANT_RULES_FILE = DATA_DIR / "merchant_rules.json"
FIXTURES_DIR = DATA_DIR / "fixtures"


@lru_cache(maxsize=1)
def _cards_raw() -> dict:
    return json.loads(CARDS_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def merchant_rules() -> dict:
    return json.loads(MERCHANT_RULES_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def cards() -> dict[str, CardProfile]:
    """Card profiles keyed by id, in the order declared in cards.json."""
    out: dict[str, CardProfile] = {}
    for raw in _cards_raw()["cards"]:
        out[raw["id"]] = CardProfile(
            id=raw["id"],
            name=raw["name"],
            issuer=raw["issuer"],
            base_rate=float(raw["base_rate"]),
            category_rates={k: float(v) for k, v in raw.get("category_rates", {}).items()},
            min_spend=float(raw.get("min_spend") or 0.0),
            monthly_cap=(None if raw.get("monthly_cap") is None else float(raw["monthly_cap"])),
            reward_unit=raw.get("reward_unit", "cashback"),
            assumptions=list(raw.get("assumptions", [])),
        )
    return out


def card(card_id: str) -> CardProfile:
    return cards()[card_id]


def global_assumptions() -> list[str]:
    return list(_cards_raw().get("global_assumptions", []))


def category_labels() -> dict[str, str]:
    return dict(merchant_rules()["categories"])


def issuer_default_card() -> dict[str, str]:
    """First card of each issuer - used when a PDF's issuer is detected."""
    out: dict[str, str] = {}
    for profile in cards().values():
        out.setdefault(profile.issuer.upper(), profile.id)
    return out


def fixtures() -> list[dict]:
    """Fixture metadata for the first screen, sorted by filename."""
    out = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        out.append(
            {
                "id": raw["id"],
                "label": raw["label"],
                "description": raw["description"],
                "cycle_label": raw["cycle_label"],
                "wallet": raw["wallet"],
                "transaction_count": len(raw["transactions"]),
            }
        )
    return out


def fixture(fixture_id: str) -> dict:
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw["id"] == fixture_id:
            return raw
    raise KeyError(fixture_id)
