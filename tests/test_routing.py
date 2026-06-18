from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from groupchat.config import DEFAULT_MEMBERS, default_aliases
from groupchat.store import ALL_TOKEN, GroupChatStore


def make_store(tmp_path: Path):
    return GroupChatStore(
        tmp_path / "group.jsonl",
        tmp_path / "state.json",
        roster=DEFAULT_MEMBERS,
        aliases=default_aliases(DEFAULT_MEMBERS),
    )


class RoutingTests(unittest.TestCase):
    def test_mentions_from_text_and_list_are_deduped(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_store(Path(td))
            mentions = store.normalize_mentions(["assistant-a"], "ping @assistant")
            self.assertEqual(mentions, ["assistant-a"])

    def test_human_defaults_to_default_responder(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_store(Path(td))
            self.assertEqual(store.targets_for("you", [], None), ["assistant-a"])

    def test_human_all_targets_reply_agents(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_store(Path(td))
            self.assertEqual(store.targets_for("you", [ALL_TOKEN], None), ["assistant-a", "assistant-b"])

    def test_agent_requires_explicit_mention_and_hop_guard(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_store(Path(td))
            self.assertEqual(store.targets_for("assistant-a", [], None, hop_count=0), [])
            self.assertEqual(store.targets_for("assistant-a", ["assistant-b"], None, hop_count=0), ["assistant-b"])
            self.assertEqual(store.targets_for("assistant-a", ["assistant-b"], None, hop_count=3), [])

    def test_duplicate_display_names_route_by_model_alias(self):
        members = [
            {"id": "you", "display_name": "You", "kind": "human", "can_reply": False, "aliases": ["you"]},
            {
                "id": "assistant47",
                "display_name": "Assistant",
                "kind": "agent",
                "can_reply": True,
                "model": "4.7",
                "aliases": ["Assistant4.7", "Assistant 4.7"],
            },
            {
                "id": "assistant46",
                "display_name": "Assistant",
                "kind": "agent",
                "can_reply": True,
                "model": "4.6",
                "aliases": ["Assistant4.6", "Assistant 4.6"],
            },
        ]
        with tempfile.TemporaryDirectory() as td:
            store = GroupChatStore(
                Path(td) / "group.jsonl",
                Path(td) / "state.json",
                roster=members,
                aliases=default_aliases(members),
            )
            self.assertEqual(store.normalize_mentions(text="@Assistant4.6 你看一下"), ["assistant46"])
            self.assertEqual(store.normalize_mentions(text="@Assistant 4.7 你先说"), ["assistant47"])
            self.assertEqual(store.normalize_mentions(text="@Assistant 这条不应该随机打到某个同名 agent"), [])
            self.assertEqual(store.targets_for("assistant47", ["assistant46"], None, hop_count=1), ["assistant46"])


if __name__ == "__main__":
    unittest.main()
