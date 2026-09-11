"""Probe how the provider handles reasoning-control parameters.

    python tools/probe_reasoning.py

Sends the same small decision prompt with different extra parameters and
reports latency, completion tokens, and whether a `reasoning_content` field
comes back - i.e. is the model spending its time thinking, and can we dial
that down through this endpoint.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import advisor  # noqa: E402
from app.config import load_dotenv  # noqa: E402

PROMPT = (
    "Three cashback plans for one month of spend. A: $121.89. B: $117.02. C: $56.98. "
    "Plan A needs all three cards to clear their minimum spends; B needs two; C needs one. "
    "Which plan should a cardholder follow? Reply with the letter and one sentence."
)

VARIANTS = {
    "baseline": {},
    "reasoning_effort=low": {"reasoning_effort": "low"},
    "reasoning_effort=none": {"reasoning_effort": "none"},
    "thinking=disabled (deepseek style)": {"thinking": {"type": "disabled"}},
    "reasoning={effort:low} (openrouter style)": {"reasoning": {"effort": "low"}},
    "reasoning={enabled:false}": {"reasoning": {"enabled": False}},
    "max_completion_tokens=400": {"max_completion_tokens": 400},
}


def main() -> int:
    load_dotenv()
    cfg = advisor.settings()
    if not cfg["api_key"]:
        print("OPENCODE_API_KEY is not set.")
        return 1
    session = "probe-" + str(int(time.time()))
    for label, extra in VARIANTS.items():
        body = {"model": cfg["model"], "messages": [{"role": "user", "content": PROMPT}], **extra}
        started = time.time()
        try:
            response = httpx.post(
                f"{cfg['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                    "x-opencode-session": session,
                },
                json=body,
                timeout=180,
            )
        except httpx.HTTPError as exc:
            print(f"{label:<44} network error: {exc}")
            continue
        elapsed = time.time() - started
        if response.status_code >= 400:
            print(f"{label:<44} HTTP {response.status_code}: {response.text[:140]}")
            continue
        data = response.json()
        message = data["choices"][0]["message"]
        usage = data.get("usage", {})
        details = usage.get("completion_tokens_details") or {}
        has_reasoning = bool(message.get("reasoning_content") or message.get("reasoning"))
        answer = (message.get("content") or "").strip().replace("\n", " ")[:70]
        print(
            f"{label:<44} {elapsed:5.1f}s  completion_tok={usage.get('completion_tokens')}  "
            f"reasoning_tok={details.get('reasoning_tokens')}  reasoning_field={has_reasoning}  -> {answer}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
