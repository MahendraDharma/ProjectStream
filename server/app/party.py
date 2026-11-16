"""In-memory watch-party registry (flystream.net parity, learning build).

Rooms live in this process: a party code maps to a set of connected members,
a shared playback state, and recent chat. The creator is the host; when the
host disconnects, the next member is promoted (the "claim" flow).

Multi-process deployments would need a shared store (Redis); the Dockerfile
runs a single uvicorn process, which is the intended shape here.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

ALLOWED_ACTIONS = {"play", "pause", "seek", "chat", "emoji", "state", "sync"}


@dataclass
class Member:
    member_id: str
    name: str
    role: str  # 'host' | 'guest' | 'controller' | 'screen'
    send: Any  # async callable(dict) -> None


@dataclass
class Room:
    code: str
    created_at: float = field(default_factory=time.time)
    members: dict[str, Member] = field(default_factory=dict)
    state: dict = field(default_factory=lambda: {"tmdb_id": None, "media_type": None, "playing": False, "position": 0.0})
    chat: list[dict] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def host(self) -> Member | None:
        for member in self.members.values():
            if member.role == "host":
                return member
        return None


class PartyRegistry:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def new_code(self) -> str:
        while True:
            code = secrets.token_hex(3).upper()  # 6 hex chars, e.g. "A1B2C3"
            if code not in self.rooms:
                return code

    def create(self, name: str) -> Room:
        room = Room(code=self.new_code())
        self.rooms[room.code] = room
        return room

    def get(self, code: str) -> Room | None:
        return self.rooms.get((code or "").upper())

    def peek(self, code: str) -> dict | None:
        room = self.get(code)
        if room is None:
            return None
        return {"code": room.code, "members": len(room.members), "created_at": room.created_at}

    def join(self, room: Room, member: Member) -> None:
        self.rooms[room.code].members[member.member_id] = member

    def leave(self, room: Room, member_id: str) -> Member | None:
        """Remove a member; promote a new host when the host leaves. Returns the removed member."""
        removed = self.rooms[room.code].members.pop(member_id, None)
        if removed and removed.role == "host":
            next_member = next(iter(self.rooms[room.code].members.values()), None)
            if next_member:
                next_member.role = "host"
        if not self.rooms[room.code].members:
            self.rooms.pop(room.code, None)
        return removed

    def members_snapshot(self, room: Room) -> list[dict]:
        return [{"member_id": m.member_id, "name": m.name, "role": m.role} for m in room.members.values()]


registry = PartyRegistry()
