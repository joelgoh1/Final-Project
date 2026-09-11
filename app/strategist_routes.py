"""Conversation with the strategist: feedback, complaints, "why not X?".

Kept as its own router so the chat surface can evolve without touching the
core analysis routes. Chat history lives in the session (in memory, TTL'd);
the tool-loop transcript is not kept - each turn rebuilds ground truth from
the engine, so the model can never argue from stale numbers.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .advisor import chat_with_strategist
from .session_store import store

router = APIRouter()

HISTORY_KEY = "strategist_chat"
MAX_STORED_TURNS = 40


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.get("/api/advisor/{session_id}/chat")
def chat_history(session_id: str) -> dict:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session expired. Re-run the analysis.")
    return {"history": session.extras.get(HISTORY_KEY, [])}


@router.post("/api/advisor/{session_id}/chat")
def chat(session_id: str, request: ChatRequest) -> dict:
    """Send one message to the strategist; may return a revised, engine-verified plan."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session expired. Re-run the analysis.")

    history: list[dict] = session.extras.setdefault(HISTORY_KEY, [])
    prior = session.advisor if session.advisor and session.advisor.get("mode") == "agent" else None
    outcome = chat_with_strategist(
        session.result.transactions,
        session.result.wallet,
        session.result.payload,
        prior,
        history,
        request.message,
    )

    if outcome["mode"] == "agent":
        history.append({"role": "user", "text": request.message})
        history.append({"role": "assistant", "text": outcome["reply"]})
        del history[:-MAX_STORED_TURNS]
        if outcome["plan"]:
            # Keep the original reasoning tree and append the chat turns that changed the plan.
            earlier = (session.advisor or {}).get("trace", [])
            outcome["plan"]["trace"] = earlier + [
                dict(entry, turn=len(earlier) + index) for index, entry in enumerate(outcome["plan"]["trace"], 1)
            ]
            session.advisor = outcome["plan"]

    return {
        "mode": outcome["mode"],
        "reply": outcome["reply"],
        "plan": outcome["plan"],
        "trace": outcome["trace"],
        "detail": outcome["detail"],
        "history": history,
    }
