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


if __name__ == "__main__":
    unittest.main()
