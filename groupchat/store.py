from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import threading
import time
import uuid
from typing import Any, Callable

from .config import DEFAULT_MEMBERS, default_aliases


ALL_TOKEN = "__all__"
GROUP_KINDS = {"chat", "task", "review_request", "question", "broadcast"}
MESSAGE_TYPES = {
    *GROUP_KINDS,
    "decision",
    "ship",
    "block",
    "progress",
    "ack",
    "review_clear",
}
THREAD_START_TYPES = {"task", "review_request"}
TASK_STATUS_BY_MESSAGE_TYPE = {
    "task": "open",
    "review_request": "waiting_review",
    "progress": "in-progress",
    "ship": "done",
    "block": "blocked",
    "review_clear": "resolved",
}
PRIORITIES = {"p0", "p1", "p2"}
PRIORITY_RANK = {"p0": 0, "p1": 1, "p2": 2, None: 3}
MENTION_RE = re.compile(
    r"@([A-Za-z][A-Za-z0-9_.-]*(?:\s*\d+(?:[._]\d+)?)?|[\u4e00-\u9fff]+(?:\s*(?:[A-Za-z]|\d+(?:[._]\d+)?))?)"
)
DEFAULT_ACK_TIMEOUT_SECONDS = 300


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _dedupe_ordered(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _task_id() -> str:
    return f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def _normalize_priority(priority: Any, *, required_for_task: bool = False) -> str | None:
    if priority is None or priority == "":
        return "p1" if required_for_task else None
    value = str(priority).strip().lower()
    if value not in PRIORITIES:
        raise ValueError(f"bad priority: {priority}")
    return value


class GroupChatStore:
    def __init__(
        self,
        jsonl_path: str | Path,
        state_path: str | Path | None = None,
        *,
        roster: list[dict[str, Any]] | None = None,
        aliases: dict[str, str] | None = None,
    ):
        self.path = Path(jsonl_path).expanduser()
        self.state_path = Path(state_path).expanduser() if state_path else None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._roster = [dict(m) for m in (roster or DEFAULT_MEMBERS)]
        self._roster_by_id = {m["id"]: m for m in self._roster}
        self._reply_agent_ids = [m["id"] for m in self._roster if m.get("can_reply")]
        self._human_ids = {m["id"] for m in self._roster if m.get("kind") == "human"}
        self._agent_ids = {m["id"] for m in self._roster if m.get("kind") == "agent"}
        self._aliases = aliases or default_aliases(self._roster)
        self._default_responder = self._pick_default_responder()
        self._lock = threading.Lock()
        self._state = self._load_state()
        self._last_ts = str(self._state.get("last_ts") or "")

    def _pick_default_responder(self) -> str | None:
        for member in self._roster:
            if member.get("can_reply") and member.get("default_responder"):
                return str(member["id"])
        return self._reply_agent_ids[0] if self._reply_agent_ids else None

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path or not self.state_path.exists():
            return {"agents": {}}
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"agents": {}}

    def _save_state(self):
        if not self.state_path:
            return
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)
        tmp.replace(self.state_path)

    def _next_ts(self) -> str:
        ts = _now_iso()
        prev = _parse_iso(self._last_ts) if self._last_ts else None
        if prev and ts <= self._last_ts:
            ts = (prev + timedelta(milliseconds=1)).isoformat(timespec="milliseconds")
        self._last_ts = ts
        self._state["last_ts"] = ts
        return ts

    def roster(self) -> list[dict[str, Any]]:
        return [dict(m) for m in self._roster]

    def member(self, member_id: str) -> dict[str, Any] | None:
        member = self._roster_by_id.get(member_id)
        return dict(member) if member else None

    def reply_agent_ids(self) -> list[str]:
        return list(self._reply_agent_ids)

    def human_ids(self) -> set[str]:
        return set(self._human_ids)

    def normalize_mentions(self, mentions: Any = None, text: str | None = None) -> list[str]:
        raw: list[str] = []
        if isinstance(mentions, str):
            raw.extend([m.strip() for m in mentions.split(",") if m.strip()])
        elif isinstance(mentions, list):
            raw.extend(str(m).strip() for m in mentions if str(m).strip())
        if text:
            raw.extend(m.group(1).strip() for m in MENTION_RE.finditer(text))

        normalized: list[str] = []
        for item in raw:
            key = item.strip().lstrip("@").lower()
            member_id = self._aliases.get(key)
            if member_id:
                normalized.append(member_id)
        return _dedupe_ordered(normalized)

    def targets_for(
        self,
        sender_id: str,
        mentions: list[str],
        online_agents: set[str] | None = None,
        hop_count: int = 0,
    ) -> list[str]:
        if sender_id in self._human_ids:
            if not mentions and self._default_responder:
                mentions = [self._default_responder]
            if ALL_TOKEN in mentions:
                candidates = self._reply_agent_ids
            else:
                candidates = [m for m in mentions if m in self._reply_agent_ids]
        else:
            if hop_count >= 3:
                return []
            if not mentions or ALL_TOKEN in mentions:
                return []
            candidates = [m for m in mentions if m in self._reply_agent_ids and m != sender_id]
        if online_agents is not None:
            candidates = [m for m in candidates if m in online_agents]
        return _dedupe_ordered(candidates)

    def append(
        self,
        sender_id: str,
        text: str,
        *,
        source: str = "group",
        route: str = "group",
        room_id: str = "main",
        mentions: list[str] | None = None,
        parent_msg_id: str | None = None,
        reply_to: str | None = None,
        delivery: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        conversation_id: str = "workgroup",
        turn_id: str | None = None,
        bubble_index: int | None = None,
        bubble_count: int | None = None,
        message_type: str = "chat",
        task_id: str | None = None,
        parent_task_id: str | None = None,
        owner: str | None = None,
        priority: str | None = None,
    ) -> dict[str, Any]:
        member = self._roster_by_id.get(sender_id)
        if not member:
            raise ValueError(f"unknown sender_id: {sender_id}")
        text = str(text or "").strip()
        if not text:
            raise ValueError("text required")
        message_type = str(message_type or "chat").strip().lower()
        if message_type not in MESSAGE_TYPES:
            raise ValueError(f"bad message_type: {message_type}")
        if message_type in THREAD_START_TYPES and not task_id:
            task_id = _task_id()
        priority = _normalize_priority(priority, required_for_task=message_type == "task")
        record = {
            "id": f"grp_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
            "ts": "",
            "conversation_id": conversation_id,
            "route": route,
            "room_id": room_id,
            "sender_id": sender_id,
            "sender_model": member.get("model"),
            "text": text,
            "mentions": mentions or [],
            "parent_msg_id": parent_msg_id,
            "reply_to": reply_to,
            "turn_id": turn_id,
            "bubble_index": bubble_index,
            "bubble_count": bubble_count,
            "source": source,
            "delivery": delivery or {},
            "meta": meta or {},
            "message_type": message_type,
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "owner": owner,
            "priority": priority,
        }
        with self._lock:
            ts = self._next_ts()
            record["ts"] = ts
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            if member.get("can_reply"):
                self.set_typing(sender_id, False, save=False)
                self._state.setdefault("agents", {}).setdefault(sender_id, {})["last_seen"] = ts
            self._save_state()
        return record

    def _normalize_record(self, rec: dict[str, Any]) -> dict[str, Any]:
        out = dict(rec)
        msg_type = str(out.get("message_type") or "chat").strip().lower()
        out["message_type"] = msg_type if msg_type in MESSAGE_TYPES else "chat"
        out.setdefault("route", "group")
        out.setdefault("room_id", "main")
        out.setdefault("turn_id", None)
        out.setdefault("bubble_index", None)
        out.setdefault("bubble_count", None)
        out.setdefault("task_id", None)
        out.setdefault("parent_task_id", None)
        out.setdefault("owner", None)
        out.setdefault("acked_by", [])
        out.setdefault("acked_at", None)
        out.setdefault("ack_deadline", None)
        out.setdefault("ack_deadline_seconds", None)
        out.setdefault("escalate_to", None)
        out.setdefault("escalation_state", None)
        try:
            out["priority"] = _normalize_priority(out.get("priority"))
        except ValueError:
            out["priority"] = None
        return out

    def _iter_records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(self._normalize_record(json.loads(line)))
                except json.JSONDecodeError:
                    continue
        return rows

    def read_since(
        self,
        since_ts: str | None = None,
        *,
        before_ts: str | None = None,
        limit: int = 100,
        room_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for rec in self._iter_records():
            ts = rec.get("ts", "")
            if since_ts and ts <= since_ts:
                continue
            if before_ts and ts >= before_ts:
                continue
            if room_id and str(rec.get("room_id") or "main") != room_id:
                continue
            rows.append(rec)
        if before_ts:
            return rows[-limit:]
        return rows[:limit] if since_ts else rows[-limit:]

    def find_record(self, msg_id: str) -> dict[str, Any] | None:
        for rec in self._iter_records():
            if rec.get("id") == msg_id:
                return rec
        return None

    def mark_reminded(self, message_id: str, target_agent_id: str, *, now: datetime | None = None) -> bool:
        rows = self._iter_records()
        changed = False
        ts = (now or datetime.now(timezone.utc).astimezone()).isoformat(timespec="milliseconds")
        for rec in rows:
            if rec.get("id") != message_id:
                continue
            delivery = rec.setdefault("delivery", {})
            reminders = delivery.setdefault("reminders", {})
            if not isinstance(reminders, dict):
                reminders = {}
                delivery["reminders"] = reminders
            target_state = reminders.setdefault(str(target_agent_id), {})
            if not isinstance(target_state, dict):
                target_state = {}
                reminders[str(target_agent_id)] = target_state
            target_state["reminded_at"] = ts
            changed = True
            break
        if not changed:
            return False
        with self._lock:
            with open(self.path, "w", encoding="utf-8") as f:
                for rec in rows:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True

    def delivery_summary(
        self,
        *,
        room_id: str | None = None,
        ack_timeout_seconds: int = DEFAULT_ACK_TIMEOUT_SECONDS,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        rows = self._iter_records()
        now = now or datetime.now(timezone.utc).astimezone()
        replies_by_parent: dict[tuple[str, str], dict[str, Any]] = {}
        for rec in rows:
            parent_id = rec.get("parent_msg_id")
            sender_id = rec.get("sender_id")
            if parent_id and sender_id:
                replies_by_parent.setdefault((str(parent_id), str(sender_id)), rec)

        pending: list[dict[str, Any]] = []
        acknowledged: list[dict[str, Any]] = []
        overdue: list[dict[str, Any]] = []
        for rec in rows:
            if room_id and str(rec.get("room_id") or "main") != room_id:
                continue
            targets = rec.get("delivery", {}).get("targets") or []
            if not isinstance(targets, list) or not targets:
                continue
            for target in targets:
                target_id = str(target)
                ack = replies_by_parent.get((str(rec.get("id")), target_id))
                item = {
                    "message_id": rec.get("id"),
                    "room_id": rec.get("room_id"),
                    "target_agent_id": target_id,
                    "sender_id": rec.get("sender_id"),
                    "text": rec.get("text"),
                    "ts": rec.get("ts"),
                    "ack_message_id": ack.get("id") if ack else None,
                    "ack_ts": ack.get("ts") if ack else None,
                    "status": "acknowledged" if ack else "pending",
                    "reminded_at": (rec.get("delivery", {}).get("reminders") or {}).get(target_id, {}).get("reminded_at")
                    if isinstance((rec.get("delivery", {}).get("reminders") or {}).get(target_id), dict)
                    else None,
                }
                if ack:
                    acknowledged.append(item)
                    continue
                msg_ts = _parse_iso(str(rec.get("ts") or ""))
                age_seconds = int((now - msg_ts).total_seconds()) if msg_ts else None
                item["age_seconds"] = age_seconds
                item["ack_timeout_seconds"] = ack_timeout_seconds
                if age_seconds is not None and age_seconds >= ack_timeout_seconds:
                    item["status"] = "overdue"
                    overdue.append(item)
                else:
                    pending.append(item)

        return {
            "pending": pending,
            "overdue": overdue,
            "acknowledged": acknowledged,
            "counts": {
                "pending": len(pending),
                "overdue": len(overdue),
                "acknowledged": len(acknowledged),
                "total": len(pending) + len(overdue) + len(acknowledged),
            },
        }

    def tasks_summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        tasks: dict[str, dict[str, Any]] = {}
        events: list[dict[str, Any]] = []
        for rec in self._iter_records():
            if room_id and str(rec.get("room_id") or "main") != room_id:
                continue
            msg_type = rec.get("message_type", "chat")
            tid = rec.get("task_id") if msg_type in THREAD_START_TYPES else rec.get("parent_task_id")
            if msg_type not in TASK_STATUS_BY_MESSAGE_TYPE or not tid:
                continue
            status = TASK_STATUS_BY_MESSAGE_TYPE[msg_type]
            if msg_type in THREAD_START_TYPES:
                task = tasks.setdefault(
                    str(tid),
                    {
                        "task_id": tid,
                        "owner": rec.get("owner") or "unassigned",
                        "status": status,
                        "priority": rec.get("priority") or "p1",
                        "title": rec.get("text", ""),
                        "created_at": rec.get("ts"),
                        "updated_at": rec.get("ts"),
                        "source_msg_id": rec.get("id"),
                        "last_event_id": rec.get("id"),
                        "thread_kind": msg_type,
                        "mentions": rec.get("mentions") or [],
                        "comments": [],
                        "severity_summary": {"P0": 0, "P1": 0, "P2": 0},
                        "all_clear_by": [],
                    },
                )
                task["owner"] = rec.get("owner") or task.get("owner") or "unassigned"
                task["status"] = status
                if rec.get("priority"):
                    task["priority"] = rec.get("priority")
            else:
                task = tasks.setdefault(
                    str(tid),
                    {
                        "task_id": tid,
                        "owner": rec.get("owner") or "unassigned",
                        "status": status,
                        "priority": rec.get("priority"),
                        "title": "",
                        "created_at": None,
                        "updated_at": rec.get("ts"),
                        "source_msg_id": None,
                        "last_event_id": rec.get("id"),
                        "thread_kind": "task",
                        "mentions": [],
                        "comments": [],
                        "severity_summary": {"P0": 0, "P1": 0, "P2": 0},
                        "all_clear_by": [],
                    },
                )
                if rec.get("owner"):
                    task["owner"] = rec.get("owner")
                if rec.get("priority"):
                    task["priority"] = rec.get("priority")
                meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
                severity = str(meta.get("severity") or "").upper()
                if severity in {"P0", "P1", "P2"}:
                    task.setdefault("severity_summary", {"P0": 0, "P1": 0, "P2": 0})[severity] += 1
                if msg_type == "review_clear":
                    if rec.get("sender_id") not in task.setdefault("all_clear_by", []):
                        task["all_clear_by"].append(rec.get("sender_id"))
                    status = "resolved"
                task.setdefault("comments", []).append(
                    {
                        "message_id": rec.get("id"),
                        "sender_id": rec.get("sender_id"),
                        "message_type": msg_type,
                        "severity": severity or None,
                        "all_clear": msg_type == "review_clear",
                        "text": rec.get("text"),
                        "ts": rec.get("ts"),
                    }
                )
                if task.get("thread_kind") == "review_request" and msg_type != "review_clear":
                    task["status"] = "waiting_review"
                else:
                    task["status"] = status
                task["updated_at"] = rec.get("ts")
                task["last_event_id"] = rec.get("id")
            events.append(
                {
                    "message_id": rec.get("id"),
                    "task_id": tid,
                    "message_type": msg_type,
                    "status": status,
                    "priority": rec.get("priority"),
                    "owner": rec.get("owner"),
                    "ts": rec.get("ts"),
                }
            )

        counts_by_owner: dict[str, dict[str, int]] = {}
        counts_by_priority = {"p0": 0, "p1": 0, "p2": 0, "none": 0, "total": 0}
        for task in tasks.values():
            owner = str(task.get("owner") or "unassigned")
            counts = counts_by_owner.setdefault(owner, {"open": 0, "in-progress": 0, "done": 0, "blocked": 0, "total": 0})
            status = str(task.get("status") or "open")
            counts[status] = counts.get(status, 0) + 1
            counts["total"] += 1
            priority = task.get("priority")
            if priority in PRIORITIES:
                counts_by_priority[str(priority)] += 1
            else:
                counts_by_priority["none"] += 1
            counts_by_priority["total"] += 1

        roster_order = self._reply_agent_ids + ["unassigned"]
        ordered_counts = {
            owner: counts_by_owner.get(owner, {"open": 0, "in-progress": 0, "done": 0, "blocked": 0, "total": 0})
            for owner in roster_order
            if owner in counts_by_owner or owner != "unassigned"
        }
        for owner, counts in sorted(counts_by_owner.items()):
            if owner not in ordered_counts:
                ordered_counts[owner] = counts

        return {
            "tasks": sorted(
                tasks.values(),
                key=lambda t: (
                    PRIORITY_RANK.get(t.get("priority"), 3),
                    1 if t.get("status") == "done" else 0,
                    str(t.get("updated_at") or ""),
                ),
            ),
            "counts_by_owner": ordered_counts,
            "counts_by_priority": counts_by_priority,
            "delivery_counts": self.delivery_summary(room_id=room_id)["counts"],
            "events": events,
        }

    def delete(self, msg_id: str) -> bool:
        if not self.path.exists():
            return False
        with self._lock:
            lines: list[str] = []
            found = False
            try:
                with open(self.path, encoding="utf-8") as f:
                    for line in f:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            rec = json.loads(stripped)
                        except json.JSONDecodeError:
                            lines.append(stripped)
                            continue
                        if rec.get("id") == msg_id:
                            found = True
                        else:
                            lines.append(stripped)
            except Exception:
                return False
            if not found:
                return False
            with open(self.path, "w", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")
        return True

    def tail(self, limit: int = 20, *, room_id: str | None = None) -> list[dict[str, Any]]:
        return self.read_since(limit=limit, room_id=room_id)

    def context_lines(self, limit: int = 20, *, room_id: str | None = None) -> list[str]:
        tool_trace_prefixes = (
            "Explored", "Ran ", "Ran\n", "Read ", "Read\n", "Edited ", "Edited\n",
            "Wrote ", "Wrote\n", "Searched", "Searching", "Search ", "Search\n",
            "Created ", "Created\n", "Listed ", "List ", "Deleted ", "Updated ",
            "Patched ", "Bash(", "Added ", "Added\n", "Removed ", "Removed\n",
            "Modified ", "Modified\n", "Found ", "Fetched ", "Reading ", "Editing ",
            "Writing ", "Running ",
        )
        bad_context_markers = (
            "[workgroup_protocol]", "[/workgroup_protocol]", "[context]", "[/context]",
            "[WORKGROUP ", "remaining_handoffs=", "Booting MCP server:",
        )
        raw_pull = max(limit * 3, 60)
        lines: list[str] = []
        for rec in self.tail(raw_pull, room_id=room_id):
            sender_id = rec.get("sender_id", "")
            text_raw = str(rec.get("text", "")).strip()
            text_lstripped = text_raw.lstrip()
            if sender_id in self._agent_ids and text_lstripped.startswith(tool_trace_prefixes):
                continue
            if sender_id in self._agent_ids and text_lstripped.startswith(("└", "├", "─", "━")):
                continue
            if sender_id in self._agent_ids and any(marker in text_raw for marker in bad_context_markers):
                continue
            sender = self.member(sender_id) or {}
            name = sender.get("display_name") or sender_id
            ts = str(rec.get("ts", ""))[11:16]
            text = text_raw.replace("\n", " ")
            if len(text) > 180:
                text = text[:177] + "..."
            lines.append(f"[{ts}] {name}: {text}")
            if len(lines) >= limit:
                break
        return lines

    def set_typing(self, agent_id: str, is_typing: bool, dispatch_id: str | None = None, *, status_text: str | None = None, save: bool = True):
        if agent_id not in self._roster_by_id:
            return
        agents = self._state.setdefault("agents", {})
        state = agents.setdefault(agent_id, {})
        state["is_typing"] = bool(is_typing)
        state["typing_since"] = _now_iso() if is_typing else None
        if dispatch_id is not None:
            state["dispatch_id"] = dispatch_id
        if status_text is not None:
            state["status_text"] = status_text if status_text else None
        if not is_typing:
            state["last_seen"] = _now_iso()
            state["status_text"] = None
        if save:
            with self._lock:
                self._save_state()

    def status_snapshot(self, session_exists: Callable[[str], bool] | None = None) -> dict[str, Any]:
        agents: dict[str, Any] = {}
        state_agents = self._state.get("agents", {})
        now = datetime.now(timezone.utc).astimezone()
        changed = False
        for member in self._roster:
            if member.get("kind") != "agent":
                continue
            agent_id = member["id"]
            session = member.get("tmux")
            online = bool(session and session_exists and session_exists(str(session)))
            stored = state_agents.get(agent_id, {})
            if stored.get("is_typing") and stored.get("typing_since"):
                typing_since = _parse_iso(str(stored.get("typing_since")))
                if typing_since and (now - typing_since).total_seconds() > 180:
                    stored["is_typing"] = False
                    stored["typing_since"] = None
                    changed = True
            agents[agent_id] = {
                "state": "online" if online else "offline",
                "tmux": session,
                "last_seen": stored.get("last_seen"),
                "is_typing": bool(stored.get("is_typing")),
                "typing_since": stored.get("typing_since"),
                "dispatch_id": stored.get("dispatch_id"),
                "status_text": stored.get("status_text"),
            }
        if changed:
            with self._lock:
                self._save_state()
        return {"agents": agents}
