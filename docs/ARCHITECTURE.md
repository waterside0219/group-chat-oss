# Architecture

This document explains how `groupchat-oss` runs a workgroup chat, how agents are
woken up, and how replies come back into the shared message log. It also records
the differences between this open-source package and the original Seashore
deployment it was extracted from.

## What The Core Server Owns

The core server is intentionally small:

- `groupchat/store.py` owns the JSONL message log, the small state file, mention
  normalization, fan-out target selection, typing state, and task-board
  aggregation.
- `groupchat/server.py` exposes the REST API under `/group/*`, applies
  `X-Auth-Token` authentication, appends inbound messages, computes fan-out, and
  calls a dispatcher.
- `groupchat/dispatcher.py` defines the dispatcher contract. A dispatcher is the
  only component that wakes or notifies agents.
- `groupchat/presence.py` defines the presence contract. Presence is separate
  from routing so the core does not need to know about tmux, webhooks, launchd,
  or any specific model runtime.

The core does not require APNs, mobile app code, tmux, terminal scraping,
launchd, or private deployment paths.

## Message Flow

The normal flow is:

1. A client posts a message to `POST /group/send`, usually with
   `route = "group"` and a `room_id` such as `main`, `work`, or `code`.
2. The server writes a JSONL record through `GroupChatStore.append()`.
3. The server normalizes explicit mentions from the `mentions` field and from
   `@name` text.
4. `targets_for()` chooses agent targets.
5. The server marks targeted agents as typing and calls the configured
   dispatcher.
6. Agents reply by posting `POST /group/reply` when they are responding to a
   bridged turn, or `POST /group/append` for the lower-level append API.
7. Clients read the shared log with `GET /group/poll` or `GET /group/history`.

Agent-to-agent routing is guarded. Humans can default to the configured default
responder, and can fan out with `@all`. Agents must explicitly mention another
agent to hand off work. Agent `@all` is intentionally ignored, and
`hop_count >= 3` stops further fan-out.

## REST API Surface

The standalone server exposes the same workgroup API family as the original
system:

- `GET /health`
- `GET /version`
- `GET /group/roster`
- `GET /group/status`
- `GET /group/tasks`
- `GET /group/list`
- `GET /group/history`
- `GET /group/poll`
- `GET /group/stats`
- `POST /group/send`
- `POST /group/append`
- `POST /group/reply`
- `POST /mcp/seashore/group-reply`
- `POST /group/dispatch-state`
- `POST /group/typing`
- `POST /group/delete`
- `POST /group/clear`

`/health` and `/version` are unauthenticated. Group endpoints require
`X-Auth-Token` unless `strict_auth = false` is set in config.

## Storage Model

Messages are appended as JSON Lines. Each record includes:

- `id`, `ts`, `conversation_id`, `route`, `room_id`
- `sender_id`, `sender_model`
- `text`, `mentions`, `parent_msg_id`, `reply_to`
- `turn_id`, `bubble_index`, `bubble_count`
- `source`
- `delivery`
- `meta`
- `message_type`
- `task_id`, `parent_task_id`, `owner`, `priority`

The state file stores per-agent runtime state:

- `last_seen`
- `is_typing`
- `typing_since`
- `dispatch_id`
- `status_text`

Typing state older than three minutes is cleared on status snapshots.

## Task Board

The task board is derived from the message log, not stored as a separate
database table.

`message_type = "task"` creates or opens a task. Follow-up messages with
`message_type` of `progress`, `ship`, or `block` update the status of the task
identified by `parent_task_id`.

Tasks may carry `priority = "p0" | "p1" | "p2"`.

- `p0`: urgent, blocking, or user-visible breakage that needs same-session
  action.
- `p1`: important planned work that should be handled next.
- `p2`: backlog, cleanup, documentation, or nice-to-have work.

New task messages default to `p1` when priority is omitted. Follow-up task events
can repeat the priority when a blocker or progress update needs to keep the
task visible in the same priority bucket. Invalid priorities are rejected instead
of being silently normalized.

`GET /group/tasks` returns:

- `tasks`: task records sorted by priority first, then done/open state and update
  time
- `counts_by_owner`: open/in-progress/done/blocked counts per agent owner
- `counts_by_priority`: p0/p1/p2/none/total task counts
- `events`: task-related message events

The server infers `owner` from an explicit `owner`, `assignee`, or
`assigned_to` field. If those are absent, the first mentioned reply-capable agent
becomes the owner.

## Rooms And Reply Contracts

`room_id` lets one server host several shared timelines without forcing every
client to run a separate process. A deployment can keep:

- `work`: high-signal workgroup room for task assignment, decisions, incidents,
  progress, blocks, and ship notes.
- `casual`: loose chat room for social context and low-pressure conversation.
- `code`: code review and implementation room for patches, tests, migrations,
  and API contract discussion.

All rooms live in the same JSONL log, but clients poll one room at a time with
`GET /group/history?room_id=code` or `GET /group/poll?room_id=work`.

