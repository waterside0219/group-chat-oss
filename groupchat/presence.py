from __future__ import annotations

from typing import Protocol


class PresenceProvider(Protocol):
    def session_exists(self, session: str) -> bool:
        ...


class AlwaysOfflinePresence:
    def session_exists(self, session: str) -> bool:
        return False
