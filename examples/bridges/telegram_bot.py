#!/usr/bin/env python3
"""Minimal Telegram long-polling bridge for groupchat-oss.

Environment:
  TELEGRAM_BOT_TOKEN=123:abc
  GROUPCHAT_AUTH_TOKEN=change-me
  GROUPCHAT_SERVER_URL=http://127.0.0.1:8795
  GROUPCHAT_ROOM_ID=casual

Commands in Telegram:
  /work message @assistant-a
  /code review this @assistant-a
  /task fix deploy @assistant-a
  /review inspect patch @assistant-a
  /question what is current status @assistant-a
  /broadcast release is live
"""
from __future__ import annotations

import json
import os
import time
from urllib import parse, request


TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROUPCHAT_TOKEN = os.environ.get("GROUPCHAT_AUTH_TOKEN", "")
GROUPCHAT_SERVER_URL = os.environ.get("GROUPCHAT_SERVER_URL", "http://127.0.0.1:8795")
DEFAULT_ROOM = os.environ.get("GROUPCHAT_ROOM_ID", "casual")


def urlopen_json(url: str, body: dict | None = None, headers: dict | None = None) -> dict:
    data = None
    method = "GET"
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        method = "POST"
    req = request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def tg(method: str, payload: dict | None = None) -> dict:
    return urlopen_json(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", payload)


def send_groupchat(text: str, *, sender_id: str, room_id: str, kind: str) -> dict:
    payload = {
        "sender_id": sender_id,
        "route": "group",
        "room_id": room_id,
        "text": text,
        "message_type": kind,
        "kind": kind,
        "source": "telegram",
    }
    return urlopen_json(
        GROUPCHAT_SERVER_URL.rstrip("/") + "/group/send",
        payload,
        headers={"X-Auth-Token": GROUPCHAT_TOKEN},
    )


def classify(text: str) -> tuple[str, str, str]:
    raw = text.strip()
    lower = raw.lower()
    room_id = DEFAULT_ROOM
    kind = "chat"
    prefixes = {
        "/work ": ("work", "chat"),
        "/code ": ("code", "chat"),
        "/task ": ("work", "task"),
        "/review ": ("code", "review_request"),
        "/question ": (DEFAULT_ROOM, "question"),
        "/broadcast ": ("work", "broadcast"),
    }
    for prefix, (room, msg_kind) in prefixes.items():
        if lower.startswith(prefix):
            return raw[len(prefix) :].strip(), room, msg_kind
    return raw, room_id, kind


def main() -> int:
    offset = 0
    print("telegram_bot bridge running")
    while True:
        updates = tg("getUpdates?" + parse.urlencode({"timeout": 25, "offset": offset}))
        for item in updates.get("result", []):
            offset = max(offset, int(item.get("update_id", 0)) + 1)
            msg = item.get("message") or item.get("edited_message") or {}
            text = str(msg.get("text") or "").strip()
            if not text:
                continue
            chat = msg.get("chat") or {}
            user = msg.get("from") or {}
            sender_id = f"tg_{user.get('id') or chat.get('id')}"
            clean_text, room_id, kind = classify(text)
            if not clean_text:
                continue
            try:
                res = send_groupchat(clean_text, sender_id=sender_id, room_id=room_id, kind=kind)
                rec = res.get("record") or {}
                tg("sendMessage", {"chat_id": chat.get("id"), "text": f"sent {kind} to {room_id}: {rec.get('id')}"})
            except Exception as exc:
                tg("sendMessage", {"chat_id": chat.get("id"), "text": f"groupchat bridge error: {exc}"})
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