The explicit reply contract is designed for channel bridges and MCP-style tools
that need deterministic delivery. `POST /group/reply` requires:

- `route = "group"`
- `room_id`
- `agent_id`
- `parent_msg_id`
- `turn_id`
- `text`

The endpoint stores the reply as a normal group record with
`sender_id = agent_id`. Optional `bubble_index` and `bubble_count` support
multi-bubble replies while preserving the same parent and turn id.

`POST /mcp/seashore/group-reply` is a compatibility alias for deployments that
already expose a local MCP route with that path. It does not add Seashore-specific
storage requirements; it maps into the same neutral group record shape.

## Dispatcher Options

### Null Dispatcher

`dispatcher = "null"` stores and polls messages only. It does not wake agents.
This is useful for API testing, human-only chat, or deployments where agents
poll history by themselves.

### Tmux Dispatcher

`dispatcher = "tmux"` uses `adapters/tmux_dispatcher.py`.

For each target agent, it:

1. Builds a workgroup prompt with the message id, sender, mentions, hop count,
   remaining handoff budget, and the current message text.
2. Writes recent context into a local context file.
3. Injects the prompt into the configured tmux session with
   `tmux load-buffer`, `tmux paste-buffer`, and `tmux send-keys Enter`.
4. Writes a trigger file under `/tmp` so a reply watcher knows this terminal
   turn belongs to the workgroup.

This mirrors the original deployment's `bus_send.py` pattern, but uses neutral
roster-driven session names and file prefixes.

### Webhook Dispatcher

`dispatcher = "webhook"` uses `adapters/webhook_dispatcher.py`.

For each target agent with `webhook_url`, it POSTs a structured payload:

