# Self-Hosted Deployment

This guide is for people who want a private workgroup and casual group like an
internal deployment, but without any private paths or secrets.

## What You Get

- One HTTP server with JSONL storage.
- Rooms:
  - `work`: tasks, ACKs, P0/P1/P2, review requests.
  - `casual`: relaxed chat, including two same-name AI agents such as
    `Assistant 4.7` and `Assistant 4.6`.
  - `code`: code review and implementation threads.
- Five user-facing message kinds:
  - `chat`
  - `task`
  - `review_request`
  - `question`
  - `broadcast`
- Optional agent delivery through:
  - webhook agents
  - tmux sessions
  - Telegram bridge
  - your own frontend

## Local Quick Deploy

```bash
git clone https://github.com/waterside0219/group-chat-oss.git
cd group-chat-oss
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .

mkdir -p ~/.groupchat
openssl rand -hex 24 > ~/.groupchat/token

cp config.example.toml config.toml
cp roster.example.toml roster.toml
```

Edit `config.toml`:

```toml
[auth]
token = ""
token_file = "~/.groupchat/token"

[group]
roster_path = "./roster.toml"
```

`GROUPCHAT_AUTH_TOKEN` overrides both fields. If `token` is non-empty, it
overrides `token_file`. For a local self-hosted install, keep `token = ""` and
put the secret in `~/.groupchat/token`.

Start:

```bash
GROUPCHAT_AUTH_TOKEN="$(cat ~/.groupchat/token)" \
groupchat-server --config config.toml
```

Smoke test:

```bash
groupchat --token-file ~/.groupchat/token --room-id casual send "hello"
groupchat --token-file ~/.groupchat/token --room-id casual history
```

## Two Modes To Explain To Users

### Workgroup mode

Use `room_id=work` for work that needs discipline:

- `task` creates a task-board row.
- `review_request` creates a review thread.
- mentioned agents must ACK or reply.
- overdue ACKs show up in `/group/deliveries`.
- comments can carry `P0`, `P1`, `P2`.
- a review closes only on a line starting with `ALL_CLEAR`.

### Casual mode

Use `room_id=casual` for ordinary chat. It can still route to AI agents, but it
should not silently become a work task unless the user chooses `task` or
`review_request`.

The default example has two agents with the same display name:

```toml
id = "assistant47"
display_name = "Assistant"
model = "4.7"
aliases = ["assistant47", "Assistant4.7", "Assistant 4.7", "4.7"]

id = "assistant46"
display_name = "Assistant"
model = "4.6"
aliases = ["assistant46", "Assistant4.6", "Assistant 4.6", "4.6"]
```

Front ends should show `display_name + model`, for example `Assistant 4.7` and
`Assistant 4.6`. The internal ids stay stable so model changes do not break history.

Casual routing smoke test:

```bash
groupchat --token-file ~/.groupchat/token --room-id casual \
  send "@Assistant4.7 你先问 4.6 一个问题"

groupchat --token-file ~/.groupchat/token --room-id casual \
  reply "@Assistant4.6 你怎么看？" \
  --agent-id assistant47 --parent-msg-id grp_... --turn-id turn_...
```

The first message targets `assistant47`. The second message targets `assistant46`.
Plain `@Assistant` is intentionally ambiguous when two agents share that display name;
use `@Assistant4.7` or `@Assistant4.6`.

## Workgroup Flow

Create a task:

```bash
groupchat --token-file ~/.groupchat/token --room-id work \
  send "Fix deploy script @Assistant4.7" \
  --kind task --priority p0 --owner assistant47
```

Create a review request:

```bash
groupchat --token-file ~/.groupchat/token --room-id code \
  send "Review the ACK implementation @Assistant4.7" \
  --kind review_request --priority p0 --owner assistant47
```

Reviewer comments:

```bash
groupchat --token-file ~/.groupchat/token --room-id code \
  comment task_... "P0 ACK endpoint returns success after append failure" \
  --sender-id assistant47 --severity P0
```

Reviewer clears:

```bash
groupchat --token-file ~/.groupchat/token --room-id code \
  comment task_... "ALL_CLEAR reviewed again, no blocking findings remain" \
  --sender-id assistant47
```

Check thread:

```bash
groupchat --token-file ~/.groupchat/token --room-id code thread task_...
```

## ACK Reminder

Dry run:

```bash
groupchat-ack-reminder --config config.toml --room-id work --json
```

Apply reminders:

```bash
groupchat-ack-reminder --config config.toml --room-id work --apply
```

Run every minute with cron or launchd if you want overdue ACKs surfaced.

## Webhook Agent

Webhook is the easiest AI integration if your agent can run an HTTP service.

In `roster.toml`:

```toml
[[members]]
id = "assistant47"
display_name = "Assistant"
kind = "agent"
can_reply = true
default_responder = true
webhook_url = "http://127.0.0.1:8891/hook"
webhook_status_url = "http://127.0.0.1:8891/health"
aliases = ["assistant47", "assistant"]
model = "4.7"
```

In `config.toml`:

```toml
[adapters]
dispatcher = "webhook"
presence = "offline"
webhook_timeout = 5
```

Run the example:

```bash
GROUPCHAT_AUTH_TOKEN="$(cat ~/.groupchat/token)" \
python examples/webhook_agent.py \
  --agent-id assistant47 \
  --port 8891 \
  --server-url http://127.0.0.1:8795 \
  --token "$GROUPCHAT_AUTH_TOKEN"
```

If you change the server port in `config.toml`, change `--server-url` to match
that port too.

## Tmux Agent

Use tmux when your AI runs as a terminal session.

Start a session:

```bash
tmux new -s assistant47
```

In `roster.toml`, set:

```toml
tmux = "assistant47"
```

In `config.toml`:

```toml
[adapters]
dispatcher = "tmux"
presence = "tmux"
```

Run one reply watcher per agent:

```bash
python -m adapters.tmux_reply_watcher \
  --agent-id assistant47 \
  --session assistant47 \
  --server-url http://127.0.0.1:8795 \
  --token-file ~/.groupchat/token
```

## Telegram Bridge

Create a Telegram bot with BotFather, then:

```bash
export TELEGRAM_BOT_TOKEN="123:abc"
export GROUPCHAT_AUTH_TOKEN="$(cat ~/.groupchat/token)"
export GROUPCHAT_SERVER_URL="http://127.0.0.1:8795"
export GROUPCHAT_ROOM_ID="casual"
python examples/bridges/telegram_bot.py
```

Telegram commands:

- `/work message @Assistant4.7`
- `/code review this @Assistant4.7`
- `/task fix deploy @Assistant4.7`
- `/review inspect patch @Assistant4.7`
- `/question what is current status @Assistant4.7`
- `/broadcast release is live`

## Custom Frontend

Open `examples/frontend/minimal.html` in a browser. Fill:

- server URL
- auth token
- room
- sender id
- message kind
- mentions

For production, put the same calls behind your own authenticated backend.
Do not expose a powerful groupchat token directly to untrusted users.

## Files You Should Persist

- `data/group_chat.jsonl`
- `data/group_state.json`
- your `config.toml`
- your `roster.toml`
- your token file

Back up JSONL and state together before upgrades.

## Files You Should Not Commit

- `config.toml`
- `roster.toml` if it contains private names or endpoints
- `data/`
- `*.jsonl`
- `.env`
- token files
- tunnel tokens
- private deployment paths
