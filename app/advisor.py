"""Agentic strategist: a reasoning model that plans next month's wallet.

The deterministic engine answers "what did this cycle cost you?". This module
answers the harder question - "what three rules should you actually follow?" -
with an LLM agent that reasons over the statement using tools backed by the
same engine.

Provider: OpenCode (OpenAI-compatible chat-completions endpoint), configured by
OPENCODE_API_KEY, LLM_BASE_URL (default https://opencode.ai/zen/go/v1) and
LLM_MODEL (default deepseek-v4.1-flash). Plain httpx, no vendor SDK.

Design rules:
  * Every number the agent quotes is produced by app.rules_engine, never by the
    model. The server re-scores the agent's final recommendation before it
    reaches the dashboard, so the UI cannot show an unverified figure.
  * Only redacted rows (date, merchant, amount, category, card used) are sent
    to the model API. Names, addresses and card numbers were removed at
    extraction, before this module sees anything.
  * If no credential is configured, the app stays fully usable: the
    deterministic dashboard is unaffected and the advisor reports itself
    unavailable.
  * If the provider rejects function calling, the strategist falls back to one
    request with engine-precomputed candidate strategies and no tools.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from . import catalog
from .models import CardProfile, Transaction
from .rules_engine import Allocation, allocate_optimal, best_rule_plan, score_actual, score_strategy

DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"
# Optional OpenAI-style reasoning knob (LLM_REASONING_EFFORT=low|medium|high|none).
# Unset by default: the strategist is a judgment task and keeps the provider's
# full reasoning depth. Measured on deepseek-v4.1-flash via OpenCode for
# reference: default ~8k reasoning tokens / 40 s per turn, low ~1.7k / 10 s.
DEFAULT_REASONING_EFFORT = ""
MAX_RULES = 3
MAX_TOOL_TURNS = 12
# Turns in which the model may call score_allocation before it must answer.
MAX_EXPLORATION_ROUNDS = 1
REQUEST_TIMEOUT_SECONDS = 120.0

CATEGORIES = ["dining", "groceries", "transport", "shopping", "utilities", "general_spend"]

SYSTEM_PROMPT = """You are a Singapore credit-card rewards strategist.

You are given one statement cycle of spend that has already been parsed,
redacted and categorized. Your job is to design the wallet strategy for NEXT
cycle: at most three rules of the form "put <category> on <card>", plus which
card to use for everything else.

Work like an analyst, not a calculator:
  * Call get_card_rules and get_spend_summary first. Minimum spends and monthly
    caps are what make this non-obvious - a 10% rate on a card that never clears
    its minimum spend is worth 0.33%, and a 6% rate is worthless once the cap is
    full.
  * The search is already done. Your first message includes engine-scored
    baselines, among them the EXHAUSTIVE best plan with 1, 2 and 3 rules -
    provably optimal for their rule budget. Do not try to out-search them.
    Your value is judgment: is the 3-rule optimum worth its complexity over the
    1- or 2-rule plan? Which minimum spend or cap makes it fragile? Say so.
  * SKILLS for anything you still want to check: best_rule_plan (exact search
    for a rule budget), best_rate_plan (the naive plan), fix_minimums (repair a
    plan whose cards miss their minimum), score_allocation (score any plan).
    Prefer calling them over reasoning the moves out by hand. All numbers come
    from the real rules engine and are ground truth.
  * Prefer a strategy a human can actually follow at the till. Three rules
    maximum, and each rule must name one category and one card.
  * Be honest about the trade-offs: if clearing a minimum spend on one card
    starves another card's bonus, say so.
  * Be fast. The card rules, the spend summary and several engine-scored
    baseline strategies are already in your first message - do not re-fetch
    them. Put every score_allocation call you want into ONE turn (they run in
    parallel), then answer. Keep any visible reasoning to a few short lines;
    the numbers come from the tools, not from your arithmetic.

Data note: the rows you can see contain only date, merchant, amount, category
and which card was used. No cardholder names, addresses or card numbers exist
in this data.

When you are done, reply with ONLY a JSON object (no prose, no code fence):
{
  "headline": "<one sentence a cardholder can act on, under 140 characters>",
  "recommended_rules": [{"category": "<dining|groceries|transport|shopping|utilities|general_spend>",
                         "card_id": "<card id from the wallet>", "rationale": "<one clause>"}],
  "default_card_id": "<card id for every category with no rule>",
  "projected_rewards": <the total_reward score_allocation returned for this exact plan>,
  "reasoning_summary": "<2-4 sentences on why this beats the alternatives you tested>",
  "watch_outs": ["<cap or minimum spend that could break the plan>"]
}
Set projected_rewards to a number score_allocation actually returned - never
compute it yourself."""

SINGLE_SHOT_PROMPT = """You are a Singapore credit-card rewards strategist.

Below is one redacted statement cycle (date, merchant, amount, category, card
used), the wallet's card rules, and several candidate strategies that have
ALREADY been scored by the deterministic rules engine. Those scores are ground
truth; you cannot run the engine yourself in this mode.

