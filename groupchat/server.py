from __future__ import annotations

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import logging
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from .auth import auth_matches
from .config import AppConfig, load_config, load_roster
from .dispatcher import DispatchRequest, NullDispatcher
from .presence import AlwaysOfflinePresence
from .store import ALL_TOKEN, GroupChatStore


logger = logging.getLogger("groupchat.server")


class ServerState:
    def __init__(self, config: AppConfig):
        self.config = config
        roster, aliases = load_roster(config.roster_path)
        self.group_chat = GroupChatStore(
            config.jsonl_path,
            config.state_path,
            roster=roster,
            aliases=aliases,
        )
        self.shared_secret = config.auth_token
        self.strict_auth = config.strict_auth
        self.admin_sender_id = config.admin_sender_id
        self.presence = AlwaysOfflinePresence()
        self.dispatcher = NullDispatcher()
        self._load_optional_adapters()
        self._group_dedupe_cache: dict[str, float] = {}

    def _load_optional_adapters(self):
        if self.config.presence == "tmux":
            from adapters.tmux_presence import TmuxPresence

            self.presence = TmuxPresence()
        if self.config.dispatcher == "tmux":
            from adapters.tmux_dispatcher import TmuxDispatcher

            self.dispatcher = TmuxDispatcher.from_roster(self.group_chat.roster())
        if self.config.dispatcher == "webhook":
            from adapters.webhook_dispatcher import WebhookDispatcher

            self.dispatcher = WebhookDispatcher.from_roster(
                self.group_chat.roster(),
                timeout=self.config.webhook_timeout,
            )


