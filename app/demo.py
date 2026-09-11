"""Configurable, seeded synthetic statement generator for the demo path.

The static fixtures in data/fixtures are pinned by tests and stay as they are.
This module adds a second demo source: a statement generated on demand from a
small set of knobs (wallet, month, transaction count, total spend, category
mix, share of unfamiliar merchants, how spend is assigned to cards, seed).

    config = resolve_config(preset_id="household_3_card", overrides={"seed": 3})
    statement = generate(config)
    analyze(statement.rows, config.wallet, "demo", statement.meta)

Everything is deterministic for a given config, so a demo can be replayed, and
every merchant comes from data/demo_merchants.json where each entry is declared
under the category the keyword rules will assign it to (tests enforce this).
"""

from __future__ import annotations

import calendar
import json
import random
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from . import catalog
from .catalog import DATA_DIR

MERCHANTS_FILE = DATA_DIR / "demo_merchants.json"
PRESETS_FILE = DATA_DIR / "demo_presets.json"

CardMode = Literal["spread", "primary", "habit", "best_rate"]
CARD_MODES: dict[str, str] = {
    "spread": "Spread at random across the wallet",
    "primary": "Everything on one primary card",
    "habit": "Mostly one habit card, some spillover",
    "best_rate": "Each purchase on its best-rate card",
}
HABIT_PRIMARY_SHARE = 0.7
UNMAPPED_KEY = "__unmapped__"

LIMITS = {
    "transaction_count": {"min": 5, "max": 200},
    "total_spend": {"min": 100, "max": 50000},
    "unmapped_pct": {"min": 0, "max": 60},
}