Pick or adapt the strategy a real person can follow: at most three rules of the
form "put <category> on <card>" plus one default card. Weigh minimum spends and
caps explicitly. If you adapt a candidate, say so, and set projected_rewards to
the score of the closest scored candidate - the server will re-score your exact
plan before showing it.

Reply with ONLY a JSON object with these keys:
  headline (string, <140 chars), recommended_rules (array of 1-3 objects with
  category, card_id, rationale), default_card_id (string), projected_rewards
  (number), reasoning_summary (string), watch_outs (array of up to 3 strings).
Categories: dining, groceries, transport, shopping, utilities, general_spend."""


@dataclass
class AdvisorRule:
    category: str
    category_label: str
    card_id: str
    card_name: str
    rationale: str


@dataclass
class AdvisorResult:
    mode: str  # "agent" | "unavailable" | "error"
    headline: str = ""
    rules: list[AdvisorRule] = field(default_factory=list)
    default_card_id: str = ""
    default_card_name: str = ""
    verified_rewards: float = 0.0  # re-scored by the engine, not by the model
    model_claimed_rewards: Optional[float] = None
    reasoning_summary: str = ""
    watch_outs: list[str] = field(default_factory=list)
    strategies_tested: int = 0
    model: str = ""
    provider: str = ""
    path: str = ""  # "tool-loop" or "single-shot"
    detail: str = ""
    trace: list[dict] = field(default_factory=list)  # one entry per model turn
    best_engine_plan: Optional[dict] = None  # exhaustive baseline the plan is judged against

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "trace": self.trace,
            "best_engine_plan": self.best_engine_plan,
            "headline": self.headline,
            "rules": [
                {
                    "category": r.category,
                    "category_label": r.category_label,
                    "card_id": r.card_id,
                    "card_name": r.card_name,
                    "rationale": r.rationale,
                }
                for r in self.rules
            ],
            "default_card_id": self.default_card_id,
            "default_card_name": self.default_card_name,
            "verified_rewards": round(self.verified_rewards, 2),
            "model_claimed_rewards": self.model_claimed_rewards,
            "reasoning_summary": self.reasoning_summary,
            "watch_outs": self.watch_outs,
            "strategies_tested": self.strategies_tested,
            "model": self.model,
            "provider": self.provider,
            "path": self.path,
            "detail": self.detail,
        }


# --------------------------------------------------------------------------- #
# Provider client (OpenAI-compatible chat completions over httpx)
# --------------------------------------------------------------------------- #


def settings() -> dict:
    """Read provider settings at call time so a freshly loaded .env is honoured."""
    effort = (os.getenv("LLM_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT).strip().lower()
    return {
        "api_key": os.getenv("OPENCODE_API_KEY", "").strip(),
        "base_url": (os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        "model": os.getenv("LLM_MODEL") or DEFAULT_MODEL,
        # "" / "default" leaves the provider's own reasoning depth untouched.
        "reasoning_effort": "" if effort in ("", "default") else effort,
    }


class ProviderError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class ChatClient:
    """Minimal OpenAI-compatible chat-completions client."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        session_id: Optional[str] = None,
        reasoning_effort: str = "",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # OpenAI-style knob; dropped automatically if the provider rejects it.
        self.reasoning_effort = reasoning_effort
        # OpenCode Go routes by session; one id per strategist run keeps the
        # whole tool loop on the same upstream. Harmless for other providers.
        self.session_id = session_id or uuid.uuid4().hex

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        response_format: Optional[dict] = None,
        temperature: float = 0.2,
    ) -> dict:
        import httpx

        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if response_format:
            body["response_format"] = response_format
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "x-opencode-session": self.session_id,
        }
        try:
            response = httpx.post(f"{self.base_url}/chat/completions", headers=headers, json=body, timeout=self.timeout)
            if response.status_code == 400 and "reasoning_effort" in body:
                # Provider does not know the knob: drop it for the rest of the run.
                self.reasoning_effort = ""
                body.pop("reasoning_effort")
                response = httpx.post(f"{self.base_url}/chat/completions", headers=headers, json=body, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ProviderError(f"network error talking to {self.base_url}: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(
                f"{self.base_url} returned {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("provider returned a non-JSON body") from exc


def advisor_status() -> dict:
    """Whether the reasoning model can be reached, without calling it."""
    cfg = settings()
    if not cfg["api_key"]:
        return {
            "available": False,
            "model": cfg["model"],
            "provider": cfg["base_url"],
            "reason": "No model credential found. Set OPENCODE_API_KEY in .env to enable the AI strategist.",
        }
    return {"available": True, "model": cfg["model"], "provider": cfg["base_url"], "reason": ""}


# --------------------------------------------------------------------------- #
# Engine-backed payloads and tools
# --------------------------------------------------------------------------- #


def _cards_for(wallet: Sequence[CardProfile]) -> dict[str, CardProfile]:
    """Bundled catalog with the session's (possibly edited) wallet layered on top."""
    cards = dict(catalog.cards())
    for card in wallet:
        cards[card.id] = card
    return cards


def _cards_payload(wallet: Sequence[CardProfile]) -> list[dict]:
    return [
        {
            "card_id": card.id,
            "name": card.name,
            "issuer": card.issuer,
            "base_rate": card.base_rate,
            "category_rates": card.category_rates,
            "min_spend_per_cycle": card.min_spend,
            "monthly_bonus_cap": card.monthly_cap,
            "modelling_assumptions": card.assumptions,
        }
        for card in wallet
    ]


def _allocation_payload(allocation: Allocation, wallet: Sequence[CardProfile]) -> dict:
    return {
        "total_reward": round(allocation.total_reward, 2),
        "cards": [
            {
                "card_id": card.id,
                "name": card.name,
                "spend": round(allocation.ledgers[card.id].spend, 2),
                "reward": round(allocation.ledgers[card.id].reward, 2),
                "min_spend_met": card.id in allocation.bonus_cards,
                "bonus_cap_reached": card.id in allocation.capped_cards,
            }
            for card in wallet
            if card.id in allocation.ledgers
        ],
    }


def _rows_payload(transactions: Sequence[Transaction], cards: dict[str, CardProfile]) -> list[dict]:
    return [
        {
            "date": t.date,
            "merchant": t.merchant,
            "amount": t.amount,
            "category": t.category,
            "card_used": cards[t.card_id].name,
        }
        for t in transactions
    ]


def build_tools(
    transactions: Sequence[Transaction],
    wallet: Sequence[CardProfile],
    dashboard: dict,
    tested: list[dict],
) -> tuple[list[dict], dict[str, Callable[[dict], str]]]:
    """The agent's tool surface: (OpenAI tool specs, name -> implementation).

    Every tool is backed by the rules engine. `tested` collects each strategy
    the agent scores so the UI can say how many candidates were explored.
    """
    cards = _cards_for(wallet)
    actual = score_actual(transactions, cards)
    wallet_ids = {c.id for c in wallet}

    def get_card_rules(_: dict) -> str:
        return json.dumps(
            {"cards": _cards_payload(wallet), "global_assumptions": catalog.global_assumptions()}
        )

    def get_spend_summary(_: dict) -> str:
        return json.dumps(
            {
                "cycle": dashboard["source"].get("cycle_label", "one statement cycle"),
                "total_spend": dashboard["summary"]["total_spend"],
                "transaction_count": dashboard["summary"]["transaction_count"],
                "by_category": dashboard["categories"],
                "actual_allocation": _allocation_payload(actual, wallet),
                "engine_per_transaction_optimum": dashboard["summary"]["optimal_rewards"],
            }
        )

    def list_transactions(args: dict) -> str:
        category = str(args.get("category") or "all")
        min_amount = float(args.get("min_amount") or 0.0)
        limit = int(args.get("limit") or 40)
        rows = [
            row
            for row in _rows_payload(transactions, cards)
            if (category in ("all", "") or row["category"] == category) and row["amount"] >= min_amount
        ]
        rows.sort(key=lambda r: -r["amount"])
        return json.dumps({"row_count": len(rows), "rows": rows[: max(1, min(limit, 200))]})

    def score_allocation(args: dict) -> str:
        rules = args.get("rules", [])
        if isinstance(rules, str):
            try:
                rules = json.loads(rules)
            except ValueError as exc:
                return json.dumps({"error": f"rules must be a JSON array: {exc}"})
        try:
            mapping = {str(r["category"]): str(r["card_id"]) for r in rules}
        except (KeyError, TypeError) as exc:
            return json.dumps({"error": f"each rule needs category and card_id: {exc}"})
        default_card_id = str(args.get("default_card_id") or "")

        requested = list(mapping.values()) + ([default_card_id] if default_card_id else [])
        unknown = [cid for cid in requested if cid not in wallet_ids]
        if unknown:
            return json.dumps({"error": f"Unknown card ids {unknown}. Wallet holds {sorted(wallet_ids)}."})

        return json.dumps(_score_plan(mapping, default_card_id or None))

    def _score_plan(mapping: dict[str, str], default_card_id: Optional[str], label: str = "") -> dict:
        allocation = score_strategy(transactions, wallet, mapping, default_card_id)
        result = {
            "strategy": {"rules": mapping, "default_card_id": default_card_id or "keep as charged"},
            **_allocation_payload(allocation, wallet),
            "vs_actual": round(allocation.total_reward - actual.total_reward, 2),
        }
        if label:
            result["label"] = label
        tested.append(result)
        return result

    # ---- skills: the deterministic moves the model otherwise re-derives ----

    categories_present = sorted({t.category for t in transactions})

    def best_rate_plan(_: dict) -> str:
        """Each category on the card with the highest headline rate; default = best base rate."""
        mapping = {
            category: max(wallet, key=lambda c: (c.rate_for(category), -wallet.index(c))).id
            for category in categories_present
            if category != "general_spend"
        }
        default = max(wallet, key=lambda c: (c.base_rate, -wallet.index(c))).id
        result = _score_plan(mapping, default, "best headline rate per category (ignores minimums)")
        result["note"] = (
            "Naive plan. Cards whose minimum spend is not met show min_spend_met=false - "
            "pass this plan to fix_minimums to repair it."
        )
        return json.dumps(result)

    def fix_minimums(args: dict) -> str:
        """Repair a plan whose cards miss their minimum spend, one whole-category move at a time."""
        rules = args.get("rules", [])
        if isinstance(rules, str):
            try:
                rules = json.loads(rules)
            except ValueError as exc:
                return json.dumps({"error": f"rules must be a JSON array: {exc}"})
        try:
            mapping = {str(r["category"]): str(r["card_id"]) for r in rules}
        except (KeyError, TypeError) as exc:
            return json.dumps({"error": f"each rule needs category and card_id: {exc}"})
        default = str(args.get("default_card_id") or "") or None
        if any(cid not in wallet_ids for cid in list(mapping.values()) + ([default] if default else [])):
            return json.dumps({"error": f"Unknown card id. Wallet holds {sorted(wallet_ids)}."})

        moves: list[str] = []
        current = score_strategy(transactions, wallet, mapping, default)
        for _ in range(2 * len(wallet)):
            starving = [
                c for c in wallet
                if c.id not in current.bonus_cards
                and c.min_spend > 0
                and (c.id in mapping.values() or c.id == default)
            ]
            if not starving:
                break
            best_move = None
            for card in starving:
                # Candidate moves: point one category (or the default) at this card.
                for category in categories_present:
                    trial_rules, trial_default = dict(mapping), default
                    if category == "general_spend" or mapping.get(category) is None:
                        if category == "general_spend":
                            trial_default = card.id
                        else:
                            trial_rules[category] = card.id
                    else:
                        trial_rules[category] = card.id
                    trial = score_strategy(transactions, wallet, trial_rules, trial_default)
                    gain = trial.total_reward - current.total_reward
                    if best_move is None or gain > best_move[0]:
                        best_move = (gain, category, card, trial_rules, trial_default, trial)
            if best_move is None or best_move[0] <= 1e-9:
                # No single move pays: the honest answer is to drop that card's bonus.
                moves.append(
                    "no whole-category move lifts " + ", ".join(c.name for c in starving)
                    + " past its minimum without losing more elsewhere - leave it at base rate"
                )
                break
            gain, category, card, mapping, default, current = best_move
            moves.append(f"move {category} -> {card.name} (+${gain:.2f})")

        result = _score_plan(mapping, default, "fix_minimums result")
        result["moves"] = moves
        return json.dumps(result)

    def best_plan(args: dict) -> str:
        """Exhaustive optimum among plans with <= max_rules rules plus a default card."""
        try:
            max_rules = max(1, min(int(args.get("max_rules") or MAX_RULES), MAX_RULES))
        except (TypeError, ValueError):
            max_rules = MAX_RULES
        mapping, default, _ = best_rule_plan(transactions, wallet, max_rules)
        result = _score_plan(mapping, default, f"exhaustive best plan with <= {max_rules} rules")
        result["note"] = (
            "Provably the best plan of this shape for this cycle's spend. Beating it "
            "requires more rules or per-transaction routing a person cannot follow."
        )
        return json.dumps(result)

    specs = [
        {
            "type": "function",
            "function": {
                "name": "best_rule_plan",
                "description": (
                    "SKILL. Exhaustively search every plan with at most max_rules category rules "
                    "plus one default card and return the highest-scoring one. Deterministic and exact."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_rules": {"type": "integer", "minimum": 1, "maximum": 3, "description": "Rule budget (default 3)."}
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "best_rate_plan",
                "description": (
                    "SKILL. Build and score the naive plan: every category on the card with the "
                    "highest headline rate. Shows which cards would miss their minimum spend."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fix_minimums",
                "description": (
                    "SKILL. Take a plan and repair cards that miss their minimum spend by moving one "
                    "whole category at a time onto them, accepting only moves that raise total reward. "
                    "Returns the repaired plan, its engine score and the moves made."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rules": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "category": {"type": "string", "enum": CATEGORIES},
                                    "card_id": {"type": "string"},
                                },
                                "required": ["category", "card_id"],
                            },
                        },
                        "default_card_id": {"type": "string"},
                    },
                    "required": ["rules"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_card_rules",
                "description": "Reward rules for every card in the wallet: rates, minimum spend, caps.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_spend_summary",
                "description": "This cycle's spend by category and by card, and what it actually earned.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_transactions",
                "description": "List redacted statement rows (date, merchant, amount, category, card used).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": CATEGORIES + ["all"], "description": "Filter by category."},
                        "min_amount": {"type": "number", "description": "Only rows at or above this amount."},
                        "limit": {"type": "integer", "description": "Maximum rows to return (1-200)."},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "score_allocation",
                "description": (
                    "Score a candidate strategy with the real rules engine. Returns total_reward, "
                    "per-card spend/reward, and whether each card met its minimum or hit its cap."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rules": {
                            "type": "array",
                            "description": "Category -> card rules for the plan.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "category": {"type": "string", "enum": CATEGORIES},
                                    "card_id": {"type": "string"},
                                },
                                "required": ["category", "card_id"],
                            },
                        },
                        "default_card_id": {
                            "type": "string",
                            "description": "Card for categories with no rule. Empty keeps them on the card used.",
                        },
                    },
                    "required": ["rules"],
                },
            },
        },
    ]
    impls = {
        "best_rule_plan": best_plan,
        "best_rate_plan": best_rate_plan,
        "fix_minimums": fix_minimums,
        "get_card_rules": get_card_rules,
        "get_spend_summary": get_spend_summary,
        "list_transactions": list_transactions,
        "score_allocation": score_allocation,
    }
    return specs, impls


