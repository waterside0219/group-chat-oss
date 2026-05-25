from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.9/3.10
    tomllib = None
from typing import Any


DEFAULT_MEMBERS: list[dict[str, Any]] = [
    {
        "id": "you",
        "display_name": "You",
        "kind": "human",
        "avatar": "Y",
        "color": "neutral",
        "model": None,
        "tmux": None,
        "can_reply": False,
        "aliases": ["you", "me", "user"],
    },
    {
        "id": "assistant-a",
        "display_name": "Assistant A",
        "kind": "agent",
        "avatar": "A",
        "color": "orange",
        "model": "local-agent",
        "tmux": None,
        "can_reply": True,
        "default_responder": True,
        "aliases": ["assistant-a", "a", "assistant"],
    },
    {
        "id": "assistant-b",
        "display_name": "Assistant B",
        "kind": "agent",
        "avatar": "B",
        "color": "blue",
        "model": "local-agent",
        "tmux": None,
        "can_reply": True,
        "aliases": ["assistant-b", "b"],
    },
]


@dataclass(frozen=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8795
    strict_auth: bool = True
    auth_token: str = ""
    token_file: Path | None = None
    jsonl_path: Path = Path("./data/group_chat.jsonl")
    state_path: Path = Path("./data/group_state.json")
    roster_path: Path | None = None
    admin_sender_id: str = "you"
    dispatcher: str = "null"
    presence: str = "offline"
    webhook_timeout: float = 5.0


def expand_path(value: str | Path | None, *, base_dir: Path | None = None) -> Path | None:
    if not value:
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute() and base_dir:
        path = base_dir / path
    return path


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path).expanduser() if path else None
    data: dict[str, Any] = {}
    base_dir = Path.cwd()
    if config_path:
        base_dir = config_path.parent
        data = load_toml(config_path)

    server = data.get("server", {})
    auth = data.get("auth", {})
    storage = data.get("storage", {})
    group = data.get("group", {})
    adapters = data.get("adapters", {})
    env_token = os.environ.get("GROUPCHAT_AUTH_TOKEN", "")
    token = env_token or str(auth.get("token") or "")
    token_file = expand_path(auth.get("token_file"), base_dir=base_dir)
    if not token and token_file and token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()

    return AppConfig(
        host=str(server.get("host") or "127.0.0.1"),
        port=int(server.get("port") or 8795),
        strict_auth=bool(server.get("strict_auth", True)),
        auth_token=token,
        token_file=token_file,
        jsonl_path=expand_path(storage.get("jsonl_path") or "./data/group_chat.jsonl", base_dir=base_dir) or Path("./data/group_chat.jsonl"),
        state_path=expand_path(storage.get("state_path") or "./data/group_state.json", base_dir=base_dir) or Path("./data/group_state.json"),
        roster_path=expand_path(group.get("roster_path"), base_dir=base_dir),
        admin_sender_id=str(group.get("admin_sender_id") or "you"),
        dispatcher=str(adapters.get("dispatcher") or "null"),
        presence=str(adapters.get("presence") or "offline"),
        webhook_timeout=float(adapters.get("webhook_timeout") or 5.0),
    )


def load_roster(path: str | Path | None = None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not path:
        return [dict(m) for m in DEFAULT_MEMBERS], default_aliases(DEFAULT_MEMBERS)
    data = load_toml(Path(path).expanduser())
    members = [normalize_member(m) for m in data.get("members", [])]
    if not members:
        members = [dict(m) for m in DEFAULT_MEMBERS]
    aliases = default_aliases(members)
    all_aliases = data.get("mentions", {}).get("all", [])
    for alias in all_aliases:
        aliases[str(alias).strip().lower()] = "__all__"
    return members, aliases


def normalize_member(member: dict[str, Any]) -> dict[str, Any]:
    out = dict(member)
    out["id"] = str(out.get("id") or "").strip()
    out["display_name"] = str(out.get("display_name") or out["id"])
    out["kind"] = str(out.get("kind") or "agent")
    out["avatar"] = str(out.get("avatar") or out["display_name"][:1] or "?")
    out["color"] = str(out.get("color") or "neutral")
    out["can_reply"] = bool(out.get("can_reply", out["kind"] == "agent"))
    out["tmux"] = str(out.get("tmux") or "").strip() or None
    out["webhook_url"] = str(out.get("webhook_url") or "").strip() or None
    out["webhook_status_url"] = str(out.get("webhook_status_url") or "").strip() or None
    out["webhook_secret"] = str(out.get("webhook_secret") or "").strip() or None
    out.setdefault("model", None)
    return out


def default_aliases(members: list[dict[str, Any]]) -> dict[str, str]:
    aliases = {"all": "__all__", "__all__": "__all__", "everyone": "__all__", "team": "__all__"}
    for member in members:
        member_id = str(member["id"])
        aliases[member_id.lower()] = member_id
        display_name = str(member.get("display_name") or "").strip()
        if display_name:
            aliases[display_name.lower()] = member_id
        for alias in member.get("aliases") or []:
            aliases[str(alias).strip().lower()] = member_id
    return aliases


def load_toml(path: Path) -> dict[str, Any]:
    if tomllib is not None:
        with open(path, "rb") as f:
            return tomllib.load(f)
    return parse_simple_toml(path.read_text(encoding="utf-8"))


def parse_simple_toml(text: str) -> dict[str, Any]:
    """Small TOML subset parser for the example configs on Python 3.9/3.10."""
    root: dict[str, Any] = {}
    current: dict[str, Any] = root
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[[") and line.endswith("]]"):
            name = line[2:-2].strip()
            arr = root.setdefault(name, [])
            if not isinstance(arr, list):
                raise ValueError(f"section {name} is not an array")
            current = {}
            arr.append(current)
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            current = root.setdefault(name, {})
            if not isinstance(current, dict):
                raise ValueError(f"section {name} is not a table")
            continue
        key, value = line.split("=", 1)
        current[key.strip()] = parse_simple_toml_value(value.strip())
    return root


def parse_simple_toml_value(value: str) -> Any:
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value in {"true", "false"}:
        return value == "true"
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_simple_toml_value(item.strip()) for item in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        return value
