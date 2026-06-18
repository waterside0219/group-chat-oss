from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DispatchRequest:
    source: str
    route: str
    room_id: str
    sender_id: str
    text: str
    message_id: str
    parent_msg_id: str
    turn_id: str
    mentions: list[str]
    targets: list[str]
    context: str
    hop_count: int


class Dispatcher(Protocol):
    def dispatch(self, request: DispatchRequest) -> tuple[list[str], list[str]]:
        ...


class NullDispatcher:
    """Dispatcher used by the standalone core: store messages but do not wake agents."""

    def dispatch(self, request: DispatchRequest) -> tuple[list[str], list[str]]:
        return [], list(request.targets)