SCORING_TOOLS = {"score_allocation", "best_rate_plan", "fix_minimums", "best_rule_plan"}


def precomputed_candidates(
    transactions: Sequence[Transaction], wallet: Sequence[CardProfile]
) -> list[dict]:
    """Engine-scored strategies for the no-tools fallback.

    The engine's per-transaction optimum is distilled into a category -> card
    map, alongside "everything on one card" plans and the statement as charged.
    """
    cards = _cards_for(wallet)
    actual = score_actual(transactions, cards)
    candidates: list[dict] = []

    def add(label: str, mapping: dict[str, str], default_card_id: Optional[str]) -> None:
        allocation = score_strategy(transactions, wallet, mapping, default_card_id)
        candidates.append(
            {
                "label": label,
                "rules": mapping,
                "default_card_id": default_card_id or "keep as charged",
                **_allocation_payload(allocation, wallet),
                "vs_actual": round(allocation.total_reward - actual.total_reward, 2),
            }
        )

    add("as charged this cycle", {}, None)

    optimal = allocate_optimal(transactions, wallet)
    spend_by_cat_card: dict[str, dict[str, float]] = {}
    for txn in transactions:
        card_id = optimal.card_of(txn.id)
        by_card = spend_by_cat_card.setdefault(txn.category, {})
        by_card[card_id] = by_card.get(card_id, 0.0) + txn.amount
    distilled = {cat: max(by_card, key=by_card.get) for cat, by_card in spend_by_cat_card.items()}
    default = distilled.pop("general_spend", None) or max(
        (c.id for c in wallet), key=lambda cid: optimal.ledgers[cid].spend
    )
    add("engine optimum distilled to category rules", distilled, default)

    for size in (1, 2, MAX_RULES):
        mapping, default_id, _ = best_rule_plan(transactions, wallet, size)
        add(f"exhaustive best plan with <= {size} rule{'s' if size > 1 else ''}", mapping, default_id)

    for card in wallet:
        add(f"everything on {card.name}", {}, card.id)

    candidates.sort(key=lambda c: -c["total_reward"])
    return candidates


