"""Local rewards rules engine: actual vs optimal cashback for one cycle.

Pure functions over Transaction + CardProfile - no I/O, no network.

  score_actual()      what the statement actually earned, on the cards used
  allocate_optimal()  the same spend re-allocated across the wallet
  score_strategy()    score a human-readable "category -> card" plan

How the optimal figure is found: for every combination of cards the user could
push to their minimum spend, assign each transaction greedily to the card that
pays most (largest reward opportunity first, so it gets the cap headroom), then
top the combination's cards up to their minimum spend with the lines that cost
least to move. The best-scoring feasible combination wins. Both allocations are
finally scored the same way - chronologically, because caps genuinely fill up in
time order.

Modelling rules (surfaced in the UI as assumptions):
  * a card's bonus cashback is capped at monthly_cap per cycle;
  * spend beyond that cap earns the card's base rate;
  * base-rate cashback does not count against the cap;
  * if a card's total cycle spend is below min_spend, all of its categories
    fall back to the base rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable, Optional, Sequence

from .models import CardProfile, Transaction

EPSILON = 1e-9
# Above this wallet size, stop enumerating every combination of cards and walk a
# single deactivation chain instead. Real wallets in v1 hold 2-5 cards.
MAX_ENUMERATED_WALLET = 10


@dataclass(frozen=True)
class RewardCalc:
    """Reward for one transaction on one card, given that card's cycle state."""

    reward: float
    rate_applied: float  # the headline rate before any cap split
    bonus_portion: float  # the part that counts against monthly_cap
    capped: bool  # True if the cap truncated this transaction


@dataclass
class CardLedger:
    spend: float = 0.0
    reward: float = 0.0
    bonus_earned: float = 0.0
    used_bonus: bool = False
    transaction_count: int = 0


@dataclass
class Allocation:
    """A set of transactions scored against a wallet."""

    total_reward: float = 0.0
    by_transaction: dict[str, tuple[str, float]] = field(default_factory=dict)
    ledgers: dict[str, CardLedger] = field(default_factory=dict)
    bonus_cards: set[str] = field(default_factory=set)  # cards earning bonus rates
    inactive_cards: set[str] = field(default_factory=set)  # bonus withheld: min spend not met
    capped_cards: set[str] = field(default_factory=set)

    def card_of(self, txn_id: str) -> str:
        return self.by_transaction[txn_id][0]

    def reward_of(self, txn_id: str) -> float:
        return self.by_transaction[txn_id][1]


def compute_reward(
    card: CardProfile,
    category: str,
    amount: float,
    bonus_earned: float = 0.0,
    bonus_active: bool = True,
) -> RewardCalc:
    """Reward for spending `amount` in `category` on `card`.

    `bonus_earned` is the bonus cashback the card has already booked this cycle.
    """
    rate = card.rate_for(category) if bonus_active else card.base_rate
    is_bonus = rate > card.base_rate + EPSILON

    if not is_bonus or card.monthly_cap is None:
        return RewardCalc(amount * rate, rate, 0.0, False)

    remaining = max(0.0, card.monthly_cap - bonus_earned)
    gross = amount * rate
    if gross <= remaining + EPSILON:
        return RewardCalc(gross, rate, gross, False)

    # The cap truncates this line: bonus rate up to the cap, base rate on the rest.
    spend_within_cap = remaining / rate if rate > 0 else 0.0
    spend_beyond_cap = max(0.0, amount - spend_within_cap)
    reward = remaining + spend_beyond_cap * card.base_rate
    return RewardCalc(reward, rate, remaining, True)


