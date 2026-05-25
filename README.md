# groupchat-oss

A small standalone workgroup chat server extracted around three stable ideas:

- JSONL message storage plus a small state file.
- REST endpoints under `/group/*` with `X-Auth-Token` authentication.
- A dispatcher contract for agents. Messages can be stored only, pushed to tmux, or POSTed to agent webhooks.

The code is configured with neutral member ids and names. Edit `roster.example.toml` to define your own human and agent members.

For the full data flow, adapter model, watchdog/heartbeat patterns, and the
audit against the original deployment, see `docs/ARCHITECTURE.md`.

## Quickstart

```bash
cd group-chat-oss
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
mkdir -p ~/.groupchat
printf 'change-me' > ~/.groupchat/token
python -m groupchat.server --config config.example.toml
```

In another terminal:

```bash
TOKEN=$(cat ~/.groupchat/token)
curl -s -H "X-Auth-Token: $TOKEN" http://127.0.0.1:8795/group/roster | python -m json.tool
curl -s -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sender_id":"you","text":"hello @assistant-a"}' \
  http://127.0.0.1:8795/group/send | python -m json.tool
curl -s -H "X-Auth-Token: $TOKEN" http://127.0.0.1:8795/group/history | python -m json.tool
```

An external agent can reply without tmux:

```bash
curl -s -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sender_id":"assistant-a","text":"reply from an external agent"}' \
  http://127.0.0.1:8795/group/append | python -m json.tool
```

## Webhook Agents

Webhook dispatching is the recommended non-tmux integration. The server POSTs each routed message to the target agent's `webhook_url`; the agent does its own work and replies with `POST /group/append`.

Set the dispatcher and add a webhook URL for each agent:

```toml
[adapters]
dispatcher = "webhook"
presence = "offline"
webhook_timeout = 5

[[members]]
id = "assistant-a"
display_name = "Assistant A"
kind = "agent"
can_reply = true
default_responder = true
webhook_url = "http://127.0.0.1:8891/hook"
webhook_status_url = "http://127.0.0.1:8891/health"
webhook_secret = "optional-agent-side-secret"
aliases = ["assistant-a", "a", "assistant"]
```

Presence semantics differ by adapter. Tmux presence checks whether the configured tmux session exists. Webhook presence treats an agent as dispatchable when `webhook_url` is configured; if `webhook_status_url` is also configured, the server uses a lightweight GET to that URL and only treats 2xx/3xx responses as online.

Webhook request shape:

```json
{
  "event": "group.message",
  "target_agent_id": "assistant-a",
  "message": {
    "id": "grp_...",
    "sender_id": "you",
    "text": "hello @assistant-a",
    "parent_msg_id": null,
    "mentions": ["assistant-a"],
    "source": "api"
  },
  "dispatch": {
    "targets": ["assistant-a"],
    "hop_count": 1,
    "context": "[recent workgroup context]"
  }
}
```

If `webhook_secret` is set, it is sent only as `X-GroupChat-Webhook-Token`; it is not included in the JSON payload. Dispatcher logs also redact webhook query strings, fragments, and userinfo. This shared secret is simple endpoint authentication, not payload integrity protection; signed requests with timestamp and nonce replay protection are the expected next step for production hardening.

Agent reply contract:

```bash
curl -s -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sender_id":"assistant-a","text":"done","parent_msg_id":"grp_...","source":"webhook:assistant-a"}' \
  http://127.0.0.1:8795/group/append | python -m json.tool
```

A minimal stdlib example is available at `examples/webhook_agent.py`:

```bash
python examples/webhook_agent.py --agent-id assistant-a --port 8891 --token "$TOKEN"
```

The example returns `200` immediately and posts `/group/append` on a background thread, so slow model work does not trip the dispatcher's short webhook timeout.

The bundled CLI wraps the same API:

```bash
groupchat --token-file ~/.groupchat/token send "hello @assistant-a"
groupchat --token-file ~/.groupchat/token append "agent reply" --sender-id assistant-a
groupchat --token-file ~/.groupchat/token history --limit 10
```

## Endpoints

GET: `/health`, `/version`, `/group/roster`, `/group/status`, `/group/tasks`, `/group/list`, `/group/history`, `/group/poll`, `/group/stats`.

POST: `/group/send`, `/group/append`, `/group/dispatch-state`, `/group/typing`, `/group/delete`, `/group/clear`.

## Stage One Scope

The core server does not require terminal injection, terminal screen scraping, launchd, APNs, mobile app code, or any private deployment paths.

## Optional tmux Adapter

Stage two adds optional tmux adapters:

- `adapters/tmux_presence.py`: reports an agent online when its tmux session exists.
- `adapters/tmux_dispatcher.py`: writes a context file, injects the workgroup prompt into tmux, and writes a trigger file.
- `adapters/tmux_reply_watcher.py`: experimental watcher that waits for a stable terminal pane, cleans protocol/tool noise, and posts `/group/append`.

To enable tmux dispatching, fill each agent's `tmux` value in `roster.example.toml`, then set:

```toml
[adapters]
dispatcher = "tmux"
presence = "tmux"
```

Run one watcher per agent:

```bash
python3 -m adapters.tmux_reply_watcher \
  --agent-id assistant-a \
  --session agent-a \
  --server-url http://127.0.0.1:8795 \
  --token-file ~/.groupchat/token
```

The watcher is intentionally conservative: if the terminal still shows a busy marker such as `esc to interrupt` or `Working (`, it returns no reply instead of scraping a partial answer.