# --------------------------------------------------------------------------- #
# Running the model
# --------------------------------------------------------------------------- #


def run_advisor(
    transactions: Sequence[Transaction],
    wallet: Sequence[CardProfile],
    dashboard: dict,
    client: Optional[ChatClient] = None,
) -> AdvisorResult:
    """Run the agent and return an engine-verified recommendation.

    `client` can be injected for tests; by default one is built from .env.
    """
    cfg = settings()
    if client is None:
        status = advisor_status()
        if not status["available"]:
            return AdvisorResult(mode="unavailable", detail=status["reason"], model=cfg["model"], provider=cfg["base_url"])
        client = ChatClient(cfg["api_key"], cfg["base_url"], cfg["model"], reasoning_effort=cfg["reasoning_effort"])
    model = getattr(client, "model", cfg["model"])
    provider = getattr(client, "base_url", cfg["base_url"])

    tested: list[dict] = []
    specs, impls = build_tools(transactions, wallet, dashboard, tested)
    candidates = precomputed_candidates(transactions, wallet)
    seed = {
        "card_rules": json.loads(impls["get_card_rules"]({})),
        "spend_summary": json.loads(impls["get_spend_summary"]({})),
        "engine_scored_baselines": candidates,
    }

    answer = None
    path = "tool-loop"
    tool_loop_error = ""
    trace: list[dict] = []
    try:
        answer = _run_tool_loop(client, specs, impls, seed=seed, trace=trace)
        tested.extend(candidates)
    except Exception as exc:  # noqa: BLE001 - fall through to the single-shot path
        tool_loop_error = f"{type(exc).__name__}: {exc}"

    if answer is None:
        # The provider may not support function calling. Fall back to one
        # request with engine-precomputed candidates and no tools.
        path = "single-shot"
        try:
            answer = _run_single_shot(client, transactions, wallet, dashboard, candidates)
            tested.extend(candidates)
        except Exception as exc:  # noqa: BLE001 - keep the dashboard usable
            detail = f"{type(exc).__name__}: {exc}"
            if tool_loop_error:
                detail = f"tool loop: {tool_loop_error}; single-shot: {detail}"
            return AdvisorResult(mode="error", detail=detail, model=model, provider=provider, path=path)

    if answer is None:
        return AdvisorResult(
            mode="error",
            detail="The model did not return a strategy in the expected format.",
            model=model,
            provider=provider,
            path=path,
            strategies_tested=len(tested),
        )

    result = verify(answer, transactions, wallet, len(tested))
    result.model, result.provider, result.path, result.trace = model, provider, path, trace
    # Transparency: the engine's exhaustive optimum is a known baseline. If the
    # model recommends something weaker, say so instead of hiding it.
    best_baseline = max(candidates, key=lambda c: c["total_reward"])
    result.best_engine_plan = {
        "label": best_baseline["label"],
        "total_reward": best_baseline["total_reward"],
        "rules": best_baseline["rules"],
        "default_card_id": best_baseline["default_card_id"],
    }
    if result.verified_rewards + 0.005 < best_baseline["total_reward"]:
        result.detail = (
            f"The engine's {best_baseline['label']} scores ${best_baseline['total_reward']:.2f}; "
            f"the strategist's plan scores ${result.verified_rewards:.2f}. "
            "Read its reasoning for why it chose the simpler plan - or take the engine's."
        ).strip()
    if tool_loop_error:
        result.detail = f"Function calling unavailable through this provider ({tool_loop_error}); used single-shot mode."
    return result


