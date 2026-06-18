#!/usr/bin/env python3
"""Experimental tmux reply watcher.

This adapter reads a terminal pane, waits until the target CLI is no longer
working, cleans terminal/protocol noise, and posts the final reply to
`/group/append`. It is terminal-UI-dependent and may need tuning per CLI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib import error, request


TRIGGER_DIR = Path("/tmp")
TRIGGER_PREFIX = "groupchat_ctx_"
MAX_REPLY_CHARS = 1200
MAX_REPLY_LINES = 24

BLOCK_STARTS = {
    "[workgroup_protocol]": "[/workgroup_protocol]",
    "[context]": "[/context]",
}

FORBIDDEN_MARKERS = (
    "[workgroup_protocol]",
    "[/workgroup_protocol]",
    "[context]",
    "[/context]",
    "[WORKGROUP ",
    "remaining_handoffs=",
    "Workgroup protocol and recent context file",
    "groupchat_context_",
    "Token usage:",
    "codex resume",
)

BUSY_MARKERS = (
    "esc to interrupt",
    "Esc to interrupt",
    "Working (",
    "Working...",
)

HISTORY_LINE_RE = re.compile(r"^\s*\[\d{2}:\d{2}\]\s+[^:：]+[:：]")


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] tmux_reply_watcher: {msg}", flush=True)


def tmux_capture(session: str, lines: int = 260) -> str | None:
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p", "-S", f"-{lines}"],
            text=True,
            capture_output=True,
            timeout=3,
        )
    except Exception as exc:
        log(f"capture failed session={session}: {exc}")
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def common_prefix_len(a: list[str], b: list[str]) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def is_working_screen(text: str) -> bool:
    return any(marker in text for marker in BUSY_MARKERS)


def strip_prompt_blocks(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    end_marker: str | None = None
    for raw in lines:
        stripped = raw.strip()
        if end_marker:
            if stripped == end_marker:
                end_marker = None
            continue
        if stripped in BLOCK_STARTS:
            end_marker = BLOCK_STARTS[stripped]
            continue
        out.append(raw)
    return "\n".join(out)


def trim_to_current_reply(text: str, prompt_text: str) -> str:
    if not prompt_text:
        return text
    candidates = [prompt_text.strip()]
    single_line = " ".join(prompt_text.split())
    if single_line and single_line not in candidates:
        candidates.append(single_line)
    best_idx = -1
    best_len = 0
    for candidate in candidates:
        if not candidate:
            continue
        idx = text.rfind(candidate)
        if idx > best_idx:
            best_idx = idx
            best_len = len(candidate)
    if best_idx >= 0:
        return text[best_idx + best_len :]
    return text


def clean_reply(text: str, prompt_text: str = "") -> str:
    text = strip_prompt_blocks(trim_to_current_reply(text, prompt_text))
    lines: list[str] = []
    skip_prefixes = (
        "[GROUP ",
        "[WORKGROUP ",
        "[context]",
        "[/context]",
        "[workgroup_protocol]",
        "[/workgroup_protocol]",
        "remaining_handoffs=",
        "mentions=",
        "工作群协议和最近上下文在本机文件",
        "处理前请读取该文件",
        "Workgroup protocol and recent context are in this local file:",
        "Read that file for context",
        "Token usage:",
        "To continue this session",
        "codex resume",
        "Booting MCP server:",
        "⏵",
        "⏺",
        "›",
        "❯",
    )
    tool_prefixes = (
        "Read ",
        "Ran ",
        "Edited ",
        "Searched ",
        "Listed ",
        "Explored ",
        "Bash(",
        "Edit(",
        "Grep(",
    )
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if stripped.startswith("• "):
            stripped = stripped[2:].strip()
            line = stripped
        if stripped.startswith(skip_prefixes):
            continue
        if any(marker in stripped for marker in FORBIDDEN_MARKERS):
            continue
        if HISTORY_LINE_RE.match(stripped):
            continue
        if stripped.startswith(tool_prefixes):
            continue
        if stripped in {"Explored", "Read", "Ran", "Edited", "Searched", "Listed"}:
            continue
        if stripped.startswith(("└", "├", "─", "━", "╭", "╰", "│", ">", "›")):
            continue
        if " default · ~/" in stripped or stripped.startswith(("Tip:", "Heads up", "⚠ Heads up")):
            continue
        lines.append(line)

    cleaned = "\n".join(lines).strip()
    cleaned_lines = [line for line in cleaned.splitlines() if line.strip()]
    if len(cleaned_lines) > MAX_REPLY_LINES:
        cleaned = "\n".join(cleaned_lines[-MAX_REPLY_LINES:]).strip()
    if len(cleaned) > MAX_REPLY_CHARS:
        cleaned = cleaned[-MAX_REPLY_CHARS:].strip()
    return cleaned


def looks_contaminated(text: str) -> bool:
    if not text:
        return True
    if any(marker in text for marker in FORBIDDEN_MARKERS):
        return True
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > MAX_REPLY_LINES:
        return True
    if sum(1 for line in lines if HISTORY_LINE_RE.match(line.strip())) >= 2:
        return True
    return len(text) > MAX_REPLY_CHARS


def wait_for_reply(
    session: str,
    baseline: str,
    *,
    prompt_text: str,
    min_wait: float,
    stable_for: float,
    max_wait: float,
    capture=tmux_capture,
    sleep=time.sleep,
) -> str:
    start = time.time()
    last_capture = baseline
    last_change = time.time()
    sleep(min_wait)

    while time.time() - start < max_wait:
        current = capture(session) or ""
        busy = is_working_screen(current)
        if current != last_capture:
            last_capture = current
            last_change = time.time()
        elif not busy and time.time() - last_change >= stable_for:
            break
        sleep(1.0)

    final_capture = capture(session) or last_capture
    if is_working_screen(final_capture):
        return ""

    before = baseline.splitlines()
    after = last_capture.splitlines()
    idx = common_prefix_len(before, after)
    diff_reply = clean_reply("\n".join(after[idx:]), prompt_text)
    if diff_reply and not looks_contaminated(diff_reply):
        return diff_reply

    anchored_reply = clean_reply(last_capture, prompt_text)
    if anchored_reply and not looks_contaminated(anchored_reply):
        return anchored_reply
    return ""


def post_group_append(server_url: str, token: str, payload: dict) -> tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        server_url.rstrip("/") + "/group/append",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "X-Auth-Token": token},
    )
    try:
        with request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def load_token(explicit: str, token_file: str) -> str:
    if explicit:
        return explicit
    env = os.environ.get("GROUPCHAT_AUTH_TOKEN", "")
    if env:
        return env
    path = Path(token_file).expanduser()
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def trigger_path(agent_id: str, session: str, trigger_dir: Path = TRIGGER_DIR) -> Path:
    safe_session = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in session)
    return trigger_dir / f"{TRIGGER_PREFIX}{agent_id}_{safe_session}.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Post tmux agent replies back to groupchat-oss")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--server-url", default=os.environ.get("GROUPCHAT_SERVER_URL", "http://127.0.0.1:8795"))
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--token-file", default="~/.groupchat/token")
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--min-wait", type=float, default=4.0)
    parser.add_argument("--stable-for", type=float, default=4.0)
    parser.add_argument("--max-wait", type=float, default=180.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    token = load_token(args.auth_token, args.token_file)
    if not token:
        log("missing auth token; set GROUPCHAT_AUTH_TOKEN or --token-file")
        return 2

    path = trigger_path(args.agent_id, args.session)
    processed: set[str] = set()
    last_digest = ""
    log(f"watching agent={args.agent_id} session={args.session} trigger={path}")

    while True:
        if not path.exists():
            if args.once:
                return 0
            time.sleep(args.poll)
            continue

        try:
            trigger = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log(f"bad trigger {path}: {exc}")
            path.unlink(missing_ok=True)
            continue

        message_id = str(trigger.get("message_id") or "")
        prompt_text = str(trigger.get("text") or "")
        if not message_id or message_id in processed:
            path.unlink(missing_ok=True)
            continue

        baseline = tmux_capture(args.session)
        if baseline is None:
            log(f"tmux session {args.session!r} not found; keeping trigger")
            time.sleep(3)
            continue

        reply = wait_for_reply(
            args.session,
            baseline,
            prompt_text=prompt_text,
            min_wait=args.min_wait,
            stable_for=args.stable_for,
            max_wait=args.max_wait,
        )
        if not reply:
            log(f"empty or contaminated reply for message_id={message_id}; clearing trigger")
            processed.add(message_id)
            path.unlink(missing_ok=True)
            if args.once:
                return 0
            continue

        digest = hashlib.sha256(reply.encode("utf-8")).hexdigest()
        if digest == last_digest:
            log("duplicate reply digest; clearing trigger")
            processed.add(message_id)
            path.unlink(missing_ok=True)
            if args.once:
                return 0
            continue

        payload = {
            "sender_id": args.agent_id,
            "text": reply,
            "source": f"tmux:{args.session}:watcher",
            "parent_msg_id": message_id,
            "hop_count": int(trigger.get("hop_count") or 0) + 1,
        }
        status, body = post_group_append(args.server_url, token, payload)
        if 200 <= status < 300:
            log(f"posted /group/append sender={args.agent_id} chars={len(reply)}")
            processed.add(message_id)
            last_digest = digest
            path.unlink(missing_ok=True)
            if args.once:
                return 0
        else:
            log(f"POST /group/append failed status={status} body={body[:200]}")
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
