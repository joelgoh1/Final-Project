"""Reward math: caps, minimum spends, and the known-answer fixture."""

import pytest

from app import catalog
from app.analysis import analyze
from app.models import CardProfile, Transaction
from app.rules_engine import (
    allocate_optimal,
    compute_reward,
    score_actual,
    score_strategy,
)


def _txn(i, category, amount, card_id, date="2026-08-01"):
    return Transaction(id=f"t{i:04d}", date=date, merchant=f"M{i}", amount=amount, card_id=card_id, category=category)


def test_bonus_rate_applies_below_cap():
    card = catalog.card("ocbc_365")
    calc = compute_reward(card, "dining", 100.0)
    assert calc.reward == pytest.approx(6.0)
    assert calc.bonus_portion == pytest.approx(6.0)
    assert not calc.capped


def test_cap_splits_a_line_into_bonus_and_base():
    card = catalog.card("ocbc_365")  # 6% dining, $80 cap, 0.3% base
    calc = compute_reward(card, "dining", 100.0, bonus_earned=78.0)
    # $2 of cap left = $33.33 of spend at 6%; the remaining $66.67 earns 0.3%.
    assert calc.reward == pytest.approx(2.0 + 66.6667 * 0.003, abs=1e-3)
    assert calc.bonus_portion == pytest.approx(2.0)
    assert calc.capped


def test_inactive_bonus_falls_back_to_base_rate():
    card = catalog.card("uob_one")
    calc = compute_reward(card, "groceries", 100.0, bonus_active=False)
    assert calc.reward == pytest.approx(100.0 * card.base_rate)
    assert calc.bonus_portion == 0.0


def test_uncapped_flat_card_never_caps():
    card = catalog.card("flat_cashback_card")
    calc = compute_reward(card, "dining", 10_000.0, bonus_earned=999.0)
    assert calc.reward == pytest.approx(150.0)
    assert not calc.capped


def test_min_spend_missed_downgrades_the_whole_card():
    cards = catalog.cards()
    # OCBC 365 needs $800; $200 of dining earns base rate only.
    txns = [_txn(1, "dining", 200.0, "ocbc_365")]
    assert score_actual(txns, cards).total_reward == pytest.approx(200.0 * 0.003)
    # Same dining with the minimum cleared earns the 6% rate.
    txns = [_txn(1, "dining", 200.0, "ocbc_365"), _txn(2, "general_spend", 600.0, "ocbc_365")]
    actual = score_actual(txns, cards)
    assert actual.total_reward == pytest.approx(200.0 * 0.06 + 600.0 * 0.003)
    assert "ocbc_365" in actual.bonus_cards


def test_optimal_never_worse_than_actual_on_fixtures():
    for fx in catalog.fixtures():
        raw = catalog.fixture(fx["id"])
        result = analyze(raw["transactions"], raw["wallet"])
        s = result.payload["summary"]
        assert s["optimal_rewards"] >= s["actual_rewards"]
        assert s["missed_value"] == pytest.approx(round(s["optimal_rewards"] - s["actual_rewards"], 2))


def test_known_answer_multi_card_fixture():
    """Pin the demo numbers so a rules change is a conscious decision."""
    raw = catalog.fixture("sg_multi_card_cycle")
    s = analyze(raw["transactions"], raw["wallet"]).payload["summary"]
    assert s["total_spend"] == pytest.approx(2822.65)
    assert s["actual_rewards"] == pytest.approx(58.75)
    # 130.99 comes from the exhaustive 3-rule search, which beats the greedy
    # per-transaction pass (125.19) on this fixture - see allocate_optimal.
    assert s["optimal_rewards"] == pytest.approx(130.99)
    assert s["missed_value"] == pytest.approx(72.24)


def test_single_card_wallet_is_already_optimal():
    cards = catalog.cards()
    txns = [_txn(1, "dining", 300.0, "flat_cashback_card"), _txn(2, "shopping", 50.0, "flat_cashback_card")]
    optimal = allocate_optimal(txns, [cards["flat_cashback_card"]])
    assert optimal.total_reward == pytest.approx(score_actual(txns, cards).total_reward)


