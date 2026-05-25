from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from adapters.tmux_dispatcher import TmuxDispatcher
from adapters.tmux_reply_watcher import clean_reply, is_working_screen, wait_for_reply
from groupchat.dispatcher import DispatchRequest


class TmuxAdapterTests(unittest.TestCase):
    def test_dispatcher_writes_neutral_context_and_trigger(self):
        with tempfile.TemporaryDirectory() as td:
            dispatcher = TmuxDispatcher(
                {"assistant-a": "agent-a"},
                {"you": "You", "assistant-a": "Assistant A"},
                trigger_dir=Path(td),
            )
            req = DispatchRequest(
                source="group",
                sender_id="you",
                text="hello",
                message_id="msg-1",
                parent_msg_id="",
                mentions=["assistant-a"],
                targets=["assistant-a"],
                context="[12:00] You: hello",
                hop_count=1,
            )
            context_path = dispatcher.write_context_file(req, 2, "[workgroup_protocol]\n[/workgroup_protocol]")
            trigger_path = dispatcher.write_trigger("assistant-a", "agent-a", req)
            self.assertTrue(context_path.exists())
            self.assertTrue(trigger_path.exists())
            self.assertIn("Workgroup Context", context_path.read_text(encoding="utf-8"))

    def test_clean_reply_removes_protocol_and_codex_noise(self):
        raw = "\n".join(
            [
                "[WORKGROUP msg_id=abc remaining_handoffs=2]",
                "工作群协议和最近上下文在本机文件：/tmp/ccc_group_context_abc.md",
                "Token usage: total=123",
                "codex resume abc",
                "final answer",
            ]
        )
        self.assertEqual(clean_reply(raw), "final answer")

    def test_working_screen_returns_empty_reply(self):
        self.assertTrue(is_working_screen("Working (12s) esc to interrupt"))
        reply = wait_for_reply(
            "agent-a",
            "",
            prompt_text="",
            min_wait=0,
            stable_for=0,
            max_wait=0,
            capture=lambda _session: "partial\nesc to interrupt",
            sleep=lambda _seconds: None,
        )
        self.assertEqual(reply, "")

    def test_wait_for_reply_after_stable_screen(self):
        captures = iter(["answer", "answer", "answer"])
        reply = wait_for_reply(
            "agent-a",
            "",
            prompt_text="",
            min_wait=0,
            stable_for=0,
            max_wait=0.1,
            capture=lambda _session: next(captures, "answer"),
            sleep=lambda _seconds: None,
        )
        self.assertEqual(reply, "answer")


if __name__ == "__main__":
    unittest.main()
