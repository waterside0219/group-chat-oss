# groupchat-oss

A small standalone workgroup chat server extracted around three stable ideas:

- JSONL message storage plus a small state file.
- REST endpoints under `/group/*` with `X-Auth-Token` authentication.
- A dispatcher contract for agents. Messages can be stored only, pushed to tmux, or POSTed to agent webhooks.

The code is configured with neutral member ids and names. Edit `roster.example.toml` to define your own human and agent members.

For the full data flow, adapter model, watchdog/heartbeat patterns, and the
audit against the original deployment, see `docs/ARCHITECTURE.md`.
For a copy-paste self-hosted deployment with work/casual/code rooms, tmux AI,
Telegram, and custom frontend examples, see `docs/DEPLOY_SELF_HOSTED.md`.

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

For a human-friendly bridge script:

```bash
python examples/bridges/group_bridge.py --room-id work \
  send "Review this patch @assistant-a" \
  --kind review_request --mentions assistant-a --wait 30
```

An external agent can reply without tmux:

```bash
curl -s -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sender_id":"assistant-a","text":"reply from an external agent"}' \
  http://127.0.0.1:8795/group/append | python -m json.tool
```

For channel-style deployments, use `room_id` to keep a work room, casual room,
or code review room in the same server without merging their timelines:

```bash
curl -s -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"route":"group","room_id":"code","sender_id":"you","text":"review this @assistant-a","turn_id":"turn_123"}' \
  http://127.0.0.1:8795/group/send | python -m json.tool
```

Agents that are invoked by an external channel bridge can use the explicit group
reply contract:

```bash
curl -s -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"route":"group","room_id":"code","agent_id":"assistant-a","parent_msg_id":"grp_...","turn_id":"turn_123","text":"review complete"}' \
  http://127.0.0.1:8795/group/reply | python -m json.tool
```

`/mcp/groupchat/group-reply` is also accepted as a compatibility alias for local
MCP-style bridges. The required fields are `route="group"`, `room_id`,
`agent_id`, `parent_msg_id`, `turn_id`, and `text`.

## Rooms

Use one server and separate rooms when the same people and agents need different
operating modes:

- `work`: high-signal workgroup room. Use it for decisions, live incidents,
  task assignment, progress updates, and ship/block reports.
- `casual`: loose chat room. Use it for non-task conversation, quick questions,
  and context that should not automatically become work.
- `code`: code review and implementation room. Use it for patches, test output,
  API contracts, migration notes, and review sign-off.

Clients keep rooms separate with `room_id`; history and poll calls accept the
same field:

```bash
groupchat --token-file ~/.groupchat/token --room-id casual send "morning"
groupchat --token-file ~/.groupchat/token --room-id code history --limit 20
```

The repository includes copyable integration examples:

- `examples/bridges/group_bridge.py`: generic shell/custom frontend bridge.
- `examples/bridges/telegram_bot.py`: Telegram long-polling bridge.
- `examples/frontend/minimal.html`: tiny browser frontend with a 5-kind picker.
- `examples/ios-swiftui/GroupChatDemo.swift`: SwiftUI reference input bar.

## How A Workgroup Should Behave

A useful workgroup is not just a shared chat. It needs a response loop:

1. A human writes the need in plain language.
2. The coordinator turns it into a task, owner, and priority.
3. The named owner must acknowledge the message with `ACK` or reply under the
   same `parent_msg_id`.
4. If there is no acknowledgement before the timeout, the item is overdue and
   should be escalated to the fallback reviewer or the human.
5. The task board stays visible until the work is shipped, blocked, or explicitly
   closed.

In practice:

- Use the work room for tasks and decisions.
- Use the casual room for conversation that should not become work.
- Use the code room for patches, tests, migrations, and review.
- Do not rely on memory or vibes. If an agent was mentioned, check the delivery
  board to see whether it actually responded.

```bash
groupchat --token-file ~/.groupchat/token --room-id work deliveries
groupchat --token-file ~/.groupchat/token --room-id work \
  ack --agent-id assistant-a --parent-msg-id grp_... --text "I am taking this"
```

## Message Kinds

Clients should expose these five user-facing kinds instead of sending
everything as plain chat:

- `chat`: ordinary conversation. Use this in casual rooms and for low-pressure
  discussion.
- `task`: actionable work. It creates a task id, owner, priority, delivery
  expectation, and task-board row.
- `review_request`: asks a named reviewer to inspect code, docs, migrations, or
  operational steps. It stays `waiting_review` until the reviewer posts
  `ALL_CLEAR`.
- `question`: direct question. It expects an answer but does not create a task
  board item by default.
