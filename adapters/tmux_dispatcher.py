from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from typing import Any

from groupchat.dispatcher import DispatchRequest


MAX_AGENT_HANDOFF_HOPS = 3
TRIGGER_DIR = Path("/tmp")
TRIGGER_PREFIX = "groupchat_ctx_"
CONTEXT_PREFIX = "groupchat_context_"


def safe_file_stem(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)
    return cleaned[:120] or f"msg_{int(time.time() * 1000)}"


class TmuxDispatcher:
    def __init__(
        self,
        agent_sessions: dict[str, str],
        agent_names: dict[str, str] | None = None,
        *,
        trigger_dir: Path = TRIGGER_DIR,
    ):
        self.agent_sessions = dict(agent_sessions)
        self.agent_names = dict(agent_names or {})
        self.trigger_dir = trigger_dir

    @classmethod
    def from_roster(cls, roster: list[dict[str, Any]]) -> "TmuxDispatcher":
        sessions: dict[str, str] = {}
        names: dict[str, str] = {}
        for member in roster:
            member_id = str(member.get("id") or "")
            if not member_id:
                continue
            names[member_id] = str(member.get("display_name") or member_id)
            session = str(member.get("tmux") or "").strip()
            if member.get("can_reply") and session:
                sessions[member_id] = session
        return cls(sessions, names)

    def dispatch(self, request: DispatchRequest) -> tuple[list[str], list[str]]:
        delivered: list[str] = []
        failed: list[str] = []
        inject_text = self.build_inject_text(request)
        for agent_id in request.targets:
            session = self.agent_sessions.get(agent_id)
            if not session:
                failed.append(agent_id)
                continue
            if self.inject_tmux(session, inject_text, buffer_hint=f"{request.message_id}_{session}"):
                self.write_trigger(agent_id, session, request)
                delivered.append(agent_id)
            else:
                failed.append(agent_id)
        return delivered, failed

    def inject_tmux(self, session: str, text: str, *, buffer_hint: str = "") -> bool:
        try:
            result = subprocess.run(["tmux", "has-session", "-t", session], capture_output=True, timeout=3)
            if result.returncode != 0:
                return False
            buffer_name = "groupchat_" + safe_file_stem(buffer_hint or session)
            subprocess.run(
                ["tmux", "load-buffer", "-b", buffer_name, "-"],
                input=text.encode("utf-8"),
                check=True,
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                ["tmux", "paste-buffer", "-b", buffer_name, "-d", "-p", "-t", session],
                check=True,
                capture_output=True,
                timeout=5,
            )
            subprocess.run(["tmux", "send-keys", "-t", session, "Enter"], check=True, capture_output=True, timeout=5)
            return True
        except Exception:
            return False

    def build_inject_text(self, request: DispatchRequest) -> str:
        remaining_hops = max(0, MAX_AGENT_HANDOFF_HOPS - request.hop_count)
        sender_name = self.agent_names.get(request.sender_id, request.sender_id)
        mention_names = [self.agent_names.get(m, m) for m in request.mentions]
        header = (
            f"[WORKGROUP route={request.route} room={request.room_id} msg_id={request.message_id} "
            f"from={request.sender_id}({sender_name}) "
            f"hop={request.hop_count} remaining_handoffs={remaining_hops}]"
        )
        if request.mentions:
            header += f" mentions={','.join(request.mentions)}({','.join(mention_names)})"
        protocol = "\n".join(
            [
                "[workgroup_protocol]",
                "You are in a local workgroup chat, not a private chat.",
                "Read the local context file before replying.",
                "Visible workgroup replies must use the configured group reply API.",
                "A group reply payload should include route, room_id, agent_id, parent_msg_id, text, and turn_id when available.",
                "If you are explicitly mentioned, acknowledge within 5 minutes with a first line that restates the task, gives an estimate, and says you are starting.",
                "Use message kinds deliberately: chat, task, review_request, question, or broadcast.",
                "For review comments, start findings with P0, P1, or P2. Use a line starting with ALL_CLEAR only when review is fully clean.",
                "Only mention another agent when handing off or asking for review.",
                "Do not fan out to everyone unless the system explicitly supports it.",
                "If remaining_handoffs is 0, summarize for the human instead of handing off.",
                "[/workgroup_protocol]",
            ]
        )
        context_path = self.write_context_file(request, remaining_hops, protocol)
        return "\n".join(
            [
                header,
                f"Workgroup protocol and recent context are in this local file: {context_path}",
                "Read that file for context, but do not quote or summarize it in your reply.",
                request.text,
            ]
        )

    def write_context_file(self, request: DispatchRequest, remaining_hops: int, protocol: str) -> Path:
        self.trigger_dir.mkdir(parents=True, exist_ok=True)
        path = self.trigger_dir / f"{CONTEXT_PREFIX}{safe_file_stem(request.message_id)}.md"
        payload = "\n".join(
            [
                "# Workgroup Context",
                "",
                f"- message_id: {request.message_id}",
                f"- route: {request.route}",
                f"- room_id: {request.room_id}",
                f"- sender: {request.sender_id}",
                f"- parent_msg_id: {request.parent_msg_id or '(none)'}",
                f"- turn_id: {request.turn_id or '(none)'}",
                f"- mentions: {','.join(request.mentions) if request.mentions else '(none)'}",
                f"- hop_count: {request.hop_count}",
                f"- remaining_handoffs: {remaining_hops}",
                "",
                protocol,
                "",
                "## Recent Context",
                "",
                request.context.strip() if request.context.strip() else "(none)",
                "",
            ]
        )
        path.write_text(payload, encoding="utf-8")
        return path

    def write_trigger(self, agent_id: str, session: str, request: DispatchRequest) -> Path:
        self.trigger_dir.mkdir(parents=True, exist_ok=True)
        path = self.trigger_dir / f"{TRIGGER_PREFIX}{agent_id}_{safe_file_stem(session)}.json"
        payload = {
            "agent_id": agent_id,
            "session": session,
            "message_id": request.message_id,
            "route": request.route,
            "room_id": request.room_id,
            "turn_id": request.turn_id,
            "sender": request.sender_id,
            "mentions": request.mentions,
            "text": request.text,
            "hop_count": request.hop_count,
            "ts": time.time(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path