class GroupChatHandler(BaseHTTPRequestHandler):
    state: ServerState
    server_version = "GroupChatOSS/0.1"

    def log_message(self, fmt: str, *args: object):
        logger.info("%s %s", self.address_string(), fmt % args)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw)

    def _send_json(self, code: int, payload: dict[str, Any]):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _query(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query)

    def _query_value(self, qs: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
        vals = qs.get(key)
        return vals[0] if vals else default

    def _auth_matches(self) -> bool:
        return auth_matches(self.headers, self.state.shared_secret)

    def _require_auth(self) -> bool:
        if self._auth_matches() or not self.state.strict_auth:
            return True
        self._send_json(401, {"error": "unauthorized"})
        return False

    def do_GET(self):
        route = urlparse(self.path).path
        if route not in {"/health", "/version"} and not self._require_auth():
            return
        if route == "/health":
            self._send_json(200, {"ok": True})
        elif route == "/version":
            self._send_json(200, {"ok": True, "version": self.server_version})
        elif route == "/group/roster":
            self._handle_group_roster()
        elif route == "/group/status":
            self._handle_group_status()
        elif route == "/group/tasks":
            self._handle_group_tasks()
        elif route == "/group/deliveries":
            self._handle_group_deliveries()
        elif route in {"/group/list", "/group/history"}:
            self._handle_group_history()
        elif route == "/group/poll":
            self._handle_group_poll()
        elif route == "/group/stats":
            self._handle_group_stats()
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self._require_auth():
            return
        try:
            body = self._read_body()
        except Exception as e:
            self._send_json(400, {"error": f"bad json: {e}"})
            return
        route = urlparse(self.path).path
        if route == "/group/send":
            self._handle_group_send(body)
        elif route == "/group/append":
            self._handle_group_append(body)
        elif route in {"/group/reply", "/mcp/seashore/group-reply"}:
            self._handle_group_reply(body)
        elif route == "/group/ack":
            self._handle_group_ack(body)
        elif route == "/group/dispatch-state":
            self._handle_group_dispatch_state(body)
        elif route == "/group/typing":
            self._handle_group_typing(body)
        elif route == "/group/delete":
            self._handle_group_delete(body)
        elif route == "/group/clear":
            self._handle_group_clear(body)
        else:
            self._send_json(404, {"error": "not found"})

    def _session_exists(self, session: str) -> bool:
        return self.state.presence.session_exists(session)

    def _group_online_agents(self) -> set[str]:
        online: set[str] = set()
        for member in self.state.group_chat.roster():
            session = member.get("tmux")
            if member.get("can_reply") and session and self._session_exists(str(session)):
                online.add(member["id"])
            if self._webhook_agent_online(member):
                online.add(member["id"])
        return online

    def _webhook_agent_online(self, member: dict[str, Any]) -> bool:
        if not (
            member.get("can_reply")
            and self.state.config.dispatcher == "webhook"
            and member.get("webhook_url")
        ):
            return False
        status_url = str(member.get("webhook_status_url") or "").strip()
        if not status_url:
            return True
        try:
            req = Request(status_url, method="GET")
            with urlopen(req, timeout=self.state.config.webhook_timeout):
                return True
        except HTTPError as exc:
            return 200 <= exc.code < 400
        except (OSError, URLError):
            return False

    def _handle_group_roster(self):
        self._send_json(
            200,
            {
                "ok": True,
                "roster": self.state.group_chat.roster(),
                "status": self.state.group_chat.status_snapshot(self._session_exists),
            },
        )

    def _handle_group_status(self):
        self._send_json(200, {"ok": True, **self.state.group_chat.status_snapshot(self._session_exists)})

    def _handle_group_tasks(self):
        qs = self._query()
        room_id = self._query_value(qs, "room_id")
        self._send_json(200, {"ok": True, **self.state.group_chat.tasks_summary(room_id=room_id)})

    def _handle_group_deliveries(self):
        qs = self._query()
        room_id = self._query_value(qs, "room_id")
        try:
            ack_timeout_seconds = int(self._query_value(qs, "ack_timeout_seconds", "300") or "300")
        except Exception:
            ack_timeout_seconds = 300
        self._send_json(
            200,
            {
                "ok": True,
                **self.state.group_chat.delivery_summary(
                    room_id=room_id,
                    ack_timeout_seconds=max(ack_timeout_seconds, 1),
                ),
            },
        )

    def _handle_group_history(self):
        qs = self._query()
        since = self._query_value(qs, "since")
        before = self._query_value(qs, "before") or self._query_value(qs, "before_ts")
        room_id = self._query_value(qs, "room_id")
        try:
            limit = int(self._query_value(qs, "limit", "100") or "100")
        except Exception:
            limit = 100
        limit = min(max(limit, 1), 1000)
        records = self.state.group_chat.read_since(since_ts=since, before_ts=before, limit=limit, room_id=room_id)
        self._send_json(200, {"ok": True, "records": records, "count": len(records)})

    def _handle_group_poll(self):
        qs = self._query()
        since = self._query_value(qs, "since")
        room_id = self._query_value(qs, "room_id")
        try:
            limit = int(self._query_value(qs, "limit", "100") or "100")
        except Exception:
            limit = 100
        limit = min(max(limit, 1), 500)
        records = self.state.group_chat.read_since(since_ts=since, limit=limit, room_id=room_id)
        self._send_json(
            200,
            {
                "ok": True,
                "records": records,
                "count": len(records),
                "last_ts": records[-1]["ts"] if records else since,
                "status": self.state.group_chat.status_snapshot(self._session_exists),
            },
        )

    def _handle_group_stats(self):
        today = datetime.now().strftime("%Y-%m-%d")
        count = 0
        if self.state.group_chat.path.exists():
            with open(self.state.group_chat.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if str(rec.get("ts", "")).startswith(today):
                        count += 1
        self._send_json(200, {"ok": True, "today_count": count, "path": str(self.state.group_chat.path)})

    def _dedupe(self, key: str, now_ts: float) -> bool:
        cache = self.state._group_dedupe_cache
        last_ts = cache.get(key, 0)
        if now_ts - last_ts < 3.0:
            return False
        cache[key] = now_ts
        for old_key in list(cache.keys()):
            if now_ts - cache[old_key] > 60:
                del cache[old_key]
        return True

    def _handle_group_send(self, body: dict[str, Any]):
        text = str(body.get("text") or "").strip()
        sender_id = str(body.get("sender_id") or self.state.admin_sender_id).strip()
        if not text:
            self._send_json(400, {"error": "text required"})
            return

        client_msg_id = body.get("client_msg_id")
        cache_key = f"cmid:{client_msg_id}" if client_msg_id else f"{sender_id}|{text[:200]}"
        if not self._dedupe(cache_key, time.time()):
            self._send_json(429, {"ok": False, "error": "duplicate within 3s window", "deduped": True})
            return

        hop_count = int(body.get("hop_count", 0) or 0)
        route = str(body.get("route") or "group").strip() or "group"
        room_id = str(body.get("room_id") or "main").strip() or "main"
        turn_id = str(body.get("turn_id") or "").strip() or None
        mentions = self.state.group_chat.normalize_mentions(body.get("mentions"), text)
        parent_msg_id = body.get("parent_msg_id")
        if sender_id in self.state.group_chat.human_ids() and parent_msg_id and not mentions:
            for rec in self.state.group_chat.tail(limit=200):
                if rec.get("id") == parent_msg_id:
                    parent_sender = rec.get("sender_id")
                    if parent_sender in self.state.group_chat.reply_agent_ids():
                        mentions = [str(parent_sender)]
                    break

        targets = self.state.group_chat.targets_for(sender_id, mentions, self._group_online_agents(), hop_count=hop_count)
        dispatch_id = f"dsp_{int(time.time() * 1000)}"
        delivery = {
            "targets": targets,
            "mode": "default" if not mentions else ("all" if ALL_TOKEN in mentions else "mention"),
            "dispatch_id": dispatch_id,
            "delivered": [],
            "failed": [],
        }
        meta = {}
        if client_msg_id:
            meta["client_msg_id"] = client_msg_id
        message_type = str(body.get("message_type") or "chat").strip().lower()
        owner = str(body.get("owner") or "").strip() or self._infer_group_task_owner(body, mentions)
        priority = str(body.get("priority") or "").strip() or None
        try:
            rec = self.state.group_chat.append(
                sender_id,
                text,
                source=str(body.get("source") or "api"),
                route=route,
                room_id=room_id,
                mentions=mentions,
                parent_msg_id=parent_msg_id or None,
                reply_to=body.get("reply_to") or None,
                delivery=delivery,
                meta=meta,
                turn_id=turn_id,
                message_type=message_type,
                task_id=str(body.get("task_id") or "").strip() or None,
                parent_task_id=str(body.get("parent_task_id") or "").strip() or None,
                owner=owner,
                priority=priority,
            )
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return

        if targets:
            self._dispatch(sender_id, text, rec["id"], str(parent_msg_id or ""), turn_id or "", mentions, targets, hop_count, dispatch_id, route=route, room_id=room_id)
        self._send_json(200, {"ok": True, "record": rec, "targets": targets})

    def _handle_group_append(self, body: dict[str, Any]):
        text = str(body.get("text") or "").strip()
        sender_id = str(body.get("sender_id") or body.get("agent_id") or "").strip()
        if not sender_id:
            self._send_json(400, {"error": "sender_id required"})
            return
        if not text:
            self._send_json(400, {"error": "text required"})
            return
        if not self._dedupe(f"{sender_id}|{text[:200]}", time.time()):
            self._send_json(429, {"ok": False, "error": "duplicate within 3s window", "deduped": True})
            return

        mentions = self.state.group_chat.normalize_mentions(body.get("mentions"), text)
        route = str(body.get("route") or "group").strip() or "group"
        room_id = str(body.get("room_id") or "main").strip() or "main"
        turn_id = str(body.get("turn_id") or "").strip() or None
        message_type = str(body.get("message_type") or "chat").strip().lower()
        owner = str(body.get("owner") or "").strip() or self._infer_group_task_owner(body, mentions)
        priority = str(body.get("priority") or "").strip() or None
        hop_count = int(body.get("hop_count", 0) or 0)
        targets = self.state.group_chat.targets_for(sender_id, mentions, self._group_online_agents(), hop_count=hop_count)
        try:
            rec = self.state.group_chat.append(
                sender_id,
                text,
                source=str(body.get("source") or f"agent:{sender_id}"),
                route=route,
                room_id=room_id,
                mentions=mentions,
                parent_msg_id=body.get("parent_msg_id") or None,
                reply_to=body.get("reply_to") or None,
                delivery={"targets": targets, "delivered": [], "failed": []},
                meta={"loop_depth": hop_count},
                turn_id=turn_id,
                bubble_index=self._optional_int(body.get("bubble_index")),
                bubble_count=self._optional_int(body.get("bubble_count")),
                message_type=message_type,
                task_id=str(body.get("task_id") or "").strip() or None,
                parent_task_id=str(body.get("parent_task_id") or "").strip() or None,
                owner=owner,
                priority=priority,
            )
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        self.state.group_chat.set_typing(sender_id, False)
        if targets:
            dispatch_id = f"dsp_{int(time.time() * 1000)}"
            self._dispatch(sender_id, text, rec["id"], str(body.get("parent_msg_id") or ""), turn_id or "", mentions, targets, hop_count, dispatch_id, route=route, room_id=room_id)
        self._send_json(200, {"ok": True, "record": rec, "targets": targets})

    def _handle_group_reply(self, body: dict[str, Any]):
        route = str(body.get("route") or "group").strip()
        room_id = str(body.get("room_id") or "").strip()
        agent_id = str(body.get("agent_id") or body.get("sender_id") or "").strip()
        parent_msg_id = str(body.get("parent_msg_id") or "").strip()
        turn_id = str(body.get("turn_id") or "").strip()
        text = str(body.get("text") or "").strip()
        if route != "group":
            self._send_json(400, {"ok": False, "error": "route must be group"})
            return
        missing = [name for name, value in {
            "room_id": room_id,
            "agent_id": agent_id,
            "parent_msg_id": parent_msg_id,
            "turn_id": turn_id,
            "text": text,
        }.items() if not value]
        if missing:
            self._send_json(400, {"ok": False, "error": f"missing required field(s): {', '.join(missing)}"})
            return
        payload = dict(body)
        payload.update({
            "sender_id": agent_id,
            "route": route,
            "room_id": room_id,
            "parent_msg_id": parent_msg_id,
            "turn_id": turn_id,
            "text": text,
            "source": body.get("source") or f"group-reply:{agent_id}",
        })
        self._handle_group_append(payload)

    def _handle_group_ack(self, body: dict[str, Any]):
        agent_id = str(body.get("agent_id") or body.get("sender_id") or "").strip()
        parent_msg_id = str(body.get("parent_msg_id") or "").strip()
        room_id = str(body.get("room_id") or "main").strip() or "main"
        turn_id = str(body.get("turn_id") or "").strip() or None
        if not agent_id or not parent_msg_id:
            self._send_json(400, {"ok": False, "error": "agent_id and parent_msg_id required"})
            return
        payload = {
            "sender_id": agent_id,
            "route": "group",
            "room_id": room_id,
            "parent_msg_id": parent_msg_id,
            "turn_id": turn_id,
            "text": str(body.get("text") or "ACK").strip() or "ACK",
            "message_type": "ack",
            "source": body.get("source") or f"ack:{agent_id}",
        }
        self._handle_group_append(payload)

    def _optional_int(self, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _dispatch(
        self,
        sender_id: str,
        text: str,
        message_id: str,
        parent_msg_id: str,
        turn_id: str,
        mentions: list[str],
        targets: list[str],
        hop_count: int,
        dispatch_id: str,
        *,
        route: str = "group",
        room_id: str = "main",
    ):
        context = "\n".join(self.state.group_chat.context_lines(limit=20, room_id=room_id))
        for agent_id in targets:
            self.state.group_chat.set_typing(agent_id, True, dispatch_id=dispatch_id)
        delivered, failed = self.state.dispatcher.dispatch(
            DispatchRequest(
                source="group",
                route=route,
                room_id=room_id,
                sender_id=sender_id,
                text=text,
                message_id=message_id,
                parent_msg_id=parent_msg_id,
                turn_id=turn_id,
                mentions=mentions,
                targets=targets,
                context=context,
                hop_count=hop_count + 1,
            )
        )
        for agent_id in failed:
            self.state.group_chat.set_typing(agent_id, False, dispatch_id=dispatch_id)
        return delivered, failed

    def _infer_group_task_owner(self, body: dict[str, Any], mentions: list[str]) -> str | None:
        assignee = body.get("assignee") or body.get("assigned_to")
        if assignee:
            return str(assignee).strip()
        reply_agents = set(self.state.group_chat.reply_agent_ids())
        for agent_id in mentions:
            if agent_id in reply_agents:
                return agent_id
        return None

    def _handle_group_delete(self, body: dict[str, Any]):
        msg_id = str(body.get("id") or "").strip()
        if not msg_id:
            self._send_json(400, {"error": "id required"})
            return
        ok = self.state.group_chat.delete(msg_id)
        self._send_json(200, {"ok": ok, "id": msg_id})

    def _handle_group_clear(self, body: dict[str, Any]):
        sender_id = str(body.get("sender_id") or "").strip()
        if sender_id != self.state.admin_sender_id:
            self._send_json(403, {"error": "only admin_sender_id can clear group"})
            return
        try:
            jsonl = self.state.group_chat.path
            backup = None
            if jsonl.exists():
                ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
                bak = jsonl.with_suffix(jsonl.suffix + f".bak.user-clear.{ts_tag}")
                bak.write_bytes(jsonl.read_bytes())
                jsonl.write_text("", encoding="utf-8")
                self.state.group_chat._last_ts = ""
                backup = str(bak)
            self._send_json(200, {"ok": True, "cleared": True, "backup": backup})
        except Exception as e:
            self._send_json(500, {"ok": False, "error": str(e)})

    def _handle_group_dispatch_state(self, body: dict[str, Any]):
        agent_id = str(body.get("agent_id") or "").strip()
        if not agent_id:
            self._send_json(400, {"error": "agent_id required"})
            return
        self.state.group_chat.set_typing(agent_id, bool(body.get("is_typing")), dispatch_id=body.get("dispatch_id") or None)
        self._send_json(200, {"ok": True, "status": self.state.group_chat.status_snapshot(self._session_exists)})

    def _handle_group_typing(self, body: dict[str, Any]):
        agent_id = str(body.get("sender_id") or body.get("agent_id") or "").strip()
        if not agent_id:
            self._send_json(400, {"error": "sender_id required"})
            return
        if "status_text" in body:
            status_text = body.get("status_text")
            status_text = "" if status_text is None else str(status_text)
        else:
            status_text = None
        self.state.group_chat.set_typing(
            agent_id,
            bool(body.get("is_typing")),
            dispatch_id=body.get("dispatch_id") or None,
            status_text=status_text,
        )
        self._send_json(200, {"ok": True})


def run_server(config: AppConfig):
    GroupChatHandler.state = ServerState(config)
    httpd = ThreadingHTTPServer((config.host, config.port), GroupChatHandler)
    logger.info("groupchat server listening on http://%s:%s", config.host, config.port)
    httpd.serve_forever()


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.example.toml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_server(load_config(args.config))


if __name__ == "__main__":
    main()
