"""Wallet workbench: edited and invented cards flow through the API into the engine."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.session_store import store


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    store.clear()
    return TestClient(app)


def test_bootstrap_exposes_editable_card_rules(client):
    body = client.get("/api/bootstrap").json()
    assert set(body["categories"]) == {"dining", "groceries", "transport", "shopping", "utilities"}
    uob = next(c for c in body["cards"] if c["id"] == "uob_one")
    assert uob["base_rate"] == pytest.approx(0.0033)
    assert uob["category_rates"]["groceries"] == pytest.approx(0.10)


def test_new_custom_card_joins_the_wallet_and_can_win(client):
    baseline = client.post("/api/analyze/fixture", json={"fixture_id": "sg_multi_card_cycle"}).json()
    unicorn = {
        "id": "unicorn",
        "name": "Unicorn Unlimited",
        "issuer": "Custom",
        "base_rate": 0.10,
        "category_rates": {},
        "min_spend": 0,
        "monthly_cap": None,
    }
    response = client.post(
        "/api/analyze/fixture",
        json={"fixture_id": "sg_multi_card_cycle", "wallet": ["unicorn"], "custom_cards": [unicorn]},
    )
    assert response.status_code == 200
    data = response.json()
    ids = [c["id"] for c in data["cards"]]
    assert "unicorn" in ids and "dbs_live_fresh" in ids  # statement cards are always kept
    unicorn_row = next(c for c in data["cards"] if c["id"] == "unicorn")
    assert unicorn_row["customised"] is True
    assert data["summary"]["optimal_rewards"] > baseline["summary"]["optimal_rewards"]
    assert data["summary"]["optimal_rewards"] == pytest.approx(data["summary"]["total_spend"] * 0.10, abs=0.02)
    assert any("Unicorn Unlimited: rules edited" in note for note in data["assumptions"])


def test_editing_a_bundled_card_rescores_the_statement_itself(client):
    baseline = client.post("/api/analyze/fixture", json={"fixture_id": "sg_multi_card_cycle"}).json()
    edited = {
        "id": "dbs_live_fresh",
        "name": "DBS Live Fresh (no minimum)",
        "issuer": "DBS",
        "base_rate": 0.003,
        "category_rates": {"shopping": 0.05, "dining": 0.05, "transport": 0.05},
        "min_spend": 0,
        "monthly_cap": 20,
    }
    data = client.post(
        "/api/analyze/fixture", json={"fixture_id": "sg_multi_card_cycle", "custom_cards": [edited]}
    ).json()
    dbs = next(c for c in data["cards"] if c["id"] == "dbs_live_fresh")
    assert dbs["name"] == "DBS Live Fresh (no minimum)" and dbs["customised"] is True
    assert dbs["min_spend_met"] is True
    assert data["summary"]["actual_rewards"] >= baseline["summary"]["actual_rewards"]
    assert data["wallet"][0]["customised"] is True


def test_custom_card_validation(client):
    bad_id = {"id": "Not Valid!", "name": "x", "base_rate": 0.01}
    assert client.post("/api/analyze/fixture", json={"fixture_id": "sg_multi_card_cycle", "custom_cards": [bad_id]}).status_code == 422
    bad_cat = {"id": "ok", "name": "x", "base_rate": 0.01, "category_rates": {"crypto": 0.5}}
    assert client.post("/api/analyze/fixture", json={"fixture_id": "sg_multi_card_cycle", "custom_cards": [bad_cat]}).status_code == 422
    too_high = {"id": "ok", "name": "x", "base_rate": 1.5}
    assert client.post("/api/analyze/fixture", json={"fixture_id": "sg_multi_card_cycle", "custom_cards": [too_high]}).status_code == 422


def test_upload_accepts_custom_cards_form_field(client, tmp_path):
    from pathlib import Path
    sample = Path(__file__).resolve().parent.parent / "samples" / "uob_one_statement.pdf"
    with sample.open("rb") as fh:
        response = client.post(
            "/api/analyze/upload",
            files={"files": ("uob.pdf", fh, "application/pdf")},
            data={"custom_cards": '[{"id":"unicorn","name":"Unicorn","base_rate":0.2}]', "wallet": "unicorn"},
        )
    assert response.status_code == 200
    assert "unicorn" in [c["id"] for c in response.json()["cards"]]
    assert client.post(
        "/api/analyze/upload",
        files={"files": ("uob.pdf", b"%PDF-1.4", "application/pdf")},
        data={"custom_cards": "not json"},
    ).status_code == 422
