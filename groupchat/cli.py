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
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    hist = sub.add_parser("history")
    hist.add_argument("--limit", type=int, default=20)

    send = sub.add_parser("send")
    send.add_argument("text")
    send.add_argument("--sender-id", default="you")
    send.add_argument("--mentions", default="")
    send.add_argument("--message-type", default="chat")
    send.add_argument("--wait", type=float, default=0)

    append = sub.add_parser("append")
    append.add_argument("text")
    append.add_argument("--sender-id", required=True)
    append.add_argument("--mentions", default="")
    append.add_argument("--parent-msg-id", default="")
    append.add_argument("--hop-count", type=int, default=0)

    args = parser.parse_args(argv)
    token = read_token(args.token, args.token_file)

    if args.cmd == "status":
        print(json.dumps(http_json(args.base_url, "GET", "/group/status", token), ensure_ascii=False, indent=2))
    elif args.cmd == "history":
        path = "/group/history?" + parse.urlencode({"limit": args.limit})
        print(json.dumps(http_json(args.base_url, "GET", path, token), ensure_ascii=False, indent=2))
    elif args.cmd == "send":
        body = {
            "sender_id": args.sender_id,
            "text": args.text,
            "mentions": args.mentions,
            "message_type": args.message_type,
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
            "text": args.text,
            "mentions": args.mentions,
            "parent_msg_id": args.parent_msg_id,
            "hop_count": args.hop_count,
            "source": "cli",
        }
        print(json.dumps(http_json(args.base_url, "POST", "/group/append", token, body), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