def test_optimal_tops_up_a_card_to_its_minimum_when_worthwhile():
    """$446 of shopping alone cannot unlock DBS 5%; the engine should move
    cheap general spend onto DBS so the 5% shopping tier is earned."""
    cards = catalog.cards()
    wallet = [cards["dbs_live_fresh"], cards["flat_cashback_card"]]
    txns = [_txn(1, "shopping", 446.0, "flat_cashback_card"), _txn(2, "general_spend", 400.0, "flat_cashback_card")]
    optimal = allocate_optimal(txns, wallet)
    assert "dbs_live_fresh" in optimal.bonus_cards
    assert optimal.ledgers["dbs_live_fresh"].spend >= 600.0
    assert optimal.total_reward > 846.0 * 0.015


def test_score_strategy_is_deterministic_and_honours_minimums():
    raw = catalog.fixture("sg_multi_card_cycle")
    result = analyze(raw["transactions"], raw["wallet"])
    plan = {"dining": "ocbc_365", "groceries": "uob_one", "utilities": "uob_one"}
    a = score_strategy(result.transactions, result.wallet, plan, "dbs_live_fresh")
    b = score_strategy(result.transactions, result.wallet, plan, "dbs_live_fresh")
    assert a.total_reward == pytest.approx(b.total_reward)
    # OCBC only receives ~$600 of dining here, below its $800 minimum.
    assert "ocbc_365" not in a.bonus_cards


def test_best_rule_plan_is_exact_and_known():
    """Exhaustive search over <= N rules + default; pins the demo numbers."""
    from app.rules_engine import best_rule_plan

    raw = catalog.fixture("sg_multi_card_cycle")
    result = analyze(raw["transactions"], raw["wallet"])
    one, one_default, a1 = best_rule_plan(result.transactions, result.wallet, 1)
    two, _, a2 = best_rule_plan(result.transactions, result.wallet, 2)
    three, three_default, a3 = best_rule_plan(result.transactions, result.wallet, 3)
    assert a1.total_reward <= a2.total_reward <= a3.total_reward  # more rules never hurt
    assert one == {"groceries": "uob_one"} and one_default == "ocbc_365"
    assert a3.total_reward == pytest.approx(130.99, abs=0.01)
    assert three == {"general_spend": "dbs_live_fresh", "groceries": "uob_one", "shopping": "dbs_live_fresh"}
    assert three_default == "ocbc_365"
    # No rule ever points at the default card (it would be redundant).
    assert three_default not in three.values()
    # The per-transaction optimum is an upper bound on any rule plan.
    assert a3.total_reward <= result.payload["summary"]["optimal_rewards"] + 1e-6
    # Deterministic.
    assert best_rule_plan(result.transactions, result.wallet, 3)[0] == three


def test_best_rule_plan_prefers_fewer_rules_on_ties():
    from app.rules_engine import best_rule_plan

    cards = catalog.cards()
    wallet = [cards["flat_cashback_card"], cards["dbs_live_fresh"]]
    # DBS cannot reach its $600 minimum, so every plan scores the same as "all on flat".
    txns = [_txn(1, "dining", 100.0, "flat_cashback_card"), _txn(2, "shopping", 50.0, "flat_cashback_card")]
    mapping, default, allocation = best_rule_plan(txns, wallet, 3)
    assert mapping == {} and default == "flat_cashback_card"
    assert allocation.total_reward == pytest.approx(150.0 * 0.015)


def test_empty_wallet_is_rejected():
    with pytest.raises(ValueError):
        allocate_optimal([], [])


def test_card_profile_helpers():
    card = CardProfile("x", "X", "Any", 0.01, {"dining": 0.05})
    assert card.rate_for("dining") == 0.05
    assert card.rate_for("shopping") == 0.01
    assert card.is_bonus_category("dining") and not card.is_bonus_category("shopping")
