"""Small bounded messages; replies can never be parsed as new questions."""

from __future__ import annotations

import re
from dataclasses import dataclass

ID = re.compile(r"[0-9a-f]{32}\Z")
TEXT_BYTES = 240
MAX_PARTS = 8


def clean_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("message text must be non-empty")
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError("control characters and multiline text are forbidden")
    return text.strip()


def chunks(text: str) -> list[str]:
    text = clean_text(text)
    result: list[str] = []
    current = ""
    for char in text:
        if len((current + char).encode("utf-8")) > TEXT_BYTES:
            result.append(current)
            current = ""
        current += char
    result.append(current)
    if len(result) > MAX_PARTS:
        raise ValueError("reply exceeds eight IRC fragments")
    return result


@dataclass(frozen=True)
class Envelope:
    kind: str
    target: str
    request_id: str
    timestamp: int
    text: str
    part: int = 1
    total: int = 1


def parse_envelope(text: str) -> Envelope:
    fields = text.split(" ", 5)
    if len(fields) < 5 or fields[0] not in {"!ask", "!reply"}:
        raise ValueError("invalid agent envelope")
    kind, target, request_id, timestamp = fields[:4]
    if not ID.fullmatch(request_id) or not re.fullmatch(r"[0-9]{1,12}", timestamp):
        raise ValueError("invalid correlation ID or timestamp")
    part = total = 1
    if kind == "!reply":
        if len(fields) != 6 or not re.fullmatch(r"[1-8]/[1-8]", fields[4]):
            raise ValueError("invalid reply fragment")
        part, total = (int(n) for n in fields[4].split("/"))
        if part > total:
            raise ValueError("invalid reply fragment")
        body = fields[5]
    else:
        body = " ".join(fields[4:])
    # Preserve spaces at fragment boundaries for exact reassembly.
    if kind == "!ask":
        clean_text(body)
    elif not body or any(ord(c) < 32 or ord(c) == 127 for c in body):
        raise ValueError("invalid reply fragment text")
    if len(body.encode("utf-8")) > TEXT_BYTES:
        raise ValueError("message exceeds IRC payload limit")
    return Envelope(kind[1:], target, request_id, int(timestamp), body, part, total)


def render(envelope: Envelope) -> str:
    fragment = f" {envelope.part}/{envelope.total}" if envelope.kind == "reply" else ""
    return (
        f"!{envelope.kind} {envelope.target} {envelope.request_id} "
        f"{envelope.timestamp}{fragment} {envelope.text}"
    )
