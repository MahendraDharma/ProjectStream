"""TV pairing sessions (flystream.net parity: /api/tv-pair + remote claim).

A TV opens /tv, which generates a pairing code and connects a WebSocket as
the "screen". A phone/laptop claims the code and becomes the "controller".
The server relays controller commands (play/pause/seek/load) to the screen.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

STALE_AFTER_SECONDS = 3600


@dataclass
class TvSession:
    code: str
    device_name: str = "TV"
    created_at: float = field(default_factory=time.time)
    screen: Any = None  # async callable(dict) -> None
    controller: Any = None

    @property
    def stale(self) -> bool:
        return time.time() - self.created_at > STALE_AFTER_SECONDS


class TvRegistry:
    def __init__(self) -> None:
        self.sessions: dict[str, TvSession] = {}

    def new_code(self) -> str:
        # 4-digit pairing code, displayed large on the TV
        return f"{secrets.randbelow(10000):04d}"

    def create(self, device_name: str) -> TvSession:
        for _ in range(50):
            code = self.new_code()
            if code not in self.sessions:
                session = TvSession(code=code, device_name=device_name[:40] or "TV")
                self.sessions[code] = session
                return session
        raise RuntimeError("unable to allocate a pairing code")

    def get(self, code: str) -> TvSession | None:
        session = self.sessions.get((code or "").strip())
        if session and session.stale:
            self.sessions.pop(session.code, None)
            return None
        return session

    def claim(self, code: str) -> TvSession | None:
        """Controller-side claim: the session must exist and not be claimed yet."""
        session = self.get(code)
        if session is None or session.controller is not None:
            return None
        return session

    def drop(self, code: str) -> None:
        self.sessions.pop((code or "").strip(), None)


tv_registry = TvRegistry()
