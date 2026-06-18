#!/usr/bin/env python3
"""Small command-line bridge for humans, custom frontends, and bots.

This mirrors the shape used by a private workgroup deployment while staying
neutral: no private member names, paths, or tokens are baked in.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from urllib import parse, request


DEFAULT_SERVER = os.environ.get("GROUPCHAT_SERVER_URL", "http://127.0.0.1:8795")
DEFAULT_TOKEN_FILE = os.environ.get("GROUPCHAT_TOKEN_FILE", "~/.groupchat/token")
MESSAGE_TYPE_CHOICES = [
    "chat",
    "task",
    "review_request",
    "question",
    "broadcast",
    "progress",
    "decision",
    "ship",
    "block",
    "review_clear",
    "ack",
]


def auth_token(explicit: str = "", token_file: str = DEFAULT_TOKEN_FILE) -> str:
    if explicit:
        return explicit
    env = os.environ.get("GROUPCHAT_AUTH_TOKEN", "")
    if env:
        return env
    path = Path(token_file).expanduser()
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def http_json(method: str, path: str, token: str, body: dict | None = None, server: str = DEFAULT_SERVER) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(server.rstrip("/") + path, data=data, method=method, headers=headers)
    with request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def print_messages(records: list[dict]) -> None:
    for rec in records:
        ts = str(rec.get("ts") or "")[11:16]
        sender = rec.get("sender_id") or "?"
        kind = rec.get("message_type") or "chat"
        text = str(rec.get("text") or "").replace("\n", " ").strip()
        if len(text) > 500:
            text = text[:497] + "..."
        print(f"[{ts}] {sender} ({kind}): {text}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge a frontend, bot, or shell into groupchat-oss")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--token", default="")
    parser.add_argument("--token-file", default=DEFAULT_TOKEN_FILE)
    parser.add_argument("--room-id", default="work")
    sub = parser.add_subparsers(dest="cmd", required=True)

    history = sub.add_parser("history", help="print recent messages")
    history.add_argument("--limit", type=int, default=20)

    status = sub.add_parser("status", help="print agent status and ACK state")

    send = sub.add_parser("send", help="send a message")
    send.add_argument("text")
    send.add_argument("--mentions", default="", help="comma-separated agent ids, e.g. assistant47,assistant46")
    send.add_argument("--sender-id", default="you")
    send.add_argument("--kind", "--message-type", dest="message_type", default="chat", choices=MESSAGE_TYPE_CHOICES)
    send.add_argument("--task-id", default="")
    send.add_argument("--parent-task-id", default="")
    send.add_argument("--owner", default="")
    send.add_argument("--priority", default="")
    send.add_argument("--wait", type=float, default=0, help="seconds to wait for direct replies")

    args = parser.parse_args()
    token = auth_token(args.token, args.token_file)

    if args.cmd == "history":
        qs = parse.urlencode({"limit": args.limit, "room_id": args.room_id})
        data = http_json("GET", f"/group/history?{qs}", token, server=args.server)
        print_messages(data.get("records") or data.get("messages") or [])
        return 0

    if args.cmd == "status":
        data = http_json("GET", "/group/status", token, server=args.server)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        deliveries = http_json("GET", "/group/deliveries?" + parse.urlencode({"room_id": args.room_id}), token, server=args.server)
        print(json.dumps({"deliveries": deliveries.get("counts")}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "send":
        mentions = [m.strip() for m in args.mentions.split(",") if m.strip()]
        payload = {
            "sender_id": args.sender_id,
            "route": "group",
            "room_id": args.room_id,
            "text": args.text,
            "mentions": mentions,
            "source": "bridge",
            "client_msg_id": f"bridge-{int(time.time() * 1000)}",
            "message_type": args.message_type,
            "kind": args.message_type,
            "task_id": args.task_id.strip() or None,
            "parent_task_id": args.parent_task_id.strip() or None,
            "owner": args.owner.strip() or None,
            "priority": args.priority.strip() or None,
        }
        data = http_json("POST", "/group/send", token, payload, server=args.server)
        rec = data.get("record") or {}
        print(f"sent id={rec.get('id')} task_id={rec.get('task_id')} targets={data.get('targets')}")
        if args.wait > 0 and rec.get("id"):
            deadline = time.time() + args.wait
            seen: set[str] = set()
            while time.time() < deadline:
                poll = http_json("GET", "/group/poll?" + parse.urlencode({"room_id": args.room_id, "limit": 30}), token, server=args.server)
                replies = [
                    item for item in poll.get("records", [])
                    if item.get("parent_msg_id") == rec.get("id") and item.get("id") not in seen
                ]
                if replies:
                    print_messages(replies)
                    return 0
                time.sleep(2)
            print("no direct replies before timeout")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