def _run_tool_loop(
    client: ChatClient,
    specs: list[dict],
    impls: dict[str, Callable[[dict], str]],
    seed: Optional[dict] = None,
    trace: Optional[list[dict]] = None,
) -> Optional[dict]:
    """Function-calling loop: call tools until the model answers in JSON.

    `seed` (card rules, spend summary, engine-scored baselines) is placed in
    the first user turn so the model starts from ground truth instead of
    spending round trips discovering it. `trace` receives one entry per model
    turn - reasoning, visible text, and each tool call with its result - so
    the decision path can be inspected afterwards.
    """
    trace = trace if trace is not None else []
    brief = (
        "Design next cycle's wallet strategy for this statement. The card rules, "
        "spend summary and engine-scored baseline strategies are below. Use "
        "score_allocation to test the variants you think can beat the best baseline "
        "(all in one turn), then return your final JSON answer."
    )
    if seed is not None:
        brief += "\n\nContext (JSON):\n" + json.dumps(seed)
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": brief},
    ]
    exploration_rounds = 0
    for _ in range(MAX_TOOL_TURNS):
        # After the allowed scoring rounds, withhold the tools: the next turn
        # must be the answer. Measured on deepseek-v4.1-flash, a second round
        # of score_allocation calls cost ~35% of the run and never changed the
        # final plan - the model over-explores, so the loop draws the line.
        explore = exploration_rounds < MAX_EXPLORATION_ROUNDS
        if not explore:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You have the engine's scores for every strategy you tested above. "
                        "Return your final JSON answer now - no further tool calls."
                    ),
                }
            )
        response = client.chat(messages, tools=specs if explore else None)
        message = _first_message(response)
        tool_calls = message.get("tool_calls") or []
        entry = _trace_entry(len(trace) + 1, response, message, tools_offered=explore)
        trace.append(entry)
        if not tool_calls:
            return parse_answer(message.get("content"))
        if any(call.get("function", {}).get("name") in SCORING_TOOLS for call in tool_calls):
            exploration_rounds += 1

        messages.append(
            {"role": "assistant", "content": message.get("content") or None, "tool_calls": tool_calls}
        )
        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            impl = impls.get(name)
            output = impl(args) if impl else json.dumps({"error": f"unknown tool {name}"})
            messages.append({"role": "tool", "tool_call_id": call.get("id", name), "content": output})
            entry["tool_calls"].append({"name": name, "args": args, "result": _summarise_tool_output(output)})

    # Out of turns: ask for the answer without tools.
    messages.append({"role": "user", "content": "Stop testing and return your final JSON answer now."})
    response = client.chat(messages)
    message = _first_message(response)
    trace.append(_trace_entry(len(trace) + 1, response, message, tools_offered=False))
    return parse_answer(message.get("content"))


