import re
from datetime import datetime, timezone

import pytest

from strategos_irc_bot.human_protocol import (
    message_metadata,
    parse_human_query,
    render_human_reply,
)


def test_exact_mention_and_utf8_bounds():
    assert parse_human_query("@flightdeck-dev Como aprovar uma Action?") == (
        "flightdeck-dev",
        "Como aprovar uma Action?",
    )
    for bad in [
        "olá @flightdeck-dev pergunta",
        " @flightdeck-dev pergunta",
        "@Flightdeck-dev pergunta",
        "@flightdeck-dev2 pergunta",
        "@flightdeck-dev ",
        "@flightdeck-dev pergunta\n!ask local",
        "@flightdeck-dev " + "🙂" * 61,
    ]:
        with pytest.raises(ValueError):
            parse_human_query(bad)


def test_server_metadata_opaque_id_preserves_case_and_requires_utc():
    tags = {"msgid": "AbC/opaque-1", "time": "2026-10-12T00:05:15.123Z"}
    msgid, stamp = message_metadata(tags)
    assert msgid == "AbC/opaque-1"
    assert (
        datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        == "2026-10-12T00:05:15.123000+00:00"
    )
    for wrong in [
        {"+msgid": "spoof", "+time": tags["time"]},
        {"msgid": ":bad", "time": tags["time"]},
        {"msgid": "space id", "time": tags["time"]},
        {"msgid": "A\nB", "time": tags["time"]},
        {"msgid": "x" * 129, "time": tags["time"]},
        {"msgid": "valid", "time": "2026-10-12T00:05:15+03:00"},
        {"msgid": "valid", "time": "2026-10-35T00:05:15.123Z"},
        {"msgid": "valid", "time": "2026-10-12T00:05:60.123Z"},
    ]:
        with pytest.raises(ValueError):
            message_metadata(wrong)


def test_readable_replies_roundtrip_unicode_with_word_boundaries():
    text = (
        "Aprovar uma Action registra uma decisão. Execução 🙂 ocorre no Maestro. " * 10
    )
    bodies = render_human_reply(
        "flightdeck-dev", "leandro", "FD-0042", text, "#rbx-agents"
    )
    pieces = []
    for body in bodies:
        assert len(body.encode()) <= 400
        assert len(("PRIVMSG #rbx-agents :" + body).encode()) <= 510
        match = re.fullmatch(
            r"\[flightdeck-dev \| Codex \| consulta documental\] leandro: \(\d/\d\) (.*) \[ref FD-0042\]",
            body,
        )
        assert match
        pieces.append(match[1])
        assert "!reply" not in body
    assert "".join(pieces) == text.strip()
    assert all(p.endswith(" ") for p in pieces[:-1])
    # Oversized single words still split at a Unicode boundary.
    bodies = render_human_reply(
        "flightdeck-dev", "leandro", "FD-0042", "🙂" * 180, "#rbx-agents"
    )
    assert len(bodies) == 3


@pytest.mark.parametrize(
    "text", ["x" * 1921, "bad\r\nPRIVMSG #other :body", "\x01ACTION misleading\x01"]
)
def test_output_cannot_inject_protocol_or_exceed_budget(text):
    with pytest.raises(ValueError):
        render_human_reply("flightdeck-dev", "leandro", "FD-0042", text, "#rbx-agents")


def test_presentation_metadata_is_not_free_form():
    for args in [
        ("other", "leandro", "FD-0042", "ok", "#rbx-agents"),
        ("flightdeck-dev", "bad\naccount", "FD-0042", "ok", "#rbx-agents"),
        ("flightdeck-dev", "leandro", "FD-0042] fake", "ok", "#rbx-agents"),
        ("flightdeck-dev", "leandro", "FD-0042", "ok", "#rbx-agents\nother"),
    ]:
        with pytest.raises(ValueError):
            render_human_reply(*args)


def test_word_preference_does_not_waste_the_eight_part_budget():
    text = ("x" * 121 + " ") * 15
    bodies = render_human_reply(
        "flightdeck-dev", "leandro", "FD-0042", text, "#rbx-agents"
    )
    assert len(bodies) == 8
    pieces = [re.search(r"\(\d/\d\) (.*) \[ref FD-0042\]", body)[1] for body in bodies]
    assert "".join(pieces) == text.strip()
    assert all(len(piece.encode()) <= 240 for piece in pieces)
