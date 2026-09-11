"""Time and token-audit each model turn of a live strategist run.

    python tools/time_advisor.py [fixture_id]

For every request: latency, prompt/completion/reasoning tokens, how many
characters WE sent (by role) versus what the provider billed, whether the
response carried `reasoning_content`, and which tools were called. The gap
between "chars we sent" and "prompt tokens billed" is where hidden waste lives.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import advisor, catalog  # noqa: E402
from app.analysis import analyze  # noqa: E402
from app.config import load_dotenv  # noqa: E402


def _chars_by_role(messages: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for message in messages:
        role = message.get("role", "?")
        out[role] = out.get(role, 0) + len(json.dumps(message))
    return out


def main() -> int:
    load_dotenv()
    fixture_id = sys.argv[1] if len(sys.argv) > 1 else "sg_multi_card_cycle"
    fixture = catalog.fixture(fixture_id)
    result = analyze(fixture["transactions"], fixture["wallet"])

    cfg = advisor.settings()
    if not cfg["api_key"]:
        print("OPENCODE_API_KEY is not set - nothing to time.")
        return 1
    client = advisor.ChatClient(cfg["api_key"], cfg["base_url"], cfg["model"], reasoning_effort=cfg["reasoning_effort"])

    original = client.chat
    log: list[dict] = []

    def timed(messages, **kwargs):
        started = time.time()
        response = original(messages, **kwargs)
        elapsed = time.time() - started
        message = response["choices"][0]["message"]
        usage = response.get("usage", {})
        details = usage.get("completion_tokens_details") or {}
        log.append(
            {
                "elapsed": elapsed,
                "sent_chars": _chars_by_role(messages),
                "tools_chars": len(json.dumps(kwargs.get("tools") or [])),
                "prompt_tokens": usage.get("prompt_tokens"),
                "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": details.get("reasoning_tokens"),
                "reasoning_content_chars": len(message.get("reasoning_content") or message.get("reasoning") or ""),
                "content_chars": len(message.get("content") or ""),
                "tool_calls": [c["function"]["name"] for c in (message.get("tool_calls") or [])],
            }
        )
        return response

    client.chat = timed  # type: ignore[assignment]

    started = time.time()
    outcome = advisor.run_advisor(result.transactions, result.wallet, result.payload, client=client)
    total = time.time() - started

    for index, entry in enumerate(log, 1):
        sent = sum(entry["sent_chars"].values()) + entry["tools_chars"]
        print(f"turn {index}: {entry['elapsed']:5.1f}s")
        print(f"   sent by us : {sent:>7,} chars  (~{sent // 4:,} tok)  by role {entry['sent_chars']}  tool specs {entry['tools_chars']:,} chars")
        print(f"   billed     : prompt_tok={entry['prompt_tokens']}  cached={entry['cached_tokens']}  completion_tok={entry['completion_tokens']}  reasoning_tok={entry['reasoning_tokens']}")
        print(f"   response   : reasoning_content={entry['reasoning_content_chars']:,} chars  content={entry['content_chars']:,} chars  tool_calls={entry['tool_calls'] or 'FINAL ANSWER'}")
    print(
        f"total {total:.1f}s over {len(log)} turns | mode={outcome.mode} path={outcome.path} "
        f"| strategies tested={outcome.strategies_tested} | verified=${outcome.verified_rewards:.2f}"
    )
    if outcome.headline:
        print("headline:", outcome.headline)
    if outcome.detail:
        print("detail:", outcome.detail)

    print("\nreasoning tree")
    for entry in outcome.trace:
        tokens = entry.get("tokens", {})
        print(
            f"+-- turn {entry['turn']}  [{'tools offered' if entry.get('tools_offered') else 'answer only'}]  "
            f"reasoning_tok={tokens.get('reasoning')}  completion_tok={tokens.get('completion')}"
        )
        reasoning = (entry.get("reasoning") or "").strip().replace("\n", " ")
        if reasoning:
            print(f"|   thinking: {reasoning[:300]}{'...' if len(reasoning) > 300 else ''}")
        for call in entry.get("tool_calls", []):
            result = call.get("result", {})
            if "error" in result:
                verdict = f"ERROR {result['error']}"
            elif "total_reward" in result:
                verdict = f"${result['total_reward']:.2f} ({result.get('vs_actual', 0):+.2f} vs actual)"
            else:
                verdict = ", ".join(f"{k}={v}" for k, v in result.items() if k not in ("strategy", "cards"))[:120]
            print(f"|   +-- {call['name']}({json.dumps(call.get('args', {}))[:90]}) -> {verdict}")
            if "moves" in result:
                for move in result["moves"]:
                    print(f"|   |     . {move}")
        text = (entry.get("text") or "").strip().replace("\n", " ")
        if text and not entry.get("tool_calls"):
            print(f"|   answer: {text[:200]}{'...' if len(text) > 200 else ''}")
    if outcome.best_engine_plan:
        plan = outcome.best_engine_plan
        print(f"+-- engine baseline: {plan['label']} = ${plan['total_reward']:.2f}  rules={plan['rules']} default={plan['default_card_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