TRACE_REASONING_CHARS = 4000


def _trace_entry(turn: int, response: dict, message: dict, tools_offered: bool) -> dict:
    usage = response.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    return {
        "turn": turn,
        "tools_offered": tools_offered,
        "reasoning": reasoning[:TRACE_REASONING_CHARS],
        "reasoning_truncated": len(reasoning) > TRACE_REASONING_CHARS,
        "text": (message.get("content") or "")[:2000],
        "tokens": {
            "prompt": usage.get("prompt_tokens"),
            "cached": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
            "completion": usage.get("completion_tokens"),
            "reasoning": details.get("reasoning_tokens"),
        },
        "tool_calls": [],
    }


def _summarise_tool_output(output: str) -> dict:
    """Keep the parts of a tool result worth showing in the reasoning tree."""
    try:
        data = json.loads(output)
    except ValueError:
        return {"raw": output[:300]}
    if not isinstance(data, dict):
        return {"raw": output[:300]}
    if "error" in data:
        return {"error": data["error"]}
    summary: dict = {}
    for key in ("label", "total_reward", "vs_actual", "moves", "note", "row_count", "engine_per_transaction_optimum"):
        if key in data:
            summary[key] = data[key]
    if "strategy" in data:
        summary["strategy"] = data["strategy"]
    if "cards" in data and isinstance(data["cards"], list) and data["cards"] and "min_spend_met" in data["cards"][0]:
        summary["cards"] = {
            c["card_id"]: ("bonus" if c["min_spend_met"] else "BASE ONLY") + (" +cap hit" if c.get("bonus_cap_reached") else "")
            for c in data["cards"]
        }
    return summary or {"keys": sorted(data)}


