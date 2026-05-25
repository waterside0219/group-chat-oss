from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import os
import threading
from urllib.request import Request, urlopen


class WebhookAgentHandler(BaseHTTPRequestHandler):
    agent_id = "assistant-a"
    server_url = "http://127.0.0.1:8795"
    auth_token = ""

    def _send_json(self, code: int, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"ok": True, "agent_id": self.agent_id})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self._send_json(200, {"ok": True, "accepted": True})
        threading.Thread(target=self._append_reply, args=(body,), daemon=True).start()

    def _append_reply(self, body: dict):
        message = body.get("message") or {}
        text = str(message.get("text") or "")
        reply = {
            "sender_id": self.agent_id,
            "text": f"received: {text}",
            "parent_msg_id": message.get("id"),
            "source": f"webhook:{self.agent_id}",
        }
        data = json.dumps(reply).encode("utf-8")
        req = Request(
            f"{self.server_url.rstrip('/')}/group/append",
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Auth-Token": self.auth_token,
            },
            method="POST",
        )
        with urlopen(req, timeout=10):
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8891)
    parser.add_argument("--agent-id", default=os.environ.get("GROUPCHAT_AGENT_ID", "assistant-a"))
    parser.add_argument("--server-url", default=os.environ.get("GROUPCHAT_SERVER_URL", "http://127.0.0.1:8795"))
    parser.add_argument("--token", default=os.environ.get("GROUPCHAT_AUTH_TOKEN", "change-me"))
    args = parser.parse_args()
    WebhookAgentHandler.agent_id = args.agent_id
    WebhookAgentHandler.server_url = args.server_url
    WebhookAgentHandler.auth_token = args.token
    ThreadingHTTPServer((args.host, args.port), WebhookAgentHandler).serve_forever()


if __name__ == "__main__":
    main()
