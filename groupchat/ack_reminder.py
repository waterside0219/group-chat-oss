from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from .config import load_config, load_roster
from .store import GroupChatStore, _parse_iso


REMINDER_INTERVAL_SECONDS = 300


def build_store(config_path: str) -> GroupChatStore:
    config = load_config(config_path)
    roster, aliases = load_roster(config.roster_path)
    return GroupChatStore(config.jsonl_path, config.state_path, roster=roster, aliases=aliases)


def due_items(store: GroupChatStore, *, room_id: str | None, ack_timeout_seconds: int, now: datetime) -> list[dict[str, Any]]:
    summary = store.delivery_summary(room_id=room_id, ack_timeout_seconds=ack_timeout_seconds, now=now)
    items: list[dict[str, Any]] = []
    for item in summary.get("overdue", []):
        reminded_at = _parse_iso(str(item.get("reminded_at") or ""))
        if reminded_at and (now - reminded_at).total_seconds() < REMINDER_INTERVAL_SECONDS:
            continue
        items.append(item)
    return items


def reminder_text(item: dict[str, Any]) -> str:
    age = item.get("age_seconds")
    age_text = f"{age}s" if age is not None else "unknown age"
    return (
        f"ACK reminder: message {item.get('message_id')} has not been acknowledged "
        f"by @{item.get('target_agent_id')} after {age_text}."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find overdue groupchat ACKs and optionally append reminders.")
    parser.add_argument("--config", default="config.example.toml")
    parser.add_argument("--room-id", default=None)
    parser.add_argument("--ack-timeout-seconds", type=int, default=300)
    parser.add_argument("--apply", action="store_true", help="append reminder records and mark reminded_at")
    parser.add_argument("--sender-id", default="you")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    store = build_store(args.config)
    now = datetime.now(timezone.utc).astimezone()
    items = due_items(store, room_id=args.room_id, ack_timeout_seconds=max(args.ack_timeout_seconds, 1), now=now)

    reminders: list[dict[str, Any]] = []
    for item in items:
        text = reminder_text(item)
        record = None
        if args.apply:
            record = store.append(
                args.sender_id,
                text,
                room_id=str(item.get("room_id") or args.room_id or "main"),
                parent_msg_id=str(item.get("message_id") or ""),
                mentions=[str(item.get("target_agent_id"))],
                message_type="progress",
                source="ack-reminder",
                meta={"ack_reminder": True, "target_agent_id": item.get("target_agent_id")},
            )
            store.mark_reminded(str(item.get("message_id")), str(item.get("target_agent_id")), now=now)
        reminders.append({"item": item, "text": text, "record": record})

    payload = {"ok": True, "apply": args.apply, "count": len(reminders), "reminders": reminders}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for reminder in reminders:
            print(reminder["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
