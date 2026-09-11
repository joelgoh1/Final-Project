"""HTTP surface plus the advisor path with a stubbed provider client."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import advisor as advisor_module
from app import catalog
from app.analysis import analyze
from app.main import app
from app.rules_engine import score_strategy
from app.session_store import store

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    store.clear()
    return TestClient(app)


def test_bootstrap_lists_fixtures_cards_and_samples(client):
    body = client.get("/api/bootstrap").json()
    assert {f["id"] for f in body["fixtures"]} == {"sg_multi_card_cycle", "dining_heavy_cycle"}
    assert len(body["cards"]) == 4
    assert "dbs_live_fresh_statement.pdf" in body["samples"]
    assert body["advisor_status"]["available"] is False
    assert body["advisor_status"]["model"] == "deepseek-v4.1-flash"


def test_fixture_flow_dashboard_csv_and_delete(client):
    response = client.post("/api/analyze/fixture", json={"fixture_id": "sg_multi_card_cycle"})
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["transaction_count"] == 36
    assert data["summary"]["missed_value"] > 0
    assert len(data["wallet_rules"]) == 3
    assert data["suboptimal_count"] >= len(data["suboptimal"]) > 0

    csv_text = client.get(f"/api/export/{data['session_id']}.csv").text
    lines = csv_text.splitlines()
    assert lines[0].startswith("date,merchant,amount_sgd,category")
    assert len([l for l in lines[1:] if l and l[:4].isdigit()]) == 36
    assert "Wallet strategy for next cycle" in csv_text

    assert client.delete(f"/api/session/{data['session_id']}").json() == {"dropped": True}
    assert client.get(f"/api/export/{data['session_id']}.csv").status_code == 404


def test_unknown_fixture_is_404(client):
    assert client.post("/api/analyze/fixture", json={"fixture_id": "nope"}).status_code == 404


def test_upload_parses_sample_pdf_and_respects_wallet(client):
    with (SAMPLES / "uob_one_statement.pdf").open("rb") as fh:
        response = client.post(
            "/api/analyze/upload",
            files=[("files", ("uob.pdf", fh, "application/pdf"))],
            data={"wallet": "ocbc_365"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["transaction_count"] == 10
    assert {c["id"] for c in data["wallet"]} == {"uob_one", "ocbc_365"}
    assert any("detected UOB" in note for note in data["parse_quality"]["notes"])


def test_upload_rejects_non_pdf_with_helpful_message(client):
    response = client.post(
        "/api/analyze/upload", files=[("files", ("x.pdf", b"garbage", "application/pdf"))]
    )
    assert response.status_code == 422
    assert "could not be read" in response.json()["detail"]


def test_advisor_reports_unavailable_without_credentials(client):
    data = client.post("/api/analyze/fixture", json={"fixture_id": "dining_heavy_cycle"}).json()
    body = client.post(f"/api/advisor/{data['session_id']}").json()
    assert body["mode"] == "unavailable"
    assert "OPENCODE_API_KEY" in body["detail"]


def test_settings_read_env_at_call_time(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "oc-test")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    cfg = advisor_module.settings()
    assert cfg == {
        "api_key": "oc-test",
        "base_url": "https://opencode.ai/zen/go/v1",
        "model": "deepseek-v4.1-flash",
        "reasoning_effort": "",  # provider default unless LLM_REASONING_EFFORT is set
    }
    status = advisor_module.advisor_status()
    assert status["available"] and status["provider"] == "https://opencode.ai/zen/go/v1"


# --------------------------------------------------------------------------- #
# Stubbed provider clients
# --------------------------------------------------------------------------- #


def _assistant(content=None, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def _call(call_id, name, args):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class _ScriptedClient:
    """Plays back a scripted function-calling conversation."""

    model = "deepseek-v4.1-flash"
    base_url = "https://opencode.ai/zen/go/v1"

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def chat(self, messages, tools=None, response_format=None, temperature=0.2):
        self.requests.append({"messages": list(messages), "tools": tools, "response_format": response_format})
        return self.turns.pop(0)


def test_tool_loop_runs_engine_tools_and_verifies_the_answer():
    raw = catalog.fixture("sg_multi_card_cycle")
    result = analyze(raw["transactions"], raw["wallet"])
    final = {
        "headline": "Put groceries on UOB One and dining on OCBC 365.",
        "recommended_rules": [
            {"category": "groceries", "card_id": "uob_one", "rationale": "10% beats everything."},
            {"category": "dining", "card_id": "ocbc_365", "rationale": "6% dining."},
            {"category": "dining", "card_id": "dbs_live_fresh", "rationale": "duplicate - must be ignored"},
            {"category": "shopping", "card_id": "not_a_card", "rationale": "unknown - must be ignored"},
        ],
        "default_card_id": "dbs_live_fresh",
        "projected_rewards": 999.99,  # deliberately wrong: the engine must override it
        "reasoning_summary": "Tested three plans.",
        "watch_outs": ["OCBC needs $800/mo."],
    }
    fake = _ScriptedClient(
        [
            # info tools only - does not count as an exploration round
            _assistant(tool_calls=[_call("c1", "get_card_rules", {}), _call("c2", "get_spend_summary", {})]),
            # the single allowed scoring round, with one valid and one bogus plan
            _assistant(tool_calls=[
                _call("c3", "score_allocation", {"rules": [{"category": "groceries", "card_id": "uob_one"}], "default_card_id": "dbs_live_fresh"}),
                _call("c4", "score_allocation", {"rules": [{"category": "dining", "card_id": "bogus"}]}),
            ]),
            _assistant(content="```json\n" + json.dumps(final) + "\n```"),
        ]
    )
    outcome = advisor_module.run_advisor(result.transactions, result.wallet, result.payload, client=fake)

    assert outcome.mode == "agent" and outcome.path == "tool-loop"
    assert [r.category for r in outcome.rules] == ["groceries", "dining"]
    expected = score_strategy(
        result.transactions, result.wallet, {"groceries": "uob_one", "dining": "ocbc_365"}, "dbs_live_fresh"
    ).total_reward
    assert outcome.verified_rewards == pytest.approx(expected)
    assert outcome.model_claimed_rewards == 999.99
    seeded = len(advisor_module.precomputed_candidates(result.transactions, result.wallet))
    assert outcome.strategies_tested == seeded + 1  # bogus card id rejected, not scored

    # Seeded context in the first user turn; tools offered while exploring, withheld after.
    first_user = fake.requests[0]["messages"][1]["content"]
    assert "engine_scored_baselines" in first_user and "card_rules" in first_user
    assert len(fake.requests[0]["tools"]) == 7 and len(fake.requests[1]["tools"]) == 7
    assert fake.requests[2]["tools"] is None
    assert "final JSON answer now" in fake.requests[2]["messages"][-1]["content"]

    # The reasoning tree records every turn, call and result summary.
    assert [t["turn"] for t in outcome.trace] == [1, 2, 3]
    assert [c["name"] for c in outcome.trace[0]["tool_calls"]] == ["get_card_rules", "get_spend_summary"]
    scored = outcome.trace[1]["tool_calls"]
    assert scored[0]["result"]["total_reward"] == pytest.approx(
        score_strategy(result.transactions, result.wallet, {"groceries": "uob_one"}, "dbs_live_fresh").total_reward, abs=0.01
    )
    assert "error" in scored[1]["result"]
    assert outcome.trace[2]["tools_offered"] is False and outcome.trace[2]["tool_calls"] == []
    assert "trace" in outcome.to_dict()

    # Tool results were fed back in OpenAI format and hold only redacted data.
    tool_messages = [m for m in fake.requests[-1]["messages"] if m["role"] == "tool"]
    assert {m["tool_call_id"] for m in tool_messages} == {"c1", "c2", "c3", "c4"}
    assert "Unknown card ids" in next(m["content"] for m in tool_messages if m["tool_call_id"] == "c4")
    blob = json.dumps(fake.requests[-1]["messages"])
    assert "TAN AH KOW" not in blob and "4123" not in blob


def test_skills_build_and_repair_plans_deterministically():
    raw = catalog.fixture("sg_multi_card_cycle")
    result = analyze(raw["transactions"], raw["wallet"])
    tested: list = []
    _specs, impls = advisor_module.build_tools(result.transactions, result.wallet, result.payload, tested)

    naive = json.loads(impls["best_rate_plan"]({}))
    # Groceries -> UOB One (10%), dining -> OCBC 365 (6%), shopping -> DBS (5%).
    assert naive["strategy"]["rules"]["groceries"] == "uob_one"
    assert naive["strategy"]["rules"]["dining"] == "ocbc_365"
    assert naive["strategy"]["rules"]["shopping"] == "dbs_live_fresh"
    assert "note" in naive

    rules = [{"category": c, "card_id": cid} for c, cid in naive["strategy"]["rules"].items()]
    repaired = json.loads(impls["fix_minimums"]({"rules": rules, "default_card_id": naive["strategy"]["default_card_id"]}))
    assert repaired["total_reward"] >= naive["total_reward"]
    assert isinstance(repaired["moves"], list)
    # Every accepted move is a real engine improvement; the result is re-scorable.
    mapping = repaired["strategy"]["rules"]
    default = repaired["strategy"]["default_card_id"]
    default = None if default == "keep as charged" else default
    assert repaired["total_reward"] == pytest.approx(
        score_strategy(result.transactions, result.wallet, mapping, default).total_reward, abs=0.01
    )
    assert len(tested) == 2  # both skill results count as tested strategies

    same_again = json.loads(impls["fix_minimums"]({"rules": rules, "default_card_id": naive["strategy"]["default_card_id"]}))
    assert same_again["strategy"] == repaired["strategy"]  # deterministic

    bad = json.loads(impls["fix_minimums"]({"rules": [{"category": "dining", "card_id": "nope"}]}))
    assert "error" in bad


class _NoToolsClient:
    """A provider that 400s on function calling but answers a plain request."""

    model = "deepseek-v4.1-flash"
    base_url = "https://example.test/v1"

    def __init__(self, final_json):
        self.loop_attempts = 0
        self.plain_requests = []
        self.final_json = final_json

    def chat(self, messages, tools=None, response_format=None, temperature=0.2):
        if tools:
            self.loop_attempts += 1
            raise advisor_module.ProviderError("tools not supported", status_code=400)
        self.plain_requests.append({"messages": messages, "response_format": response_format})
        return _assistant(content=json.dumps(self.final_json))


def test_single_shot_fallback_when_provider_rejects_tools():
    raw = catalog.fixture("sg_multi_card_cycle")
    result = analyze(raw["transactions"], raw["wallet"])
    answer = {
        "headline": "Consolidate groceries and utilities on UOB One.",
        "recommended_rules": [{"category": "groceries", "card_id": "uob_one", "rationale": "10%."}],
        "default_card_id": "uob_one",
        "projected_rewards": 1.0,
        "reasoning_summary": "Picked the top engine candidate.",
        "watch_outs": [],
    }
    fake = _NoToolsClient(answer)
    outcome = advisor_module.run_advisor(result.transactions, result.wallet, result.payload, client=fake)

    assert fake.loop_attempts == 1
    assert outcome.mode == "agent" and outcome.path == "single-shot"
    assert outcome.strategies_tested >= 3  # engine-precomputed candidates were supplied
    assert "single-shot" in outcome.detail
    expected = score_strategy(result.transactions, result.wallet, {"groceries": "uob_one"}, "uob_one").total_reward
    assert outcome.verified_rewards == pytest.approx(expected)
    sent = fake.plain_requests[0]
    assert sent["response_format"] == {"type": "json_object"}
    assert "engine_scored_candidates" in sent["messages"][1]["content"]


def test_provider_error_surfaces_as_error_mode():
    class _Broken:
        model, base_url = "m", "b"

        def chat(self, *a, **k):
            raise advisor_module.ProviderError("boom", status_code=500)

    raw = catalog.fixture("dining_heavy_cycle")
    result = analyze(raw["transactions"], raw["wallet"])
    outcome = advisor_module.run_advisor(result.transactions, result.wallet, result.payload, client=_Broken())
    assert outcome.mode == "error" and "boom" in outcome.detail


def test_precomputed_candidates_are_engine_scored_and_sorted():
    raw = catalog.fixture("dining_heavy_cycle")
    result = analyze(raw["transactions"], raw["wallet"])
    candidates = advisor_module.precomputed_candidates(result.transactions, result.wallet)
    assert any(c["label"] == "as charged this cycle" for c in candidates)
    assert [c["total_reward"] for c in candidates] == sorted((c["total_reward"] for c in candidates), reverse=True)
    as_charged = next(c for c in candidates if c["label"] == "as charged this cycle")
    assert as_charged["total_reward"] == pytest.approx(result.payload["summary"]["actual_rewards"], abs=0.01)


def test_parse_answer_handles_fenced_bare_parts_and_junk():
    assert advisor_module.parse_answer('```json\n{"a": 1}\n```') == {"a": 1}
    assert advisor_module.parse_answer('Sure! {"a": 2} hope that helps') == {"a": 2}
    assert advisor_module.parse_answer([{"type": "text", "text": '{"a": 3}'}]) == {"a": 3}
    assert advisor_module.parse_answer("no json here") is None
    assert advisor_module.parse_answer("[1, 2]") is None
    assert advisor_module.parse_answer(None) is None


def test_chat_client_sends_bearer_and_stable_opencode_session(monkeypatch):
    import httpx

    seen = []

    class _Resp:
        status_code = 200
        text = "{}"

        def json(self):
            return _assistant(content='{"ok": true}')

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append({"url": url, "headers": headers, "json": json})
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    client = advisor_module.ChatClient("oc-key", "https://opencode.ai/zen/go/v1/", "deepseek-v4.1-flash")
    client.chat([{"role": "user", "content": "hi"}])
    client.chat([{"role": "user", "content": "again"}], tools=[{"type": "function", "function": {"name": "f"}}])

    assert seen[0]["url"] == "https://opencode.ai/zen/go/v1/chat/completions"
    assert seen[0]["headers"]["Authorization"] == "Bearer oc-key"
    session = seen[0]["headers"]["x-opencode-session"]
    assert session and session == seen[1]["headers"]["x-opencode-session"]  # stable within one run
    assert seen[1]["json"]["tool_choice"] == "auto" and seen[1]["json"]["model"] == "deepseek-v4.1-flash"
    assert advisor_module.ChatClient("k", "u", "m").session_id != session  # fresh per run


def test_chat_turn_answers_in_prose_without_changing_the_plan():
    raw = catalog.fixture("sg_multi_card_cycle")
    result = analyze(raw["transactions"], raw["wallet"])
    prior = {
        "mode": "agent",
        "headline": "Groceries on UOB One.",
        "rules": [{"category": "groceries", "card_id": "uob_one", "rationale": "10%"}],
        "default_card_id": "ocbc_365",
        "verified_rewards": 112.19,
        "reasoning_summary": "",
        "watch_outs": [],
    }
    fake = _ScriptedClient([
        _assistant(tool_calls=[_call("s1", "score_allocation", {"rules": [{"category": "dining", "card_id": "ocbc_365"}], "default_card_id": "ocbc_365"})]),
        _assistant(content="Dining is already on OCBC 365 by default, so a separate rule adds nothing - the engine scores it the same."),
    ])
    out = advisor_module.chat_with_strategist(
        result.transactions, result.wallet, result.payload, prior, [], "Why not put dining on the 6% card?", client=fake
    )
    assert out["mode"] == "agent" and out["plan"] is None
    assert out["reply"].startswith("Dining is already")
    # Context rebuilt from ground truth: seed, prior plan, then the question + chat instructions.
    sent = fake.requests[0]["messages"]
    assert "engine_scored_baselines" in sent[1]["content"]
    assert "My current recommendation" in sent[2]["content"] and "uob_one" in sent[2]["content"]
    assert sent[-1]["content"].startswith("Why not put dining") and "in conversation with the cardholder" in sent[-1]["content"]
    assert [c["name"] for c in out["trace"][0]["tool_calls"]] == ["score_allocation"]
    assert fake.requests[1]["tools"] is None  # one scoring round, then the reply


def test_chat_turn_can_revise_the_plan_and_is_re_verified():
    raw = catalog.fixture("sg_multi_card_cycle")
    result = analyze(raw["transactions"], raw["wallet"])
    revised = {
        "headline": "Two rules: groceries on UOB One, everything else on OCBC 365.",
        "recommended_rules": [{"category": "groceries", "card_id": "uob_one", "rationale": "10%."}],
        "default_card_id": "ocbc_365",
        "projected_rewards": 5.0,  # wrong on purpose; the engine overrides
        "reasoning_summary": "Simpler as asked.",
        "watch_outs": ["OCBC needs $800/mo."],
    }
    fake = _ScriptedClient([
        _assistant(content="Sure - here is a simpler version.\n```json\n" + json.dumps(revised) + "\n```"),
    ])
    history = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}]
    out = advisor_module.chat_with_strategist(
        result.transactions, result.wallet, result.payload, None, history, "Give me a 2-rule version.", client=fake
    )
    assert out["mode"] == "agent" and out["plan"] is not None
    assert out["reply"] == "Sure - here is a simpler version."
    expected = score_strategy(result.transactions, result.wallet, {"groceries": "uob_one"}, "ocbc_365").total_reward
    assert out["plan"]["verified_rewards"] == pytest.approx(round(expected, 2))
    assert out["plan"]["model_claimed_rewards"] == 5.0
    assert out["plan"]["path"] == "chat" and out["plan"]["best_engine_plan"]["total_reward"] >= out["plan"]["verified_rewards"]
    sent = fake.requests[0]["messages"]
    assert [m["role"] for m in sent[:5]] == ["system", "user", "assistant", "user", "assistant"]  # seed, no-plan note, history
    assert "not produced a recommendation" in sent[2]["content"]


def test_chat_route_persists_history_and_updates_the_plan(client, monkeypatch):
    from app.analysis import analyze as _analyze

    raw = catalog.fixture("dining_heavy_cycle")
    session = store.put(_analyze(raw["transactions"], raw["wallet"]))
    calls = []

    def fake_chat(transactions, wallet, dashboard, prior, history, message, client=None):
        calls.append({"prior": prior, "history": list(history), "message": message})
        plan = None
        if "simpler" in message:
            plan = {"mode": "agent", "headline": "Simpler.", "rules": [], "verified_rewards": 1.0, "trace": [{"turn": 1}], "watch_outs": [], "reasoning_summary": "", "default_card_id": "", "default_card_name": ""}
        return {"mode": "agent", "reply": f"echo: {message}", "plan": plan, "trace": [], "detail": ""}

    monkeypatch.setattr("app.strategist_routes.chat_with_strategist", fake_chat)

    assert client.get(f"/api/advisor/{session.id}/chat").json() == {"history": []}
    first = client.post(f"/api/advisor/{session.id}/chat", json={"message": "why?"}).json()
    assert first["reply"] == "echo: why?" and first["plan"] is None
    assert [m["role"] for m in first["history"]] == ["user", "assistant"]

    second = client.post(f"/api/advisor/{session.id}/chat", json={"message": "make it simpler"}).json()
    assert second["plan"]["headline"] == "Simpler."
    assert calls[1]["history"][0]["text"] == "why?"  # earlier turns were passed back in
    assert store.get(session.id).advisor["headline"] == "Simpler."  # the session's plan was replaced
    assert len(second["history"]) == 4

    assert client.post("/api/advisor/nope/chat", json={"message": "x"}).status_code == 404
    assert client.post(f"/api/advisor/{session.id}/chat", json={"message": ""}).status_code == 422


def test_chat_client_raises_provider_error_with_status(monkeypatch):
    import httpx

    class _Resp:
        status_code = 400
        text = '{"error": "MissingSessionID"}'

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    with pytest.raises(advisor_module.ProviderError) as info:
        advisor_module.ChatClient("k", "https://x.test/v1", "m").chat([{"role": "user", "content": "hi"}])
    assert info.value.status_code == 400 and "MissingSessionID" in str(info.value)


# --- Months: several analysed cycles held at once ---


def test_months_crud_list_rename_delete_and_totals(client):
    first = client.post("/api/analyze/fixture", json={"fixture_id": "sg_multi_card_cycle"}).json()
    assert first["label"] == "Aug 2026"
    assert first["period"]["key"] == "2026-08"

    second = client.post(
        "/api/analyze/fixture", json={"fixture_id": "dining_heavy_cycle", "label": "  Dining   test "}
    ).json()
    assert second["label"] == "Dining test"

    listing = client.get("/api/months").json()
    assert listing["count"] == 2
    assert [m["session_id"] for m in listing["months"]] == sorted(
        [first["session_id"], second["session_id"]],
        key=lambda sid: next(m["period"]["key"] for m in listing["months"] if m["session_id"] == sid),
    )
    totals = listing["totals"]
    assert totals["total_spend"] == round(
        first["summary"]["total_spend"] + second["summary"]["total_spend"], 2
    )
    assert totals["transaction_count"] == (
        first["summary"]["transaction_count"] + second["summary"]["transaction_count"]
    )
    assert totals["missed_value"] > 0

    renamed = client.patch(f"/api/months/{first['session_id']}", json={"label": "August real"}).json()
    assert renamed["label"] == "August real"
    assert client.get(f"/api/months/{first['session_id']}").json()["label"] == "August real"
    assert client.patch(f"/api/months/{first['session_id']}", json={"label": "   "}).status_code == 422

    full = client.get(f"/api/months/{second['session_id']}").json()
    assert full["summary"] == second["summary"]
    assert full["advisor"] is None

    assert client.delete(f"/api/months/{first['session_id']}").json() == {"dropped": True}
    assert client.get(f"/api/months/{first['session_id']}").status_code == 404
    assert client.get("/api/months").json()["count"] == 1

    assert client.delete("/api/months").json() == {"dropped": 1}
    assert client.get("/api/months").json() == {
        "months": [],
        "count": 0,
        "totals": {
            "total_spend": 0.0, "actual_rewards": 0.0, "optimal_rewards": 0.0,
            "missed_value": 0.0, "transaction_count": 0,
            "actual_yield_pct": 0.0, "optimal_yield_pct": 0.0,
        },
    }


def test_statement_period_labels_single_and_multi_month():
    from app.analysis import statement_period
    from app.models import Transaction

    def txn(day):
        return Transaction(id=day, date=day, merchant="x", amount=1.0, card_id="uob_one")

    assert statement_period([txn("2026-08-03"), txn("2026-08-29")])["label"] == "Aug 2026"
    span = statement_period([txn("2026-08-25"), txn("2026-09-02")])
    assert span["label"] == "Aug - Sep 2026" and span["key"] == "2026-08"
    assert statement_period([txn("2026-12-25"), txn("2027-01-02")])["label"] == "Dec 2026 - Jan 2027"
    assert statement_period([txn("garbage")])["label"] == ""


def test_upload_month_takes_label_and_derives_period(client):
    with (SAMPLES / "uob_one_statement.pdf").open("rb") as fh:
        data = client.post(
            "/api/analyze/upload",
            files={"files": ("uob_one_statement.pdf", fh, "application/pdf")},
            data={"label": "My UOB month"},
        ).json()
    assert data["label"] == "My UOB month"
    assert data["period"]["key"]
    assert data["source"]["cycle_label"] == data["period"]["label"]
