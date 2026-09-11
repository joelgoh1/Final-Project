"""Configurable demo generator: data integrity, determinism, knobs, HTTP surface."""

import pytest
from fastapi.testclient import TestClient

from app import demo
from app.analysis import analyze
from app.categorize import categorize
from app.main import app
from app.session_store import store


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    store.clear()
    return TestClient(app)


def test_every_pool_merchant_categorizes_as_declared():
    pool = demo.merchant_pool()
    for category, merchants in pool.items():
        expected = "general_spend" if category == demo.UNMAPPED_KEY else category
        for merchant in merchants:
            assert categorize(merchant["name"])[0] == expected, merchant["name"]
            low, high = merchant["range"]
            assert 0 < low < high


def test_presets_resolve_and_generate():
    for preset in demo.presets():
        config = demo.resolve_config(preset["id"])
        statement = demo.generate(config)
        assert len(statement.rows) == config.transaction_count
        assert sum(r["amount"] for r in statement.rows) == pytest.approx(config.total_spend, abs=0.05)
        assert {r["card_id"] for r in statement.rows} <= set(config.wallet)
        assert all(r["date"].startswith(config.month) for r in statement.rows)
        assert statement.meta["kind"] == "demo"


def test_same_seed_same_statement_different_seed_differs():
    a = demo.generate(demo.resolve_config("household_3_card"))
    b = demo.generate(demo.resolve_config("household_3_card"))
    c = demo.generate(demo.resolve_config("household_3_card", {"seed": 99}))
    assert a.rows == b.rows
    assert a.rows != c.rows


def test_mix_and_unmapped_share_drive_spend():
    config = demo.resolve_config(
        None,
        {"mix": {"groceries": 3, "dining": 1}, "unmapped_pct": 20, "total_spend": 1000, "transaction_count": 40},
    )
    statement = demo.generate(config)
    spend = statement.category_spend
    assert spend["general_spend"] == pytest.approx(200, abs=0.05)
    assert spend["groceries"] == pytest.approx(600, abs=0.05)
    assert spend["dining"] == pytest.approx(200, abs=0.05)
    assert "shopping" not in spend
    result = analyze(statement.rows, config.wallet, "demo", statement.meta)
    assert result.payload["parse_quality"]["general_spend_rows"] > 0


def test_card_modes():
    primary = demo.generate(demo.resolve_config(None, {"card_mode": "primary", "primary_card": "ocbc_365"}))
    assert {r["card_id"] for r in primary.rows} == {"ocbc_365"}

    habit = demo.generate(demo.resolve_config(None, {"card_mode": "habit", "primary_card": "uob_one", "transaction_count": 80}))
    on_primary = sum(r["card_id"] == "uob_one" for r in habit.rows)
    assert 0.5 * 80 < on_primary < 80

    best = demo.generate(
        demo.resolve_config(None, {"card_mode": "best_rate", "mix": {"groceries": 1}, "unmapped_pct": 0})
    )
    assert {r["card_id"] for r in best.rows} == {"uob_one"}  # 10% groceries beats everything

    spread = demo.generate(demo.resolve_config(None, {"card_mode": "spread", "transaction_count": 60}))
    assert len({r["card_id"] for r in spread.rows}) == 3


def test_config_validation():
    with pytest.raises(ValueError, match="Unknown card ids"):
        demo.resolve_config(None, {"wallet": ["amex_platinum"]})
    with pytest.raises(ValueError, match="Unknown mix categories"):
        demo.resolve_config(None, {"mix": {"crypto": 1}})
    with pytest.raises(ValueError, match="positive weight"):
        demo.resolve_config(None, {"mix": {"dining": 0}})
    with pytest.raises(ValueError, match="not in the wallet"):
        demo.resolve_config(None, {"wallet": ["uob_one"], "card_mode": "primary", "primary_card": "ocbc_365"})
    with pytest.raises(ValueError):
        demo.resolve_config(None, {"transaction_count": 1})
    # primary defaults to the first wallet card when the mode needs one
    assert demo.resolve_config(None, {"wallet": ["ocbc_365", "uob_one"], "card_mode": "habit"}).primary_card == "uob_one"


def test_to_fixture_matches_fixture_schema():
    statement = demo.generate(demo.resolve_config("grocery_family"))
    fixture = statement.to_fixture("frozen_demo")
    assert {"id", "label", "description", "cycle_label", "wallet", "transactions"} <= set(fixture)
    assert fixture["transactions"][0].keys() == {"date", "merchant", "amount", "card_id"}


def test_bootstrap_exposes_demo_controls(client):
    body = client.get("/api/bootstrap").json()["demo"]
    assert {p["id"] for p in body["presets"]} >= {"household_3_card", "single_card_professional"}
    assert {m["id"] for m in body["card_modes"]} == set(demo.CARD_MODES)
    assert [c["key"] for c in body["categories"]] == demo.mix_categories()
    assert body["limits"]["transaction_count"]["max"] == 200


def test_demo_endpoint_generates_and_reports_config(client):
    response = client.post("/api/analyze/demo", json={"preset": "household_3_card", "seed": 3, "transaction_count": 20})
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["transaction_count"] == 20
    assert data["source"]["kind"] == "demo"
    assert data["source"]["label"] == "3-card household"
    assert data["demo_config"]["seed"] == 3 and data["demo_config"]["preset"] == "household_3_card"
    assert client.get(f"/api/export/{data['session_id']}.csv").status_code == 200

    again = client.post("/api/analyze/demo", json={"preset": "household_3_card", "seed": 3, "transaction_count": 20}).json()
    assert again["summary"] == data["summary"]


def test_demo_endpoint_errors_are_readable(client):
    assert client.post("/api/analyze/demo", json={"preset": "nope"}).status_code == 404
    bad = client.post("/api/analyze/demo", json={"wallet": ["nope"]})
    assert bad.status_code == 422
    assert bad.json()["detail"].startswith("wallet: Unknown card ids")
    too_many = client.post("/api/analyze/demo", json={"transaction_count": 9999})
    assert too_many.status_code == 422 and "transaction_count" in too_many.json()["detail"]
