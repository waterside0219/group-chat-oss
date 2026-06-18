from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest

from adapters.webhook_dispatcher import WebhookDispatcher, safe_webhook_url
from groupchat.config import AppConfig
from groupchat.dispatcher import DispatchRequest
from groupchat.server import GroupChatHandler, ServerState


class CaptureHandler(BaseHTTPRequestHandler):
    records: list[dict] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.records.append(
            {
                "path": self.path,
                "token": self.headers.get("X-GroupChat-Webhook-Token"),
                "body": body,
            }
        )
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt: str, *args: object):
        return


class HealthHandler(BaseHTTPRequestHandler):
    status_code = 200

    def do_GET(self):
        self.send_response(self.status_code)
        self.end_headers()

    def log_message(self, fmt: str, *args: object):
        return


def make_request() -> DispatchRequest:
    return DispatchRequest(
        source="group",
        route="group",
        room_id="main",
        sender_id="you",
        text="hello @assistant-a",
        message_id="msg-1",
        parent_msg_id="",
        turn_id="turn-1",
        mentions=["assistant-a"],
        targets=["assistant-a"],
        context="[12:00] You: hello",
        hop_count=1,
    )


class WebhookDispatcherTests(unittest.TestCase):
    def test_dispatch_posts_structured_payload_and_secret_header(self):
        CaptureHandler.records = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            dispatcher = WebhookDispatcher.from_roster(
                [
                    {
                        "id": "assistant-a",
                        "kind": "agent",
                        "can_reply": True,
                        "webhook_url": f"http://127.0.0.1:{port}/hook?secret=do-not-log",
                        "webhook_secret": "agent-secret",
                    }
                ],
                timeout=1,
            )
            delivered, failed = dispatcher.dispatch(make_request())
            self.assertEqual(delivered, ["assistant-a"])
            self.assertEqual(failed, [])
            self.assertEqual(len(CaptureHandler.records), 1)
            record = CaptureHandler.records[0]
            self.assertEqual(record["token"], "agent-secret")
            self.assertEqual(record["body"]["event"], "group.message")
            self.assertEqual(record["body"]["target_agent_id"], "assistant-a")
            self.assertEqual(record["body"]["message"]["id"], "msg-1")
            self.assertEqual(record["body"]["message"]["route"], "group")
            self.assertEqual(record["body"]["message"]["room_id"], "main")
            self.assertEqual(record["body"]["message"]["turn_id"], "turn-1")
            self.assertNotIn("agent-secret", json.dumps(record["body"]))
        finally:
            server.shutdown()
            server.server_close()

    def test_safe_webhook_url_removes_userinfo_query_and_fragment(self):
        self.assertEqual(
            safe_webhook_url("https://user:pass@example.test:8443/hook?token=secret#frag"),
            "https://example.test:8443/hook",
        )

    def test_server_treats_webhook_agents_as_online_targets(self):
        state = ServerState(AppConfig(dispatcher="webhook"))
        member = state.group_chat.member("assistant-a")
        self.assertIsNotNone(member)
        assert member is not None
        member["webhook_url"] = "http://127.0.0.1:8891/hook"
        state.group_chat._roster_by_id["assistant-a"] = member
        state.group_chat._roster = [
            member if item["id"] == "assistant-a" else item for item in state.group_chat._roster
        ]
        handler = object.__new__(GroupChatHandler)
        handler.state = state
        self.assertIn("assistant-a", handler._group_online_agents())

    def test_server_checks_optional_webhook_status_url(self):
        HealthHandler.status_code = 503
        server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            state = ServerState(AppConfig(dispatcher="webhook", webhook_timeout=1))
            member = state.group_chat.member("assistant-a")
            self.assertIsNotNone(member)
            assert member is not None
            member["webhook_url"] = "http://127.0.0.1:8891/hook"
            member["webhook_status_url"] = f"http://127.0.0.1:{port}/health"
            state.group_chat._roster_by_id["assistant-a"] = member
            state.group_chat._roster = [
                member if item["id"] == "assistant-a" else item for item in state.group_chat._roster
            ]
            handler = object.__new__(GroupChatHandler)
            handler.state = state
            self.assertNotIn("assistant-a", handler._group_online_agents())
            HealthHandler.status_code = 204
            self.assertIn("assistant-a", handler._group_online_agents())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