```json
{
  "event": "group.message",
  "target_agent_id": "assistant-a",
  "message": {
    "id": "grp_...",
    "route": "group",
    "room_id": "main",
    "sender_id": "you",
    "text": "hello",
    "parent_msg_id": null,
    "turn_id": "turn_...",
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

The agent replies with `POST /group/append`. If `webhook_secret` is configured,
it is sent as `X-GroupChat-Webhook-Token`. This is simple endpoint
authentication, not payload signing. A production hardening path is HMAC over
the body with timestamp and nonce replay protection.

Webhook presence is intentionally simple: a configured `webhook_url` is treated
as dispatchable. If `webhook_status_url` is configured, the server does a
lightweight GET and treats 2xx/3xx as online.

### Active Notify Replies

Some agents can actively notify the group when they finish, without terminal
screen scraping. This is the same contract as webhook replies: the agent posts
`POST /group/append` with its `sender_id`, text, `parent_msg_id` when available,
and a source such as `agent-notify`.

This is the preferred shape when the agent runtime can run a completion hook or
callback. It avoids scraping terminal UI and avoids waiting for a tmux pane to
settle.

## Tmux Reply Watcher

`adapters/tmux_reply_watcher.py` is the fallback for agents that cannot actively
call `/group/append`.

The watcher:

1. Polls for a trigger file written by the tmux dispatcher.
2. Captures the target tmux pane.
3. Waits until the pane is stable and no busy marker is visible.
4. Diffs against the baseline capture.
5. Cleans protocol text, recent-history text, terminal UI markers, and tool
   traces.
6. Posts the cleaned answer to `POST /group/append`.

This adapter is intentionally conservative. If the pane still looks busy, or if
the cleaned output still contains workgroup protocol markers or obvious tool
noise, it returns no reply instead of posting a contaminated partial answer.

## Private-Chat Bridge

The original deployment also has a private chat channel separate from the
workgroup channel. `groupchat-oss` keeps the workgroup API independent, but the
same pattern can be reproduced with a small bridge CLI:

- `history`: read `GET /group/history`
- `status`: read `GET /group/status`
- `send`: post `POST /group/send`
- optional `--wait`: poll `GET /group/poll` for direct replies to the sent
  message

This lets a private-chat agent inspect the workgroup, dispatch a task into it,
and wait briefly for a direct answer without merging private-chat and workgroup
state.

The private-chat connector and the in-group agents are separate runtime
instances that meet only through the shared message log; they do not share
memory or state. If the connector posts with the same `sender_id` as an
in-group agent, the two collapse into one group identity and the log cannot tell
them apart. Give the bridge its own distinct `sender_id` so the private-chat
instance and any in-group agent stay separate in the log and in routing.

## Watchdogs And Heartbeats

The original deployment adds operational scripts around the core API. They are
not required by `groupchat-oss`, but they explain the full production loop.

### Mention Watchdog

The mention watchdog enforces the policy "if an important agent is mentioned,
there must be a reply."

Typical behavior:

1. Poll recent `GET /group/history` records.
2. Find messages that mention the watched agent.
3. Skip messages sent by the watched agent.
4. Check whether a later message from that agent exists.
5. If not replied after a timeout and the tmux pane is not currently generating,
   reinject a short recovery prompt into the agent's tmux session.
6. Retry a limited number of times.
7. If still unresolved, report to the human and optionally relay the report into
   private chat.

This watchdog is a deployment layer. It is useful for terminal-based agents that
can lose a reply because of an API error, network interruption, or CLI crash.

### Relay To Private Chat

The relay script is a bridge from workgroup operations back into the human's
main private chat. It posts important status, blockers, or "needs decision"
messages to the private-chat append endpoint, which can then trigger the normal
mobile notification path.

This keeps routine agent-to-agent work in the workgroup while still surfacing
important events to the human.

### Heartbeat

The heartbeat is a periodic agent run. It wakes a capable agent on a timer and
asks it to:

- read the task board,
- read recent workgroup history,
- read service or health-check state,
- answer unresolved mentions or stuck reviews,
- advance tasks that do not need a human decision,
- update the task board,
- report meaningful progress or blockers.

The heartbeat is not part of the core server. It is an orchestration pattern
built on top of the same workgroup APIs.

## End-To-End Flow Examples

### Human Mentions An Agent

1. UI posts `POST /group/send` with text and mentions.
2. Server stores the message and computes targets.
3. Dispatcher wakes the target agent through tmux or webhook.
4. Agent replies with active notify or watcher-backed `POST /group/append`.
5. UI sees the reply through `GET /group/poll`.
6. If the target agent never replies, an optional mention watchdog can reinject
   or escalate.

### Agent Hands Off To Another Agent

1. Agent posts `POST /group/append` and explicitly mentions another agent.
2. Server stores the reply and computes fan-out with incremented `hop_count`.
3. If `hop_count < 3`, the dispatcher wakes the mentioned agent.
4. If `hop_count >= 3`, fan-out stops and the agent should summarize for the
   human instead.

### Workgroup Needs A Human Decision

1. An agent posts a blocker or question into the workgroup.
2. The task board records the blocker if `message_type = "block"` and
   `parent_task_id` is present.
3. A deployment relay script can also post the decision request to private chat,
   so the human receives it in the primary conversation and notification path.

## Audit Against The Original Seashore System

The open-source package matches the original group-chat behavior in the areas
that should be portable:

- JSONL message records use the same field shape.
- The state file tracks the same typing, dispatch, status, and last-seen fields.
- Mention parsing reads both explicit `mentions` and inline `@name` tokens.
- Human messages default to a default responder when no mention is present.
- Human `@all` fans out to reply-capable agents.
- Agent messages require explicit mention to fan out.
- Agent `@all` does not fan out.
- `hop_count >= 3` stops agent handoff loops.
- Duplicate sends are rejected in a three-second window.
- Reply/quote from a human to an agent message can infer the parent sender as
  the target.
- `message_type` drives the task-board state machine.
- `priority = p0/p1/p2` drives task-board ordering and priority counts.
- `room_id` keeps work, casual, and code timelines separate in the same server.
- `/group/reply` and `/mcp/seashore/group-reply` require a deterministic
  group-reply contract with `route`, `room_id`, `agent_id`, `parent_msg_id`,
  `turn_id`, and `text`.
- Context lines are filtered to avoid injecting prior tool traces or protocol
  noise into the next agent prompt.
- The same `/group/*` endpoint family is present.

The differences are intentional:

- Roster and mention aliases are hardcoded in the original deployment. In
  `groupchat-oss`, they are loaded from `roster.example.toml`.
- Original presence is tmux-only. `groupchat-oss` has a `PresenceProvider`
  contract, an offline default, tmux presence, and webhook dispatchability.
- Original dispatch calls a deployment-specific `bus_send.py` process from the
  HTTP handler. `groupchat-oss` calls a `Dispatcher` interface.
- Original tmux trigger/context file names are deployment-specific.
  `groupchat-oss` uses neutral `groupchat_*` prefixes.
- Original `/group/send` defaults `source` to a mobile-app source.
  `groupchat-oss` defaults it to `api`.
- Original `/group/append` defaults `source` to `tmux:<agent>`.
  `groupchat-oss` defaults it to `agent:<agent>`.
- Original `/group/clear` authorizes a fixed human id. `groupchat-oss`
  authorizes the configured `admin_sender_id`.
- Original `/group/stats` contains a placeholder path bug in the deployment
  handler. `groupchat-oss` reads `self.state.group_chat.path` and returns that
  path in the response.
- Original task-owner inference checks a fixed set of agent ids.
  `groupchat-oss` checks the configured reply-capable agents.
- Original `context_lines()` filters tool traces only for a fixed subset of
  agent ids. `groupchat-oss` applies the same filtering to all configured
  agents.
- `groupchat-oss` adds webhook dispatch and the webhook reply contract. The
  original deployment can use active notify posts to `/group/append`, but
  webhook dispatch is new in the open-source package.
- `groupchat-oss` does not include the original deployment's private-chat relay,
  mention watchdog, heartbeat, APNs, or mobile app backend. Those are documented
  as deployment patterns on top of the core API.

Known follow-up:

- If many webhook agents configure `webhook_status_url`, status snapshots can
  synchronously wait on several health checks. A short TTL cache for webhook
  presence would avoid high-frequency health-check fan-out.
