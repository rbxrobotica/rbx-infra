"""Single-channel, single-tenant configuration for the agent mailbox pilot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .bot import _split_server

NAME = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
ACCOUNT = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")


@dataclass(frozen=True)
class AgentConfig:
    agent_id: str
    server: str
    tls: bool
    nick: str
    account: str
    password_env: str
    channel: str
    peers: dict[str, str]
    operators: frozenset[str]
    state_dir: Path
    ttl_seconds: int = 600
    retention_seconds: int = 86400
    capacity: int = 500


def load_agent_config(path: Path) -> AgentConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("agent config must be a mapping")
    required = {
        "agent_id",
        "server",
        "tls",
        "nick",
        "account",
        "password_env",
        "channel",
        "peers",
        "operators",
        "state_dir",
    }
    if set(raw) - required - {"ttl_seconds", "retention_seconds", "capacity"}:
        raise ValueError("unknown agent config field")
    if required - set(raw):
        raise ValueError("missing required agent config field")
    for key in required - {"tls", "peers", "operators"}:
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    if not NAME.fullmatch(raw["agent_id"]):
        raise ValueError("invalid agent_id")
    if type(raw["tls"]) is not bool:
        raise ValueError("tls must be a boolean")
    host, _ = _split_server(raw["server"])
    if not raw["tls"] and host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("plaintext is allowed only on loopback/SSH tunnels")
    if not ACCOUNT.fullmatch(raw["nick"]) or not ACCOUNT.fullmatch(raw["account"]):
        raise ValueError("invalid IRC nick or account")
    if not re.fullmatch(r"RBX_[A-Z0-9_]{1,59}", raw["password_env"]):
        raise ValueError("invalid password environment variable name")
    if not re.fullmatch(r"#[a-z][a-z0-9-]{0,48}", raw["channel"]):
        raise ValueError("invalid private pilot channel")
    peers = raw["peers"]
    if not isinstance(peers, dict) or not peers:
        raise ValueError("peers must map agent IDs to SASL accounts")
    if any(
        not isinstance(k, str)
        or not NAME.fullmatch(k)
        or not isinstance(v, str)
        or not ACCOUNT.fullmatch(v)
        for k, v in peers.items()
    ):
        raise ValueError("invalid peer mapping")
    peers = {k: v.casefold() for k, v in peers.items()}
    if (
        len(set(peers.values())) != len(peers)
        or raw["account"].casefold() in peers.values()
    ):
        raise ValueError("each agent must use a distinct dedicated account")
    if raw["agent_id"] in peers:
        raise ValueError("peers must not include this agent")
    operators = raw["operators"]
    if (
        not isinstance(operators, list)
        or not operators
        or any(not isinstance(v, str) or not ACCOUNT.fullmatch(v) for v in operators)
    ):
        raise ValueError("operators must be a non-empty list of SASL accounts")
    operators = frozenset(v.casefold() for v in operators)
    if operators & (set(peers.values()) | {raw["account"].casefold()}):
        raise ValueError("operator and agent accounts must be distinct")
    state_dir = Path(raw["state_dir"]).expanduser()
    if not state_dir.is_absolute():
        raise ValueError("state_dir must be absolute")
    limits = {}
    for key, default, low, high in (
        ("ttl_seconds", 600, 30, 3600),
        ("retention_seconds", 86400, 3600, 604800),
        ("capacity", 500, 10, 5000),
    ):
        value = raw.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"invalid {key}")
        limits[key] = value
    if limits["retention_seconds"] < limits["ttl_seconds"]:
        raise ValueError("retention must cover request TTL")
    return AgentConfig(
        raw["agent_id"],
        raw["server"],
        raw["tls"],
        raw["nick"],
        raw["account"].casefold(),
        raw["password_env"],
        raw["channel"],
        peers,
        operators,
        state_dir,
        **limits,
    )
