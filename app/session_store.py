"""Ephemeral, in-memory session store.

One process-local dict with a TTL. Nothing is written to disk, and everything
disappears when the process exits - which is the whole privacy promise of v1.

Each session is one analysed statement cycle ("month"). Several can be held at
once so the dashboard can switch between them and total them up; the TTL is
measured from the last time a session was touched, so a month you keep looking
at does not vanish under you.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_TTL_SECONDS = 30 * 60
MAX_SESSIONS = 50
MAX_LABEL_LENGTH = 60


@dataclass
class Session:
    id: str
    created_at: float
    result: object  # app.analysis.AnalysisResult
    label: str = ""
    touched_at: float = 0.0
    advisor: Optional[dict] = None
    extras: dict = field(default_factory=dict)

    def touch(self) -> None:
        self.touched_at = time.time()


def clean_label(label: Optional[str], fallback: str) -> str:
    """Trim, collapse whitespace and cap a user-supplied month label."""
    text = " ".join(str(label or "").split())[:MAX_LABEL_LENGTH].strip()
    return text or fallback


class SessionStore:
    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, Session] = {}

    def _sweep(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        for session_id in [sid for sid, s in self._sessions.items() if s.touched_at < cutoff]:
            del self._sessions[session_id]
        # Hard ceiling so a long-lived demo process cannot grow without bound.
        while len(self._sessions) > MAX_SESSIONS:
            oldest = min(self._sessions.values(), key=lambda s: s.touched_at)
            del self._sessions[oldest.id]

    def put(self, result: object, label: str = "") -> Session:
        self._sweep()
        now = time.time()
        session = Session(
            id=secrets.token_urlsafe(12), created_at=now, touched_at=now, result=result, label=label
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Optional[Session]:
        self._sweep()
        session = self._sessions.get(session_id)
        if session is not None:
            session.touch()
        return session

    def list(self) -> list[Session]:
        """All live sessions, oldest first. Does not refresh their TTL."""
        self._sweep()
        return sorted(self._sessions.values(), key=lambda s: s.created_at)

    def rename(self, session_id: str, label: str) -> Optional[Session]:
        session = self.get(session_id)
        if session is not None:
            session.label = clean_label(label, session.label)
        return session

    def drop(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def clear(self) -> None:
        self._sessions.clear()

    def __len__(self) -> int:
        self._sweep()
        return len(self._sessions)


store = SessionStore()