def _run_single_shot(client: ChatClient, transactions, wallet, dashboard, candidates: list[dict]) -> Optional[dict]:
    """One plain request, no tools: the model reasons over engine-scored candidates."""
    cards = _cards_for(wallet)
    context = {
        "cycle": dashboard["source"].get("cycle_label", "one statement cycle"),
        "summary": dashboard["summary"],
        "by_category": dashboard["categories"],
        "card_rules": _cards_payload(wallet),
        "global_assumptions": catalog.global_assumptions(),
        "engine_scored_candidates": candidates,
        "transactions": _rows_payload(transactions, cards),
    }
    messages = [
        {"role": "system", "content": SINGLE_SHOT_PROMPT},
        {"role": "user", "content": "Statement context (JSON):\n" + json.dumps(context) + "\n\nReturn the JSON answer now."},
    ]
    try:
        response = client.chat(messages, response_format={"type": "json_object"})
    except ProviderError as exc:
        if exc.status_code not in (400, 422):
            raise
        response = client.chat(messages)  # provider does not support response_format
    return parse_answer(_first_message(response).get("content"))


def _first_message(response: dict) -> dict:
    try:
        return response["choices"][0]["message"] or {}
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"unexpected response shape: {str(response)[:200]}") from exc


def parse_answer(content) -> Optional[dict]:
    """Pull the JSON object out of the model's final text, fenced or bare."""
    if content is None:
        return None
    if isinstance(content, list):  # some providers return content parts
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    text = str(content).strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :] if "{" in text else ""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def verify(
    answer: dict,
    transactions: Sequence[Transaction],
    wallet: Sequence[CardProfile],
    strategies_tested: int = 0,
) -> AdvisorResult:
    """Re-score the model's recommendation with the engine before showing it."""
    cards = _cards_for(wallet)
    labels = catalog.category_labels()
    wallet_ids = {c.id for c in wallet}
    rules: list[AdvisorRule] = []
    mapping: dict[str, str] = {}
    for raw in (answer.get("recommended_rules") or [])[:MAX_RULES]:
        if not isinstance(raw, dict):
            continue
        card_id = str(raw.get("card_id", ""))
        category = str(raw.get("category", ""))
        if card_id not in wallet_ids or category in mapping or category not in CATEGORIES:
            continue
        mapping[category] = card_id
        rules.append(
            AdvisorRule(
                category=category,
                category_label=labels.get(category, category),
                card_id=card_id,
                card_name=cards[card_id].name,
                rationale=str(raw.get("rationale", "")).strip(),
            )
        )

    default_card_id = str(answer.get("default_card_id") or "")
    if default_card_id not in wallet_ids:
        default_card_id = ""

    allocation = score_strategy(transactions, wallet, mapping, default_card_id or None)
    claimed = answer.get("projected_rewards")
    watch_outs = answer.get("watch_outs") or []
    return AdvisorResult(
        mode="agent",
        headline=str(answer.get("headline", "")).strip(),
        rules=rules,
        default_card_id=default_card_id,
        default_card_name=cards[default_card_id].name if default_card_id else "Keep as charged",
        verified_rewards=allocation.total_reward,
        model_claimed_rewards=round(float(claimed), 2) if isinstance(claimed, (int, float)) else None,
        reasoning_summary=str(answer.get("reasoning_summary", "")).strip(),
        watch_outs=[str(w).strip() for w in watch_outs if str(w).strip()][:3],
        strategies_tested=strategies_tested,
    )


# --------------------------------------------------------------------------- #
# Conversation: the cardholder talks back
# --------------------------------------------------------------------------- #

CHAT_INSTRUCTIONS = """(You are now in conversation with the cardholder about the plan above.
Reply in plain, friendly prose - a few short sentences, no bullet dumps, no JSON
unless the recommendation changes. Take feedback seriously: if they say a card is
not an option, a category is wrong, a rule is too fiddly, or they want something
simpler, re-plan - score any new plan with the skills before quoting a number.
If your recommendation changes, finish your reply with a JSON object in the
final-answer schema so the dashboard can update; otherwise no JSON at all.
Never invent figures.)"""

MAX_CHAT_HISTORY = 12


def build_seed(transactions, wallet, dashboard, impls, candidates) -> dict:
    """Ground-truth context placed in the first user turn (also reused by chat)."""
    return {
        "card_rules": json.loads(impls["get_card_rules"]({})),
        "spend_summary": json.loads(impls["get_spend_summary"]({})),
        "engine_scored_baselines": candidates,
    }


