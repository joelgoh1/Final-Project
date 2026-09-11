"""Ephemeral, in-memory session store.

One process-local dict with a TTL. Nothing is written to disk, and everything
disappears when the process exits - which is the whole privacy promise of v1.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_TTL_SECONDS = 30 * 60
MAX_SESSIONS = 50


@dataclass
class Session:
    id: str
    created_at: float
    result: object  # app.analysis.AnalysisResult
    advisor: Optional[dict] = None
    extras: dict = field(default_factory=dict)


class SessionStore:
    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, Session] = {}

    def _sweep(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        for session_id in [sid for sid, s in self._sessions.items() if s.created_at < cutoff]:
            del self._sessions[session_id]
        # Hard ceiling so a long-lived demo process cannot grow without bound.
        while len(self._sessions) > MAX_SESSIONS:
            oldest = min(self._sessions.values(), key=lambda s: s.created_at)
            del self._sessions[oldest.id]

    def put(self, result: object) -> Session:
        self._sweep()
        session = Session(id=secrets.token_urlsafe(12), created_at=time.time(), result=result)
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Optional[Session]:
        self._sweep()
        return self._sessions.get(session_id)

    def drop(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def clear(self) -> None:
        self._sessions.clear()

    def __len__(self) -> int:
        self._sweep()
        return len(self._sessions)


store = SessionStore()
