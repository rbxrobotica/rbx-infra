"""Bounded human query presentation, separate from the peer wire protocol."""

from __future__ import annotations

import re
from datetime import datetime

from .agent_config import ACCOUNT
from .agent_protocol import MAX_PARTS, TEXT_BYTES, clean_text

HUMAN_ALIAS = "flightdeck-dev"
HUMAN_CAPABILITIES = frozenset({"account-tag", "sasl", "message-tags", "server-time"})
REFERENCE = re.compile(r"FD-[A-Za-z0-9-]{1,32}\Z")


def parse_human_query(text: str) -> tuple[str, str]:
    """An exact initial mention routes a question; it grants no authority."""
    prefix = f"@{HUMAN_ALIAS} "
    if not text.startswith(prefix):
        raise ValueError("human query requires an exact initial alias")
    question = clean_text(text[len(prefix) :])
    if len(question.encode("utf-8")) > TEXT_BYTES:
        raise ValueError("question exceeds 240 UTF-8 bytes")
    return HUMAN_ALIAS, question


def message_metadata(tags: dict[str, str | None]) -> tuple[str, float]:
    """Only server tags count; client-only +msgid/+time never substitute."""
    msgid, timestamp = tags.get("msgid"), tags.get("time")
    if (
        not isinstance(msgid, str)
        or not 1 <= len(msgid.encode("utf-8")) <= 128
        or msgid.startswith(":")
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in msgid)
    ):
        raise ValueError("missing or invalid server message ID")
    # IRCv3 server-time uses UTC milliseconds. Reject ambiguous timestamps and
    # unsupported leap-second input instead of silently changing its meaning.
    if not isinstance(timestamp, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", timestamp, flags=re.ASCII
    ):
        raise ValueError("missing or invalid server time")
    try:
        seconds = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError) as error:
        raise ValueError("invalid server time") from error
    return msgid, seconds


def render_human_reply(
    alias: str, account: str, reference: str, text: str, channel: str
) -> list[str]:
    """Return readable PRIVMSG bodies with room for the server's relay prefix."""
    if alias != HUMAN_ALIAS or not ACCOUNT.fullmatch(account):
        raise ValueError("invalid human reply identity")
    if not REFERENCE.fullmatch(reference) or not re.fullmatch(
        r"#[a-z][a-z0-9-]{0,48}", channel
    ):
        raise ValueError("invalid reply reference or channel")
    text = clean_text(text)
    if len(text.encode("utf-8")) > TEXT_BYTES * MAX_PARTS:
        raise ValueError("reply exceeds 1920 UTF-8 bytes")
    prefix = f"[{alias} | Codex | consulta documental] {account}: "
    suffix = f" [ref {reference}]"
    # Each body <=400 bytes, and complete client wire frame <=510 bytes. Keep
    # the existing 240-byte fragment bound for answer text as an extra ceiling.
    payload_bytes = min(
        TEXT_BYTES,
        400 - len((prefix + suffix + " (8/8)").encode("utf-8")),
        510
        - len((f"PRIVMSG {channel} :" + prefix + suffix + " (8/8)").encode("utf-8")),
    )

    def split(prefer_words: bool) -> list[str]:
        fragments: list[str] = []
        remaining = text
        while remaining:
            end = size = 0
            for char in remaining:
                char_size = len(char.encode("utf-8"))
                if size + char_size > payload_bytes:
                    break
                size += char_size
                end += 1
            if prefer_words and end < len(remaining):
                # Preserve boundary spaces for exact reassembly.
                space = remaining.rfind(" ", 0, end)
                if space >= 0:
                    end = space + 1
            fragments.append(remaining[:end])
            remaining = remaining[end:]
        return fragments

    fragments = split(prefer_words=True)
    if len(fragments) > MAX_PARTS:
        fragments = split(prefer_words=False)
    if len(fragments) > MAX_PARTS:
        raise ValueError("reply exceeds eight readable IRC fragments")
    return [
        f"{prefix}({index}/{len(fragments)}) {part}{suffix}"
        for index, part in enumerate(fragments, 1)
    ]
