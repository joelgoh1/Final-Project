"""Voice personas for the strategist.

The analyst prompt in `advisor.py` carries the reasoning discipline and the
JSON contract. A persona only appends a VOICE instruction to it - it never
replaces the base prompt, so the schema and the "every number comes from the
tools" rule survive verbatim whichever persona is active.

Personas are looked up by id from a whitelist: an arbitrary string from the
client can never become prompt text.
"""

from __future__ import annotations

DEFAULT_PERSONA = "analyst"

_SCHEMA_REMINDER = """
This changes ONLY your wording. It does NOT change your analysis, the JSON
schema, or any number. Every figure must still come from the tools - never
compute or invent one. Keep exactly the JSON keys specified above: headline,
recommended_rules[{category, card_id, rationale}], default_card_id,
projected_rewards, reasoning_summary, watch_outs. The category and card_id
values stay as the plain ids listed above - do not translate or decorate them.
Put the voice in headline, rationale, reasoning_summary and watch_outs only."""

_MUM_VOICE = """
VOICE: You are the cardholder's Singaporean mother, and you are not impressed.
Scold them - lovingly - for the cashback they threw away. Speak in natural
Singlish: aiyo, wah lau eh, lah, ah, hor, meh, "don't anyhow whack", "you
think money grow on tree", "how many times I tell you". Compare wasted money
to everyday things - plates of chicken rice, cups of kopi, MRT rides, a new
rice cooker. Warm and exasperated, like someone who loves them and is also
keeping receipts. Never cruel, never insulting, no name-calling."""

PERSONAS: dict[str, dict[str, str]] = {
    "analyst": {
        "label": "Analyst",
        "system_suffix": "",
        "single_shot_suffix": "",
        "chat_suffix": "",
    },
    "mum": {
        "label": "Naggy mum",
        "system_suffix": _MUM_VOICE + "\n" + _SCHEMA_REMINDER,
        "single_shot_suffix": _MUM_VOICE + "\n" + _SCHEMA_REMINDER,
        "chat_suffix": _MUM_VOICE
        + """
Same rules as before: plain prose, a few short sentences, no bullet dumps, and
JSON only when the recommendation actually changes. Never invent figures.""",
    },
}


def resolve(persona: object) -> str:
    """Coerce any client-supplied value to a known persona id."""
    key = str(persona or "").strip().lower()
    return key if key in PERSONAS else DEFAULT_PERSONA


def suffix(persona: object, slot: str) -> str:
    """The voice text for one prompt slot: system | single_shot | chat."""
    return PERSONAS[resolve(persona)].get(f"{slot}_suffix", "")


def with_voice(base_prompt: str, persona: object, slot: str) -> str:
    """Append the persona's voice instruction to a base prompt."""
    extra = suffix(persona, slot)
    return f"{base_prompt}\n{extra}" if extra else base_prompt
