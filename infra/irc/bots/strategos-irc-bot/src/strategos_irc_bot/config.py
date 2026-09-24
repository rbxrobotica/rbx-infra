from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class IrcConfig:
    server: str
    tls: bool
    tls_verify: bool
    nick: str
    username: str
    realname: str
    channels: tuple[str, ...]
    password_env: str


@dataclass(frozen=True)
class AuthConfig:
    allowed_accounts: tuple[str, ...]
    read_only: bool


@dataclass(frozen=True)
class StrategosConfig:
    api_base_url: str
    api_token_env: str
    enabled: bool


@dataclass(frozen=True)
class BotConfig:
    irc: IrcConfig
    auth: AuthConfig
    strategos: StrategosConfig


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a mapping")
    return value


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def load_config(path: Path) -> BotConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    root = _mapping(raw, "config")
    irc = _mapping(root.get("irc"), "irc")
    auth = _mapping(root.get("auth"), "auth")
    strategos = _mapping(root.get("strategos"), "strategos")

    channels_raw = irc.get("channels")
    if not isinstance(channels_raw, list) or not channels_raw:
        raise ValueError("irc.channels must be a non-empty list")
    channels = tuple(_nonempty(channel, "irc channel") for channel in channels_raw)
    if any(not channel.startswith("#") for channel in channels):
        raise ValueError("all IRC channels must start with #")

    accounts_raw = auth.get("allowed_accounts")
    if not isinstance(accounts_raw, list) or not accounts_raw:
        raise ValueError("auth.allowed_accounts must be a non-empty list")
    accounts = tuple(_nonempty(account, "allowed account") for account in accounts_raw)

    return BotConfig(
        irc=IrcConfig(
            server=_nonempty(irc.get("server"), "irc.server"),
            tls=bool(irc.get("tls", False)),
            tls_verify=bool(irc.get("tls_verify", True)),
            nick=_nonempty(irc.get("nick"), "irc.nick"),
            username=_nonempty(irc.get("username"), "irc.username"),
            realname=_nonempty(irc.get("realname"), "irc.realname"),
            channels=channels,
            password_env=_nonempty(irc.get("password_env"), "irc.password_env"),
        ),
        auth=AuthConfig(
            allowed_accounts=accounts,
            read_only=bool(auth.get("read_only", True)),
        ),
        strategos=StrategosConfig(
            api_base_url=_nonempty(
                strategos.get("api_base_url"), "strategos.api_base_url"
            ),
            api_token_env=_nonempty(
                strategos.get("api_token_env"), "strategos.api_token_env"
            ),
            enabled=bool(strategos.get("enabled", False)),
        ),
    )