- `broadcast`: announcement to the room. Use sparingly; do not use it as a
  hidden task assignment.

The lower-level API still accepts legacy task update types such as `progress`,
`block`, `ship`, and `review_clear`, but end-user input bars should present the
five kinds above.

```bash
groupchat --token-file ~/.groupchat/token --room-id code \
  send "Please review the ACK patch @assistant-a" \
  --kind review_request --priority p0 --owner assistant-a
```

## Task Board And P0/P1/P2

The task board is derived from messages, so the chat log is the source of truth.
Create tasks with `message_type="task"` and update them with `progress`, `block`,
or `ship` plus `parent_task_id`.

Priorities are explicit:

- `p0`: urgent or blocking. Production breakage, user-visible failures, or work
  that must be handled in the current session.
- `p1`: important planned work. It should be picked up next, but it is not an
  active outage.
- `p2`: backlog, cleanup, documentation, or nice-to-have work.

If a task omits priority, it defaults to `p1`. Invalid values are rejected.
`GET /group/tasks` returns tasks sorted with P0 first, plus
`counts_by_owner`, `counts_by_priority`, and the task event stream.
Pass `room_id` to inspect one room's board, for example
`GET /group/tasks?room_id=work`.

```bash
groupchat --token-file ~/.groupchat/token --room-id work \
  send "Restore the webhook dispatcher @assistant-a" \
  --message-type task --priority p0 --owner assistant-a

groupchat --token-file ~/.groupchat/token --room-id work \
  append "Waiting on cloud token" \
  --sender-id assistant-a --message-type block \
  --parent-task-id task_... --priority p0 --owner assistant-a

curl -s -H "X-Auth-Token: $TOKEN" \
  http://127.0.0.1:8795/group/tasks | python -m json.tool
```

The task board response also includes `delivery_counts`. If `overdue > 0`, the
workgroup has mentioned someone who has not acknowledged or replied in time.

## Review Request Loop And ALL_CLEAR

Use `review_request` when the next step is not implementation but review. A
healthy loop is:

1. Sender posts a `review_request` and mentions the reviewer.
2. Reviewer ACKs within five minutes.
3. Reviewer posts comments. Findings should start with `P0`, `P1`, or `P2` so
   clients can summarize severity.
4. Author fixes all `P0` issues and asks for re-review under the same thread.
5. Reviewer posts a line starting with `ALL_CLEAR` only when the thread is clean.

`GET /group/thread/<task_id>` returns the review thread with `comments`,
`severity_summary`, `all_clear_by`, and `status`. A review request is
`waiting_review` until `ALL_CLEAR`, then becomes `resolved`.

```bash
groupchat --token-file ~/.groupchat/token --room-id code \
  comment task_... "P0 ACK endpoint returns success after append failure" \
  --sender-id assistant-a --severity P0

groupchat --token-file ~/.groupchat/token --room-id code \
  comment task_... "ALL_CLEAR reviewed again, no blocking findings remain" \
  --sender-id assistant-a
```

Reminder automation can inspect overdue ACKs without mutating state:

```bash
groupchat-ack-reminder --config config.example.toml --room-id work --json
```

Add `--apply` only when you want it to append reminder records and mark the
target as recently reminded.

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
  -d '{"route":"group","room_id":"main","agent_id":"assistant-a","text":"done","parent_msg_id":"grp_...","turn_id":"turn_..."}' \
  http://127.0.0.1:8795/group/reply | python -m json.tool
```

A minimal stdlib example is available at `examples/webhook_agent.py`:

```bash
python examples/webhook_agent.py \
  --agent-id assistant-a \
  --port 8891 \
  --server-url http://127.0.0.1:8795 \
  --token "$TOKEN"
```

The example returns `200` immediately and posts `/group/append` on a background thread, so slow model work does not trip the dispatcher's short webhook timeout.

The bundled CLI wraps the same API:

```bash
groupchat --token-file ~/.groupchat/token send "hello @assistant-a"
groupchat --token-file ~/.groupchat/token append "agent reply" --sender-id assistant-a
groupchat --token-file ~/.groupchat/token history --limit 10
```

## Endpoints

GET: `/health`, `/version`, `/group/roster`, `/group/status`, `/group/tasks`, `/group/deliveries`, `/group/list`, `/group/history`, `/group/poll`, `/group/stats`.

POST: `/group/send`, `/group/append`, `/group/reply`, `/group/ack`, `/mcp/groupchat/group-reply`, `/group/dispatch-state`, `/group/typing`, `/group/delete`, `/group/clear`.

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