# --------------------------------------------------------------------------- #
# Data files
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _merchants_raw() -> dict:
    return json.loads(MERCHANTS_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _presets_raw() -> dict:
    return json.loads(PRESETS_FILE.read_text(encoding="utf-8"))


def merchant_pool() -> dict[str, list[dict]]:
    """Category -> [{name, range}], plus UNMAPPED_KEY for guardrail merchants."""
    raw = _merchants_raw()
    pool = {k: list(v) for k, v in raw["categories"].items()}
    pool[UNMAPPED_KEY] = list(raw.get("unmapped", []))
    return pool


def mix_categories() -> list[str]:
    """Categories the mix can reference, in merchant_rules.json order."""
    known = catalog.category_labels()
    fallback = catalog.merchant_rules().get("fallback_category", "general_spend")
    pool = _merchants_raw()["categories"]
    return [k for k in known if k != fallback and k in pool]


def defaults() -> dict:
    return dict(_presets_raw()["defaults"])


def presets() -> list[dict]:
    return [dict(p) for p in _presets_raw()["presets"]]


def preset(preset_id: str) -> dict:
    for item in _presets_raw()["presets"]:
        if item["id"] == preset_id:
            return dict(item)
    raise KeyError(preset_id)


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


class DemoConfig(BaseModel):
    """Fully resolved generator settings. Validated against the card catalog."""

    wallet: list[str] = Field(min_length=1)
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    transaction_count: int = Field(ge=LIMITS["transaction_count"]["min"], le=LIMITS["transaction_count"]["max"])
    total_spend: float = Field(ge=LIMITS["total_spend"]["min"], le=LIMITS["total_spend"]["max"])
    mix: dict[str, float]
    unmapped_pct: float = Field(ge=LIMITS["unmapped_pct"]["min"], le=LIMITS["unmapped_pct"]["max"])
    card_mode: CardMode = "spread"
    primary_card: Optional[str] = None
    seed: int = Field(ge=0, le=2**31 - 1)
    label: Optional[str] = Field(default=None, max_length=80)

    @field_validator("wallet")
    @classmethod
    def _known_cards(cls, value: list[str]) -> list[str]:
        known = catalog.cards()
        unknown = [c for c in value if c not in known]
        if unknown:
            raise ValueError(f"Unknown card ids: {', '.join(unknown)}. Known: {', '.join(known)}.")
        # Catalog order, de-duplicated, so the engine's tie-breaks stay stable.
        return [c for c in known if c in value]

    @field_validator("mix")
    @classmethod
    def _known_mix(cls, value: dict[str, float]) -> dict[str, float]:
        allowed = mix_categories()
        unknown = [k for k in value if k not in allowed]
        if unknown:
            raise ValueError(f"Unknown mix categories: {', '.join(unknown)}. Allowed: {', '.join(allowed)}.")
        if any(v < 0 for v in value.values()):
            raise ValueError("Mix weights cannot be negative.")
        if sum(value.values()) <= 0:
            raise ValueError("At least one mix category needs a positive weight.")
        return {k: float(value.get(k, 0.0)) for k in allowed}

    @model_validator(mode="after")
    def _primary_in_wallet(self) -> "DemoConfig":
        if self.card_mode in ("primary", "habit"):
            if self.primary_card is None:
                self.primary_card = self.wallet[0]
            elif self.primary_card not in self.wallet:
                raise ValueError(f"primary_card '{self.primary_card}' is not in the wallet.")
        return self


def resolve_config(preset_id: Optional[str] = None, overrides: Optional[dict[str, Any]] = None) -> DemoConfig:
    """defaults -> preset -> explicit overrides (None values are ignored)."""
    base = defaults()
    if preset_id:
        base.update(preset(preset_id)["config"])
    for key, value in (overrides or {}).items():
        if value is not None:
            base[key] = value
    return DemoConfig(**base)


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


@dataclass
class DemoStatement:
    rows: list[dict]
    meta: dict
    config: DemoConfig
    category_spend: dict[str, float] = field(default_factory=dict)

    def to_fixture(self, fixture_id: str) -> dict:
        """Same shape as data/fixtures/*.json, so a good run can be frozen."""
        return {
            "id": fixture_id,
            "label": self.meta["label"],
            "description": self.meta["description"],
            "cycle_label": self.meta["cycle_label"],
            "wallet": list(self.config.wallet),
            "note": "Synthetic data generated by app.demo. No real cardholder, account or card number appears in this file.",
            "generator_config": self.config.model_dump(),
            "transactions": [dict(r) for r in self.rows],
        }


def _largest_remainder(targets: dict[str, float], total: int, minimum: int = 1) -> dict[str, int]:
    """Integer counts summing to `total`, proportional to `targets`, each >= minimum where affordable."""
    keys = [k for k, v in targets.items() if v > 0]
    if not keys:
        return {}
    minimum = min(minimum, total // len(keys))
    remaining = total - minimum * len(keys)
    weight_sum = sum(targets[k] for k in keys)
    exact = {k: remaining * targets[k] / weight_sum for k in keys}
    counts = {k: minimum + int(exact[k]) for k in keys}
    leftover = total - sum(counts.values())
    for k in sorted(keys, key=lambda k: (-(exact[k] - int(exact[k])), k))[:leftover]:
        counts[k] += 1
    return counts


def _cycle_label(month: str) -> str:
    year, mon = (int(p) for p in month.split("-"))
    last = calendar.monthrange(year, mon)[1]
    return f"1 - {last} {calendar.month_abbr[mon]} {year}"


def _best_rate_card(category: str, wallet: list) -> str:
    return max(wallet, key=lambda c: (c.rate_for(category), -wallet.index(c))).id


def _describe(config: DemoConfig, unmapped_rows: int) -> str:
    cards = catalog.cards()
    names = ", ".join(cards[c].name for c in config.wallet)
    top = sorted(config.mix.items(), key=lambda kv: -kv[1])[:2]
    labels = catalog.category_labels()
    lead = " and ".join(labels.get(k, k) for k, v in top if v > 0)
    mode = CARD_MODES[config.card_mode].lower()
    if config.primary_card and config.card_mode in ("primary", "habit"):
        mode += f" ({cards[config.primary_card].name})"
    return (
        f"Generated {config.transaction_count} transactions worth about ${config.total_spend:,.0f} "
        f"across {names}. Spend leans {lead}; {mode}; {unmapped_rows} merchant{'' if unmapped_rows == 1 else 's'} the categorizer does not know. "
        f"Seed {config.seed}."
    )


def generate(config: DemoConfig) -> DemoStatement:
    """Deterministic synthetic statement for `config`."""
    rng = random.Random(config.seed)
    pool = merchant_pool()
    wallet = [catalog.card(c) for c in config.wallet]

    # Spend targets per bucket. The unmapped share comes off the top; the rest
    # follows the mix weights.
    mapped_total = config.total_spend * (1 - config.unmapped_pct / 100)
    weight_sum = sum(config.mix.values())
    targets = {k: mapped_total * v / weight_sum for k, v in config.mix.items() if v > 0}
    if config.unmapped_pct > 0:
        targets[UNMAPPED_KEY] = config.total_spend * config.unmapped_pct / 100

    # Row counts: proportional to target / typical ticket, so cheap categories
    # get more lines, with at least one line per bucket where the count allows.
    typical = {
        k: sum((m["range"][0] + m["range"][1]) / 2 for m in pool[k]) / len(pool[k]) for k in targets
    }
    counts = _largest_remainder({k: targets[k] / typical[k] for k in targets}, config.transaction_count)

    year, mon = (int(p) for p in config.month.split("-"))
    days = calendar.monthrange(year, mon)[1]

    rows: list[dict] = []
    category_spend: dict[str, float] = {}
    for bucket in targets:
        n = counts.get(bucket, 0)
        if n == 0:
            continue
        merchants = pool[bucket]
        picks = rng.sample(merchants, n) if n <= len(merchants) else [rng.choice(merchants) for _ in range(n)]
        amounts = [rng.uniform(*m["range"]) for m in picks]
        scale = targets[bucket] / sum(amounts)
        amounts = [max(0.5, round(a * scale, 2)) for a in amounts]
        # Push the rounding residue onto the largest line so the bucket total is exact.
        residue = round(targets[bucket] - sum(amounts), 2)
        idx = max(range(n), key=lambda i: amounts[i])
        amounts[idx] = max(0.5, round(amounts[idx] + residue, 2))

        category = "general_spend" if bucket == UNMAPPED_KEY else bucket
        for merchant, amount in zip(picks, amounts):
            rows.append(
                {
                    "date": f"{year:04d}-{mon:02d}-{rng.randint(1, days):02d}",
                    "merchant": merchant["name"],
                    "amount": amount,
                    "card_id": None,
                    "_category": category,
                }
            )
        category_spend[category] = round(sum(amounts), 2)

    rows.sort(key=lambda r: r["date"])

    others = [c for c in config.wallet if c != config.primary_card]
    for row in rows:
        if config.card_mode == "primary":
            row["card_id"] = config.primary_card
        elif config.card_mode == "habit":
            row["card_id"] = (
                config.primary_card if not others or rng.random() < HABIT_PRIMARY_SHARE else rng.choice(others)
            )
        elif config.card_mode == "best_rate":
            row["card_id"] = _best_rate_card(row["_category"], wallet)
        else:
            row["card_id"] = rng.choice(config.wallet)
        del row["_category"]

    unmapped_rows = counts.get(UNMAPPED_KEY, 0)
    meta = {
        "kind": "demo",
        "label": config.label or "Custom demo statement",
        "cycle_label": _cycle_label(config.month),
        "description": _describe(config, unmapped_rows),
    }
    return DemoStatement(rows=rows, meta=meta, config=config, category_spend=category_spend)


def bootstrap_payload() -> dict:
    """Everything the customize panel needs."""
    labels = catalog.category_labels()
    return {
        "defaults": defaults(),
        "presets": presets(),
        "card_modes": [{"id": k, "label": v} for k, v in CARD_MODES.items()],
        "categories": [{"key": k, "label": labels.get(k, k)} for k in mix_categories()],
        "limits": LIMITS,
    }