def _prior_plan_message(prior: Optional[dict]) -> str:
    if not prior or prior.get("mode") != "agent":
        return "I have not produced a recommendation for this statement yet."
    plan = {
        "headline": prior.get("headline", ""),
        "recommended_rules": [
            {"category": r["category"], "card_id": r["card_id"], "rationale": r.get("rationale", "")}
            for r in prior.get("rules", [])
        ],
        "default_card_id": prior.get("default_card_id", ""),
        "projected_rewards": prior.get("verified_rewards"),
        "reasoning_summary": prior.get("reasoning_summary", ""),
        "watch_outs": prior.get("watch_outs", []),
    }
    return "My current recommendation (engine-verified):\n" + json.dumps(plan)


def chat_with_strategist(
    transactions: Sequence[Transaction],
    wallet: Sequence[CardProfile],
    dashboard: dict,
    prior_plan: Optional[dict],
    history: Sequence[dict],
    user_message: str,
    client: Optional[ChatClient] = None,
) -> dict:
    """One conversational turn with the strategist.

    Returns {"mode", "reply", "plan", "trace", "detail"}. `plan` is an
    engine-verified AdvisorResult dict when the model revised its
    recommendation, else None. `history` is [{"role": "user"|"assistant",
    "text": ...}] from earlier turns; the caller persists it.
    """
    cfg = settings()
    if client is None:
        status = advisor_status()
        if not status["available"]:
            return {"mode": "unavailable", "reply": status["reason"], "plan": None, "trace": [], "detail": ""}
        client = ChatClient(cfg["api_key"], cfg["base_url"], cfg["model"], reasoning_effort=cfg["reasoning_effort"])

    tested: list[dict] = []
    specs, impls = build_tools(transactions, wallet, dashboard, tested)
    candidates = precomputed_candidates(transactions, wallet)
    seed = build_seed(transactions, wallet, dashboard, impls, candidates)

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Statement context (JSON) - card rules, spend summary and engine-scored baselines:\n"
            + json.dumps(seed),
        },
        {"role": "assistant", "content": _prior_plan_message(prior_plan)},
    ]
    for turn in list(history)[-MAX_CHAT_HISTORY:]:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": str(turn.get("text", ""))[:4000]})
    messages.append({"role": "user", "content": f"{user_message.strip()}\n\n{CHAT_INSTRUCTIONS}"})

    trace: list[dict] = []
    try:
        text = _converse(client, messages, specs, impls, trace)
    except Exception as exc:  # noqa: BLE001 - keep the UI usable
        return {"mode": "error", "reply": "", "plan": None, "trace": trace, "detail": f"{type(exc).__name__}: {exc}"}

    plan_json = parse_answer(text)
    plan = None
    reply = text.strip()
    if isinstance(plan_json, dict) and plan_json.get("recommended_rules"):
        verified = verify(plan_json, transactions, wallet, len(tested))
        verified.model, verified.provider, verified.path, verified.trace = (
            getattr(client, "model", cfg["model"]),
            getattr(client, "base_url", cfg["base_url"]),
            "chat",
            trace,
        )
        best = max(candidates, key=lambda c: c["total_reward"])
        verified.best_engine_plan = {
            "label": best["label"],
            "total_reward": best["total_reward"],
            "rules": best["rules"],
            "default_card_id": best["default_card_id"],
        }
        plan = verified.to_dict()
        reply = _prose_before_json(text) or verified.headline

    return {"mode": "agent", "reply": reply, "plan": plan, "trace": trace, "detail": ""}


def _converse(client: ChatClient, messages: list[dict], specs, impls, trace: list[dict]) -> str:
    """Tool loop for a chat turn: one scoring round, then the reply."""
    exploration_rounds = 0
    for _ in range(MAX_TOOL_TURNS):
        explore = exploration_rounds < MAX_EXPLORATION_ROUNDS
        if not explore:
            messages.append({"role": "user", "content": "Reply to the cardholder now - no further tool calls."})
        response = client.chat(messages, tools=specs if explore else None)
        message = _first_message(response)
        tool_calls = message.get("tool_calls") or []
        entry = _trace_entry(len(trace) + 1, response, message, tools_offered=explore)
        trace.append(entry)
        if not tool_calls:
            return message.get("content") or ""
        if any(call.get("function", {}).get("name") in SCORING_TOOLS for call in tool_calls):
            exploration_rounds += 1
        messages.append({"role": "assistant", "content": message.get("content") or None, "tool_calls": tool_calls})
        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            impl = impls.get(name)
            output = impl(args) if impl else json.dumps({"error": f"unknown tool {name}"})
            messages.append({"role": "tool", "tool_call_id": call.get("id", name), "content": output})
            entry["tool_calls"].append({"name": name, "args": args, "result": _summarise_tool_output(output)})
    return ""


def _prose_before_json(text: str) -> str:
    """The human-readable part of a reply that ends in a JSON block."""
    cut = text.find("```")
    if cut == -1:
        cut = text.find("{")
    prose = text[:cut] if cut > 0 else ""
    return prose.strip()
