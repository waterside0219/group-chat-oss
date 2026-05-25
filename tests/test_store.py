from __future__ import annotations

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
            rows = store.read_since(limit=10)
            self.assertEqual([r["id"] for r in rows], [rec["id"]])
            self.assertTrue(store.delete(rec["id"]))
            self.assertEqual(store.read_since(limit=10), [])

    def test_task_summary(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_store(Path(td))
            task = store.append("you", "do the thing", message_type="task", owner="assistant-a")
            store.append("assistant-a", "done", message_type="ship", parent_task_id=task["task_id"], owner="assistant-a")
            summary = store.tasks_summary()
            self.assertEqual(summary["tasks"][0]["status"], "done")
            self.assertEqual(summary["counts_by_owner"]["assistant-a"]["done"], 1)

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
