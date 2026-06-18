from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any
from urllib import parse, request


def read_token(token: str | None, token_file: str | None) -> str:
    if token:
        return token
    if token_file:
        path = Path(token_file).expanduser()
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return ""


def http_json(base_url: str, method: str, path: str, token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(base_url.rstrip("/") + path, data=data, method=method, headers=headers)
    with request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="groupchat")
    parser.add_argument("--base-url", default="http://127.0.0.1:8795")
    parser.add_argument("--token")
    parser.add_argument("--token-file", default="~/.groupchat/token")
    parser.add_argument("--room-id", default="main")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("tasks")
    deliveries = sub.add_parser("deliveries")
    deliveries.add_argument("--ack-timeout-seconds", type=int, default=300)
    hist = sub.add_parser("history")
    hist.add_argument("--limit", type=int, default=20)
    thread = sub.add_parser("thread")
    thread.add_argument("task_id")

    send = sub.add_parser("send")
    send.add_argument("text")
    send.add_argument("--sender-id", default="you")
    send.add_argument("--mentions", default="")
    send.add_argument("--message-type", default="chat")
    send.add_argument("--kind", default="")
    send.add_argument("--turn-id", default="")
    send.add_argument("--priority", default="")
    send.add_argument("--task-id", default="")
    send.add_argument("--parent-task-id", default="")
    send.add_argument("--owner", default="")
    send.add_argument("--wait", type=float, default=0)

    append = sub.add_parser("append")
    append.add_argument("text")
    append.add_argument("--sender-id", required=True)
    append.add_argument("--mentions", default="")
    append.add_argument("--parent-msg-id", default="")
    append.add_argument("--turn-id", default="")
    append.add_argument("--message-type", default="chat")
    append.add_argument("--kind", default="")
    append.add_argument("--priority", default="")
    append.add_argument("--task-id", default="")
    append.add_argument("--parent-task-id", default="")
    append.add_argument("--owner", default="")
    append.add_argument("--hop-count", type=int, default=0)

    reply = sub.add_parser("reply")
    reply.add_argument("text")
    reply.add_argument("--agent-id", required=True)
    reply.add_argument("--parent-msg-id", required=True)
    reply.add_argument("--turn-id", required=True)
    reply.add_argument("--bubble-index", type=int)
    reply.add_argument("--bubble-count", type=int)

    ack = sub.add_parser("ack")
    ack.add_argument("--agent-id", required=True)
    ack.add_argument("--parent-msg-id", required=True)
    ack.add_argument("--turn-id", default="")
    ack.add_argument("--text", default="ACK")

    comment = sub.add_parser("comment")
    comment.add_argument("task_id")
    comment.add_argument("text")
    comment.add_argument("--sender-id", required=True)
    comment.add_argument("--message-type", default="")
    comment.add_argument("--severity", choices=["P0", "P1", "P2", "p0", "p1", "p2"], default="")

    args = parser.parse_args(argv)
    token = read_token(args.token, args.token_file)

    if args.cmd == "status":
        print(json.dumps(http_json(args.base_url, "GET", "/group/status", token), ensure_ascii=False, indent=2))
    elif args.cmd == "tasks":
        path = "/group/tasks?" + parse.urlencode({"room_id": args.room_id})
        print(json.dumps(http_json(args.base_url, "GET", path, token), ensure_ascii=False, indent=2))
    elif args.cmd == "deliveries":
        path = "/group/deliveries?" + parse.urlencode({
            "room_id": args.room_id,
            "ack_timeout_seconds": args.ack_timeout_seconds,
        })
        print(json.dumps(http_json(args.base_url, "GET", path, token), ensure_ascii=False, indent=2))
    elif args.cmd == "history":
        path = "/group/history?" + parse.urlencode({"limit": args.limit, "room_id": args.room_id})
        print(json.dumps(http_json(args.base_url, "GET", path, token), ensure_ascii=False, indent=2))
    elif args.cmd == "thread":
        print(json.dumps(http_json(args.base_url, "GET", f"/group/thread/{parse.quote(args.task_id)}", token), ensure_ascii=False, indent=2))
    elif args.cmd == "send":
        body = {
            "sender_id": args.sender_id,
            "route": "group",
            "room_id": args.room_id,
            "text": args.text,
            "mentions": args.mentions,
            "message_type": args.kind or args.message_type,
            "turn_id": args.turn_id,
            "priority": args.priority,
            "task_id": args.task_id,
            "parent_task_id": args.parent_task_id,
            "owner": args.owner,
            "source": "cli",
        }
        res = http_json(args.base_url, "POST", "/group/send", token, body)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if args.wait > 0:
            since = res.get("record", {}).get("ts", "")
            deadline = time.time() + args.wait
            while time.time() < deadline:
                time.sleep(1)
                poll = http_json(args.base_url, "GET", "/group/poll?" + parse.urlencode({"since": since}), token)
                records = poll.get("records") or []
                if records:
                    print(json.dumps(poll, ensure_ascii=False, indent=2))
                    return
            print("wait timed out", file=sys.stderr)
    elif args.cmd == "append":
        body = {
            "sender_id": args.sender_id,
            "route": "group",
            "room_id": args.room_id,
            "text": args.text,
            "mentions": args.mentions,
            "parent_msg_id": args.parent_msg_id,
            "turn_id": args.turn_id,
            "message_type": args.kind or args.message_type,
            "priority": args.priority,
            "task_id": args.task_id,
            "parent_task_id": args.parent_task_id,
            "owner": args.owner,
            "hop_count": args.hop_count,
            "source": "cli",
        }
        print(json.dumps(http_json(args.base_url, "POST", "/group/append", token, body), ensure_ascii=False, indent=2))
    elif args.cmd == "reply":
        body = {
            "route": "group",
            "room_id": args.room_id,
            "agent_id": args.agent_id,
            "parent_msg_id": args.parent_msg_id,
            "turn_id": args.turn_id,
            "text": args.text,
        }
        if args.bubble_index is not None:
            body["bubble_index"] = args.bubble_index
        if args.bubble_count is not None:
            body["bubble_count"] = args.bubble_count
        print(json.dumps(http_json(args.base_url, "POST", "/group/reply", token, body), ensure_ascii=False, indent=2))
    elif args.cmd == "ack":
        body = {
            "room_id": args.room_id,
            "agent_id": args.agent_id,
            "parent_msg_id": args.parent_msg_id,
            "turn_id": args.turn_id,
            "text": args.text,
        }
        print(json.dumps(http_json(args.base_url, "POST", "/group/ack", token, body), ensure_ascii=False, indent=2))
    elif args.cmd == "comment":
        meta: dict[str, Any] = {}
        if args.severity:
            meta["severity"] = args.severity.upper()
        body = {
            "room_id": args.room_id,
            "sender_id": args.sender_id,
            "text": args.text,
            "message_type": args.message_type,
            "meta": meta,
        }
        print(json.dumps(http_json(args.base_url, "POST", f"/group/thread/{parse.quote(args.task_id)}/comment", token, body), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