def spend_by_card(transactions: Iterable[Transaction]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for txn in transactions:
        totals[txn.card_id] = totals.get(txn.card_id, 0.0) + txn.amount
    return totals


def _rate(card: CardProfile, category: str, bonus_active: bool) -> float:
    return card.rate_for(category) if bonus_active else card.base_rate


def score_assignment(
    transactions: Sequence[Transaction],
    assignment: dict[str, str],
    wallet: Sequence[CardProfile],
    bonus_cards: set[str],
) -> Allocation:
    """Score a txn_id -> card_id assignment chronologically, applying caps."""
    cards = {card.id: card for card in wallet}
    allocation = Allocation(
        bonus_cards=set(bonus_cards),
        inactive_cards={card.id for card in wallet if card.id not in bonus_cards},
    )
    for card in wallet:
        allocation.ledgers.setdefault(card.id, CardLedger())

    for txn in sorted(transactions, key=lambda t: (t.date, t.id)):
        card = cards[assignment[txn.id]]
        ledger = allocation.ledgers.setdefault(card.id, CardLedger())
        calc = compute_reward(
            card, txn.category, txn.amount, ledger.bonus_earned, card.id in bonus_cards
        )
        ledger.spend += txn.amount
        ledger.reward += calc.reward
        ledger.bonus_earned += calc.bonus_portion
        ledger.transaction_count += 1
        if calc.bonus_portion > EPSILON:
            ledger.used_bonus = True
        if calc.capped:
            allocation.capped_cards.add(card.id)
        allocation.total_reward += calc.reward
        allocation.by_transaction[txn.id] = (card.id, calc.reward)
    return allocation


def score_actual(
    transactions: Sequence[Transaction], cards: dict[str, CardProfile]
) -> Allocation:
    """What the statement actually earned, on the cards the spend went on."""
    totals = spend_by_card(transactions)
    wallet = [cards[card_id] for card_id in totals]
    bonus_cards = {
        card.id
        for card in wallet
        if card.min_spend == 0 or totals[card.id] + EPSILON >= card.min_spend
    }
    assignment = {txn.id: txn.card_id for txn in transactions}
    return score_assignment(transactions, assignment, wallet, bonus_cards)


def _greedy_assign(
    transactions: Sequence[Transaction],
    wallet: Sequence[CardProfile],
    bonus_cards: set[str],
) -> dict[str, str]:
    """Biggest reward opportunities first, so they claim the cap headroom."""
    bonus_earned = {card.id: 0.0 for card in wallet}

    def opportunity(txn: Transaction) -> float:
        return max(
            txn.amount * _rate(card, txn.category, card.id in bonus_cards) for card in wallet
        )

    assignment: dict[str, str] = {}
    for txn in sorted(transactions, key=lambda t: (-opportunity(t), t.date, t.id)):
        best_card: Optional[CardProfile] = None
        best_calc: Optional[RewardCalc] = None
        for card in wallet:  # declaration order breaks ties deterministically
            calc = compute_reward(
                card, txn.category, txn.amount, bonus_earned[card.id], card.id in bonus_cards
            )
            if best_calc is None or calc.reward > best_calc.reward + EPSILON:
                best_card, best_calc = card, calc
        assert best_card is not None and best_calc is not None
        assignment[txn.id] = best_card.id
        bonus_earned[best_card.id] += best_calc.bonus_portion
    return assignment


def _top_up_minimums(
    transactions: Sequence[Transaction],
    wallet: Sequence[CardProfile],
    bonus_cards: set[str],
    assignment: dict[str, str],
) -> Optional[dict[str, str]]:
    """Move the cheapest lines onto cards short of their minimum spend.

    Returns None if a card in `bonus_cards` cannot reach its minimum.
    """
    cards = {card.id: card for card in wallet}
    spend: dict[str, float] = {card.id: 0.0 for card in wallet}
    for txn in transactions:
        spend[assignment[txn.id]] += txn.amount

    for _ in range(2 * len(wallet) + 2):
        needy = [
            cards[cid]
            for cid in bonus_cards
            if cards[cid].min_spend > 0 and spend[cid] + EPSILON < cards[cid].min_spend
        ]
        if not needy:
            return assignment
        # Fill the card closest to its minimum first - it is the cheapest to satisfy.
        target = min(needy, key=lambda c: (c.min_spend - spend[c.id], c.id))
        shortfall = target.min_spend - spend[target.id]

        candidates = []
        for txn in transactions:
            donor_id = assignment[txn.id]
            if donor_id == target.id:
                continue
            donor = cards[donor_id]
            loss = (
                _rate(donor, txn.category, donor_id in bonus_cards)
                - _rate(target, txn.category, True)
            ) * txn.amount
            # Prefer cheap moves, and among equals the ones that close the gap fastest.
            candidates.append((round(loss, 6), -txn.amount, txn.id))
        candidates.sort()

        for _loss, neg_amount, txn_id in candidates:
            if shortfall <= EPSILON:
                break
            donor_id = assignment[txn_id]
            donor = cards[donor_id]
            amount = -neg_amount
            if (
                donor_id in bonus_cards
                and donor.min_spend > 0
                and spend[donor_id] - amount + EPSILON < donor.min_spend
            ):
                continue  # would break the donor's own minimum spend
            assignment[txn_id] = target.id
            spend[donor_id] -= amount
            spend[target.id] += amount
            shortfall -= amount

        if shortfall > EPSILON:
            return None  # this card's minimum is out of reach for this cycle
    return None


def _candidate_bonus_sets(wallet: Sequence[CardProfile]) -> list[set[str]]:
    """Which cards to try pushing to their minimum spend."""
    ids = [card.id for card in wallet]
    if len(ids) <= MAX_ENUMERATED_WALLET:
        return [
            set(subset)
            for size in range(len(ids), -1, -1)
            for subset in combinations(ids, size)
        ]
    # Very large wallet: walk one chain, dropping the priciest minimum each step.
    ordered = sorted(wallet, key=lambda c: (-c.min_spend, c.id))
    chain, current = [], set(ids)
    chain.append(set(current))
    for card in ordered:
        current = current - {card.id}
        chain.append(set(current))
    return chain


def allocate_optimal(
    transactions: Sequence[Transaction], wallet: Sequence[CardProfile]
) -> Allocation:
    """Best feasible re-allocation of this cycle's spend across the wallet."""
    if not wallet:
        raise ValueError("wallet must contain at least one card")

    best: Optional[Allocation] = None
    best_key: Optional[tuple] = None
    for bonus_cards in _candidate_bonus_sets(wallet):
        assignment = _greedy_assign(transactions, wallet, bonus_cards)
        assignment = _top_up_minimums(transactions, wallet, bonus_cards, assignment)
        if assignment is None:
            continue
        allocation = score_assignment(transactions, assignment, wallet, bonus_cards)
        # Highest reward wins; ties prefer the simpler wallet, then card order.
        key = (-round(allocation.total_reward, 6), len(bonus_cards), tuple(sorted(bonus_cards)))
        if best_key is None or key < best_key:
            best, best_key = allocation, key

    assert best is not None  # the empty bonus set is always feasible
    return best


def eligible_bonus_cards(
    wallet: Sequence[CardProfile], spend: dict[str, float]
) -> set[str]:
    """Cards whose assigned spend clears their minimum."""
    return {
        card.id
        for card in wallet
        if card.min_spend == 0 or spend.get(card.id, 0.0) + EPSILON >= card.min_spend
    }


def score_strategy(
    transactions: Sequence[Transaction],
    wallet: Sequence[CardProfile],
    category_to_card: dict[str, str],
    default_card_id: Optional[str] = None,
) -> Allocation:
    """Score a plain-English strategy: "put this category on that card".

    Categories with no rule go to `default_card_id`, or stay on the card the
    statement actually used. Minimum spends are then judged on the resulting
    totals, so an unreachable minimum shows up as lost bonus rather than being
    silently assumed away.
    """
    wallet_ids = {card.id for card in wallet}
    assignment: dict[str, str] = {}
    for txn in transactions:
        card_id = category_to_card.get(txn.category) or default_card_id or txn.card_id
        if card_id not in wallet_ids:
            card_id = txn.card_id if txn.card_id in wallet_ids else next(iter(wallet_ids))
        assignment[txn.id] = card_id

    spend: dict[str, float] = {card.id: 0.0 for card in wallet}
    for txn in transactions:
        spend[assignment[txn.id]] += txn.amount
    return score_assignment(transactions, assignment, wallet, eligible_bonus_cards(wallet, spend))
