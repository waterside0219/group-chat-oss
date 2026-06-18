from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from groupchat.dispatcher import DispatchRequest


logger = logging.getLogger("groupchat.webhook")


def safe_webhook_url(url: str) -> str:
    """Return a log-safe URL without userinfo, query string, or fragment."""
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


class WebhookDispatcher:
    """POST workgroup messages to agent-owned webhook URLs."""

    def __init__(self, agents: dict[str, dict[str, str]], *, timeout: float = 5.0):
        self.agents = agents
        self.timeout = timeout

    @classmethod
    def from_roster(cls, roster: list[dict[str, Any]], *, timeout: float = 5.0) -> "WebhookDispatcher":
        agents: dict[str, dict[str, str]] = {}
        for member in roster:
            member_id = str(member.get("id") or "").strip()
            webhook_url = str(member.get("webhook_url") or "").strip()
            if member_id and webhook_url and member.get("can_reply"):
                agents[member_id] = {
                    "url": webhook_url,
                    "secret": str(member.get("webhook_secret") or "").strip(),
                }
        return cls(agents, timeout=timeout)

    def dispatch(self, request: DispatchRequest) -> tuple[list[str], list[str]]:
        delivered: list[str] = []
        failed: list[str] = []
        for agent_id in request.targets:
            agent = self.agents.get(agent_id)
            if not agent:
                failed.append(agent_id)
                continue
            try:
                self._post(agent_id, agent, request)
                delivered.append(agent_id)
            except Exception as exc:
                failed.append(agent_id)
                logger.warning(
                    "webhook dispatch failed agent_id=%s url=%s error=%s",
                    agent_id,
                    safe_webhook_url(agent.get("url", "")),
                    exc,
                )
        return delivered, failed

    def _post(self, agent_id: str, agent: dict[str, str], request: DispatchRequest):
        payload = {
            "event": "group.message",
            "target_agent_id": agent_id,
            "message": {
                "id": request.message_id,
                "route": request.route,
                "room_id": request.room_id,
                "sender_id": request.sender_id,
                "text": request.text,
                "parent_msg_id": request.parent_msg_id or None,
                "turn_id": request.turn_id or None,
                "mentions": request.mentions,
                "source": request.source,
            },
            "dispatch": {
                "targets": request.targets,
                "hop_count": request.hop_count,
                "context": request.context,
            },
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "groupchat-oss-webhook/0.1",
        }
        secret = agent.get("secret")
        if secret:
            headers["X-GroupChat-Webhook-Token"] = secret
        req = Request(agent["url"], data=data, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.timeout):
                pass
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(str(exc.reason)) from exc
