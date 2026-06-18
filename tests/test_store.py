from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from groupchat.config import DEFAULT_MEMBERS, default_aliases
from groupchat.store import GroupChatStore


def make_store(tmp_path: Path):
    return GroupChatStore(
        tmp_path / "group.jsonl",
        tmp_path / "state.json",
        roster=DEFAULT_MEMBERS,
        aliases=default_aliases(DEFAULT_MEMBERS),
    )


class StoreTests(unittest.TestCase):
    def test_append_read_delete(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_store(Path(td))
            rec = store.append("you", "hello @assistant-a", mentions=["assistant-a"])
            self.assertEqual(rec["sender_id"], "you")
            self.assertEqual(rec["route"], "group")
            self.assertEqual(rec["room_id"], "main")
            rows = store.read_since(limit=10)
            self.assertEqual([r["id"] for r in rows], [rec["id"]])
            self.assertTrue(store.delete(rec["id"]))
            self.assertEqual(store.read_since(limit=10), [])

    def test_rooms_and_turn_metadata_are_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_store(Path(td))
            main = store.append("you", "main room", room_id="main", turn_id="turn-main")
            ops = store.append(
                "assistant-a",
                "ops reply",
                room_id="ops",
                parent_msg_id=main["id"],
                turn_id="turn-ops",
                bubble_index=0,
                bubble_count=1,
            )
            self.assertEqual(ops["parent_msg_id"], main["id"])
            self.assertEqual(ops["turn_id"], "turn-ops")
            self.assertEqual(ops["bubble_index"], 0)
            self.assertEqual([r["id"] for r in store.read_since(limit=10, room_id="main")], [main["id"]])
            self.assertEqual([r["id"] for r in store.read_since(limit=10, room_id="ops")], [ops["id"]])

    def test_task_summary(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_store(Path(td))
            task = store.append("you", "do the thing", message_type="task", owner="assistant-a")
            store.append("assistant-a", "done", message_type="ship", parent_task_id=task["task_id"], owner="assistant-a")
            summary = store.tasks_summary()
            self.assertEqual(summary["tasks"][0]["status"], "done")
            self.assertEqual(summary["counts_by_owner"]["assistant-a"]["done"], 1)
            self.assertEqual(summary["counts_by_priority"]["p1"], 1)

    def test_task_priority_board(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_store(Path(td))
            p2 = store.append("you", "later cleanup", message_type="task", priority="p2")
            p0 = store.append("you", "production outage", message_type="task", priority="P0", owner="assistant-a")
            store.append(
                "assistant-a",
                "blocked on token",
                message_type="block",
                parent_task_id=p0["task_id"],
                priority="p0",
                owner="assistant-a",
            )
            summary = store.tasks_summary()
            self.assertEqual([task["task_id"] for task in summary["tasks"][:2]], [p0["task_id"], p2["task_id"]])
            self.assertEqual(summary["tasks"][0]["priority"], "p0")
            self.assertEqual(summary["tasks"][0]["status"], "blocked")
            self.assertEqual(summary["counts_by_priority"]["p0"], 1)
            self.assertEqual(summary["counts_by_priority"]["p2"], 1)
            self.assertEqual(summary["events"][-1]["priority"], "p0")

    def test_task_summary_can_filter_room(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_store(Path(td))
            work = store.append("you", "work outage", room_id="work", message_type="task", priority="p0")
            store.append("you", "casual follow-up", room_id="casual", message_type="task", priority="p2")
            summary = store.tasks_summary(room_id="work")
            self.assertEqual([task["task_id"] for task in summary["tasks"]], [work["task_id"]])
            self.assertEqual(summary["counts_by_priority"]["p0"], 1)
            self.assertEqual(summary["counts_by_priority"]["p2"], 0)

    def test_bad_priority_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_store(Path(td))
            with self.assertRaises(ValueError):
                store.append("you", "bad", message_type="task", priority="p9")

    def test_delivery_summary_tracks_ack_and_overdue(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_store(Path(td))
            msg = store.append(
                "you",
                "please handle this @assistant-a",
                room_id="work",
                mentions=["assistant-a"],
                delivery={"targets": ["assistant-a"], "delivered": [], "failed": []},
            )
            ts = datetime.fromisoformat(msg["ts"])
            pending = store.delivery_summary(room_id="work", ack_timeout_seconds=300, now=ts + timedelta(seconds=60))
            self.assertEqual(pending["counts"]["pending"], 1)
            self.assertEqual(pending["counts"]["overdue"], 0)

            overdue = store.delivery_summary(room_id="work", ack_timeout_seconds=300, now=ts + timedelta(seconds=301))
            self.assertEqual(overdue["counts"]["pending"], 0)
            self.assertEqual(overdue["counts"]["overdue"], 1)

            ack = store.append("assistant-a", "ACK", room_id="work", parent_msg_id=msg["id"], message_type="ack")
            done = store.delivery_summary(room_id="work", ack_timeout_seconds=300, now=datetime.fromisoformat(ack["ts"]))
            self.assertEqual(done["counts"]["acknowledged"], 1)
            self.assertEqual(done["acknowledged"][0]["ack_message_id"], ack["id"])
            self.assertEqual(store.tasks_summary(room_id="work")["delivery_counts"]["acknowledged"], 1)

    def test_context_filters_agent_tool_trace(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_store(Path(td))
            store.append("assistant-a", "Ran command output")
            store.append("assistant-a", "real reply")
            lines = store.context_lines(limit=5)
            self.assertEqual(len(lines), 1)
            self.assertIn("real reply", lines[0])


if __name__ == "__main__":
    unittest.main()
