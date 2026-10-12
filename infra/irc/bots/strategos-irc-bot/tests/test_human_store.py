"""Human storage tests use temporary private SQLite files, never live services."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import threading

import pytest

from strategos_irc_bot.agent_config import AgentConfig
from strategos_irc_bot.agent_protocol import Envelope, parse_envelope
from strategos_irc_bot.agent_store import Mailbox
from strategos_irc_bot.human_store import HumanMailbox


@pytest.fixture
def config(tmp_path):
    return AgentConfig(
        "local",
        "127.0.0.1:6667",
        False,
        "rbx-local",
        "rbx-local",
        "RBX_AGENT_IRC_PASSWORD",
        "#rbx-agents",
        {"peer": "rbx-peer"},
        frozenset({"leandro", "other-human"}),
        tmp_path / "private",
        human_queries=True,
    )


@pytest.fixture
def box(config):
    return HumanMailbox(Mailbox(config))


def session(box, now=1000, **kwargs):
    return box.open_session(
        "flightdeck-dev",
        "leandro",
        "rbx-flightdeck",
        "a" * 40,
        "gpt-6.1-sol",
        now=now,
        **kwargs,
    )


def receive(box, msgid="opaque-1", now=1001, **kwargs):
    fields = dict(
        alias="flightdeck-dev",
        account="leandro",
        msgid=msgid,
        server_time=now,
        text="Qual é o limite?",
    )
    fields.update(kwargs)
    return box.receive(**fields, now=now)


def answer(box, text="O limite é três perguntas.", now=1002, lease_seconds=60):
    row = receive(box)
    claim = box.claim(row["id"], "consumer-1", lease_seconds=lease_seconds, now=now)
    return box.complete(row["id"], "consumer-1", claim["fence"], text, now=now + 1)


def test_disabled_opt_in_never_creates_tables_or_accepts_mutations(config):
    disabled = HumanMailbox(Mailbox(replace(config, human_queries=False)))
    assert disabled.status()["enabled"] is False
    with disabled.mailbox.connection() as db:
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'human_%'"
        ).fetchall()
    calls = [
        lambda: session(disabled),
        lambda: disabled.close_session("missing"),
        lambda: receive(disabled),
        lambda: disabled.claim("id", "owner"),
        lambda: disabled.complete("id", "owner", 1, "answer"),
        lambda: disabled.fail("id", "owner", 1, "reason"),
        lambda: disabled.dispatch_pending(lambda _: None),
        lambda: disabled.mark_socket_sent("id"),
    ]
    for call in calls:
        with pytest.raises(ValueError, match="disabled"):
            call()


def test_deduplication_survives_restart_and_conflicting_bodies_accounts_fail(
    box, config
):
    opened = session(box)
    first = receive(box)
    assert first["reference"] == "FD-0001"
    assert first["session_id"] == opened["id"]
    assert first["untrusted_content"] is True
    assert len(first["id"]) == 32
    restarted = HumanMailbox(Mailbox(config))
    repeated = receive(restarted)
    assert repeated["duplicate"] is True and repeated["id"] == first["id"]
    assert receive(restarted, account="other-human") is None
    assert receive(restarted, text="Texto conflitante") is None
    assert receive(restarted, server_time=1001.001) is None
    second = receive(restarted, msgid="opaque-2")
    assert second["id"] != first["id"] and second["text"] == first["text"]
    assert restarted.reference(first["reference"], now=1002)["id"] == first["id"]
    assert restarted.status(now=1002)["sessions"][0]["accepted"] == 2
    # A replay remains correlated after its freshness window without new work.
    assert receive(restarted, now=1050, server_time=1001)["id"] == first["id"]


@pytest.mark.parametrize(
    "changed",
    [
        {"account": "unknown"},
        {"account": "rbx-local"},
        {"account": "rbx-peer"},
        {"alias": "unknown"},
        {"msgid": ""},
        {"msgid": ":opaque"},
        {"msgid": "bad\u00a0id"},
        {"msgid": "é" * 65},
        {"server_time": None},
        {"server_time": True},
        {"server_time": float("nan")},
        {"server_time": float("inf")},
        {"server_time": "1001"},
        {"server_time": 969},
        {"server_time": 1007},
        {"server_time": 999.999},
        {"text": ""},
        {"text": "a\nline"},
        {"text": "é" * 121},
    ],
)
def test_ingress_rejects_untrusted_or_stale_metadata(box, changed):
    session(box)
    assert receive(box, **changed) is None
    assert box.inbox(now=1002) == []


def test_ingress_freshness_boundaries_and_casefold_account(box):
    session(box)
    assert receive(box, now=1030, server_time=1000, account="LEANDRO")
    assert receive(box, now=1030, server_time=1035, msgid="future-boundary")
    assert receive(box, now=1031, server_time=1000, msgid="stale") is None


def test_question_limit_closes_admission_but_accepted_answers_still_complete(box):
    session(box)
    rows = [receive(box, msgid=f"id-{n}") for n in range(3)]
    assert receive(box, msgid="id-4") is None
    status = box.status(now=1002)["sessions"][0]
    assert status["admission_open"] == 0 and status["closed_at"] is None
    for number, row in enumerate(rows):
        claim = box.claim(row["id"], "same-consumer", now=1002 + number)
        assert (
            box.complete(
                row["id"],
                "same-consumer",
                claim["fence"],
                "Resposta.",
                now=1002 + number,
            )["status"]
            == "answered"
        )
    assert box.status(now=1010)["requests"] == {"answered": 3}


def test_two_separate_connections_have_one_claim_winner_and_pinned_consumer(
    box, config
):
    session(box)
    first, second = receive(box), receive(box, msgid="opaque-2")
    other = HumanMailbox(Mailbox(config))
    barrier = threading.Barrier(2)

    def try_claim(mailbox, owner):
        barrier.wait(timeout=5)
        try:
            return mailbox.claim(first["id"], owner, now=1002)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(try_claim, box, "one")
        b = pool.submit(try_claim, other, "two")
        results = [a.result(timeout=5), b.result(timeout=5)]
    winners = [r for r in results if r]
    assert len(winners) == 1
    winner = winners[0]
    with pytest.raises(ValueError, match="in-progress"):
        box.claim(second["id"], winner["owner"], now=1003)
    box.complete(first["id"], winner["owner"], winner["fence"], "Ok", now=1003)
    with pytest.raises(ValueError, match="different consumer"):
        box.claim(second["id"], "two" if winner["owner"] == "one" else "one", now=1004)
    next_claim = box.claim(second["id"], winner["owner"], now=1004)
    assert next_claim["fence"] > winner["fence"]


def test_owner_fence_and_lease_are_required_and_expired_claim_never_reclaimed(box):
    session(box)
    row = receive(box)
    claim = box.claim(row["id"], "one", lease_seconds=2, now=1002)
    for owner, fence in [
        ("two", claim["fence"]),
        ("one", claim["fence"] + 1),
        ("one", True),
    ]:
        with pytest.raises(ValueError, match="claim"):
            box.complete(row["id"], owner, fence, "Resposta", now=1003)
    with pytest.raises(ValueError, match="claim"):
        box.complete(row["id"], "one", claim["fence"], "Resposta", now=1004)
    with pytest.raises(ValueError, match="unavailable"):
        box.claim(row["id"], "one", now=1004)
    assert box.reference(row["reference"], now=1004)["status"] == "uncertain"
    assert box.dispatch_pending(lambda _: pytest.fail("no output"), now=1004) is None


def test_deadline_and_stop_mark_inflight_uncertain_pending_expired(box):
    opened = session(box, duration_seconds=10)
    first, second = receive(box), receive(box, msgid="opaque-2")
    claim = box.claim(first["id"], "one", lease_seconds=600, now=1002)
    assert claim["claim_expires_at"] == opened["expires_at"]
    assert box.status(now=1010)["requests"] == {"expired": 1, "uncertain": 1}
    assert receive(box, now=1010, msgid="late") is None
    with pytest.raises(ValueError):
        box.complete(first["id"], "one", claim["fence"], "too late", now=1010)
    with pytest.raises(ValueError):
        box.claim(second["id"], "one", now=1010)
    opened = session(box, now=1011)
    first = receive(box, now=1012, msgid="next-session")
    second = receive(box, now=1012, msgid="next-session-2")
    box.claim(first["id"], "one", now=1013)
    closed = box.close_session(opened["id"], now=1014)
    assert closed["close_reason"] == "operator"
    assert box.reference(first["reference"], now=1014)["status"] == "uncertain"
    assert box.reference(second["reference"], now=1014)["status"] == "expired"


def test_explicit_failed_and_uncertain_states_are_terminal(box):
    session(box)
    for index, uncertain in enumerate([False, True]):
        row = receive(box, msgid=f"id-{index}")
        claim = box.claim(row["id"], "one", now=1002)
        failed = box.fail(
            row["id"],
            "one",
            claim["fence"],
            "Falha simulada",
            uncertain=uncertain,
            now=1003,
        )
        assert failed["status"] == ("uncertain" if uncertain else "failed")
        with pytest.raises(ValueError):
            box.claim(row["id"], "one", now=1004)
        with pytest.raises(ValueError):
            box.complete(row["id"], "one", claim["fence"], "Resposta", now=1004)


def test_answer_persists_before_publication_and_each_frame_has_one_attempt(box, config):
    session(box)
    row = answer(box, "Resposta detalhada. " * 30)
    assert row["response"] == ("Resposta detalhada. " * 30).strip()
    assert row["status"] == "answered" and row["delivery_status"] == "pending"
    sent = []
    frame_count = box.status(now=1004)["queued_frames"]
    assert frame_count > 1
    for _ in range(frame_count):
        result = box.dispatch_pending(lambda lines: sent.extend(lines), now=1004)
        assert result["id"] == row["id"]
        assert (
            box.dispatch_pending(
                lambda _: pytest.fail("uncertain output must block"), now=1004
            )
            is None
        )
        box.mark_socket_sent(row["id"], now=1004)
    assert len(sent) == frame_count
    assert box.reference(row["reference"], now=1004)["delivery_status"] == "socket_sent"
    assert (
        HumanMailbox(Mailbox(config)).dispatch_pending(
            lambda _: pytest.fail("no resend"), now=1005
        )
        is None
    )


def test_socket_failure_or_restart_never_blindly_retries_or_sends_remaining_fragments(
    box, config
):
    session(box)
    row = answer(box, "Falha de publicação. " * 30)

    def fail_write(lines):
        assert len(lines) == 1
        raise OSError("socket failure")

    with pytest.raises(OSError):
        box.dispatch_pending(fail_write, now=1004)
    restarted = HumanMailbox(Mailbox(config))
    assert (
        restarted.reference(row["reference"], now=1004)["delivery_status"]
        == "output_uncertain"
    )
    assert (
        restarted.dispatch_pending(lambda _: pytest.fail("no retries"), now=1004)
        is None
    )


@pytest.mark.parametrize("ending", ["stop", "deadline", "lease"])
def test_persisted_answer_is_not_published_after_session_or_lease_ends(box, ending):
    opened = session(box, duration_seconds=10)
    row = answer(box, lease_seconds=3)
    if ending == "stop":
        box.close_session(opened["id"], now=1004)
        now = 1004
    else:
        now = 1010 if ending == "deadline" else 1005
    assert box.dispatch_pending(lambda _: pytest.fail("late answer"), now=now) is None
    assert box.reference(row["reference"], now=now)["delivery_status"] == "cancelled"


def test_stop_between_durable_reservation_and_socket_write_cancels_output(
    box, monkeypatch
):
    opened = session(box)
    row = answer(box)
    original = box._maintain
    calls = 0

    def stop_during_second_transaction(db, now):
        nonlocal calls
        calls += 1
        if calls == 2:
            db.execute(
                "UPDATE human_sessions SET closed_at=?,admission_open=0,close_reason='operator' WHERE id=?",
                (now, opened["id"]),
            )
        original(db, now)

    monkeypatch.setattr(box, "_maintain", stop_during_second_transaction)
    assert (
        box.dispatch_pending(lambda _: pytest.fail("stopped session"), now=1004) is None
    )
    assert box.reference(row["reference"], now=1004)["delivery_status"] == "cancelled"


def test_concurrent_stop_cannot_commit_between_final_validation_and_write(box, config):
    opened = session(box)
    answer(box)
    other = HumanMailbox(Mailbox(config))
    callback_entered, stop_started, stop_done = (threading.Event() for _ in range(3))
    writes = []

    def stop():
        assert callback_entered.wait(timeout=5)
        stop_started.set()
        result = other.close_session(opened["id"], now=1004)
        stop_done.set()
        return result

    def write(lines):
        callback_entered.set()
        assert stop_started.wait(timeout=5)
        assert not stop_done.is_set()
        writes.extend(lines)

    with ThreadPoolExecutor(max_workers=1) as pool:
        stopping = pool.submit(stop)
        assert box.dispatch_pending(write, now=1004)
        assert stopping.result(timeout=5)["closed_at"] == 1004
    assert len(writes) == 1 and stop_done.is_set()
    assert (
        box.dispatch_pending(lambda _: pytest.fail("post-stop write"), now=1005) is None
    )


def test_capacity_rate_retention_and_reference_counters_are_bounded(config):
    box = HumanMailbox(Mailbox(replace(config, capacity=2)))
    session(box)
    first, second = receive(box), receive(box, msgid="opaque-2")
    with pytest.raises(ValueError, match="full"):
        receive(box, msgid="opaque-3")
    assert box.status(now=1001)["requests"] == {"received": 2}
    assert box.status(now=1001 + config.retention_seconds + 601)["requests"] == {}
    assert box.status(now=1001 + config.retention_seconds + 601)["sessions"] == []
    session(box, now=1001 + config.retention_seconds + 602)
    row = receive(box, msgid="new", now=1001 + config.retention_seconds + 603)
    assert int(row["reference"].split("-")[1]) > int(second["reference"].split("-")[1])
    with pytest.raises(ValueError, match="missing"):
        box.reference(first["reference"], now=1001 + config.retention_seconds + 603)


def test_human_capacity_and_rate_share_existing_peer_limits(config, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1000)
    mailbox = Mailbox(replace(config, capacity=2))
    box = HumanMailbox(mailbox)
    session(box)
    mailbox.send("peer", "peer question")
    assert receive(box)
    with pytest.raises(ValueError, match="full"):
        receive(box, msgid="no-space")
    with mailbox.connection() as db:
        db.execute("DELETE FROM requests")
        db.execute("DELETE FROM human_requests")
        db.execute("INSERT OR REPLACE INTO rate_limits VALUES ('leandro',960,6)")
    assert receive(box, msgid="rate-capped", now=1001) is None


def test_legacy_schema_and_peer_frames_remain_usable_after_additive_migration(
    config, monkeypatch
):
    monkeypatch.setattr("time.time", lambda: 1000)
    old = Mailbox(replace(config, human_queries=False))
    request_id = old.send("peer", "peer question")
    frame = old.next_outgoing()
    with old.connection() as db:
        before = dict(
            db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")
        )
    box = HumanMailbox(Mailbox(config))
    session(box)
    assert receive(box)
    downgraded = Mailbox(replace(config, human_queries=False))
    with downgraded.connection() as db:
        after = dict(
            db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")
        )
        assert all(after[key] == value for key, value in before.items())
    assert downgraded.next_outgoing() == frame
    parsed = parse_envelope(frame[1])
    assert downgraded.receive(
        Envelope("reply", "local", request_id, parsed.timestamp, "peer answer"),
        "rbx-peer",
        now=1001,
    )
    assert downgraded.inbox()[0]["response"] == "peer answer"
    assert box.inbox(now=1002)[0]["origin"] == "human"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duration_seconds": 0},
        {"duration_seconds": 601},
        {"duration_seconds": True},
        {"max_questions": 0},
        {"max_questions": 4},
    ],
)
def test_session_bounds(box, kwargs):
    with pytest.raises(ValueError):
        session(box, **kwargs)


def test_session_identity_provenance_and_one_active_alias(box):
    for fields in [
        ("other", "leandro", "rbx-flightdeck", "a" * 40, "gpt-6.1-sol"),
        ("flightdeck-dev", "unknown", "rbx-flightdeck", "a" * 40, "gpt-6.1-sol"),
        ("flightdeck-dev", "leandro", "other", "a" * 40, "gpt-6.1-sol"),
        ("flightdeck-dev", "leandro", "rbx-flightdeck", "short", "gpt-6.1-sol"),
        ("flightdeck-dev", "leandro", "rbx-flightdeck", "a" * 40, "bad model"),
    ]:
        with pytest.raises(ValueError):
            box.open_session(*fields, now=1000)
    session(box)
    with pytest.raises(ValueError, match="active"):
        session(box)


@pytest.mark.parametrize(
    "operation", ["open", "receive", "claim", "complete", "fail", "dispatch"]
)
def test_current_time_is_refreshed_after_waiting_for_database_lock(
    box, monkeypatch, operation
):
    clock = [1003]
    monkeypatch.setattr("time.time", lambda: clock[0])
    row = None
    if operation != "open":
        session(box, duration_seconds=10)
        if operation == "dispatch":
            row = answer(box)
        elif operation != "receive":
            row = receive(box)
            if operation in {"complete", "fail"}:
                row = box.claim(row["id"], "one", now=1002)
    original = box.mailbox.connection

    @contextmanager
    def acquire_after_deadline():
        with original() as db:
            clock[0] = 1010
            yield db

    monkeypatch.setattr(box.mailbox, "connection", acquire_after_deadline)
    if operation == "open":
        assert session(box, now=None)["created_at"] == 1010
    elif operation == "receive":
        assert (
            box.receive("flightdeck-dev", "leandro", "locked", 1003, "question") is None
        )
    elif operation == "dispatch":
        assert (
            box.dispatch_pending(lambda _: pytest.fail("expired before lock")) is None
        )
    else:
        with pytest.raises(ValueError):
            if operation == "claim":
                box.claim(row["id"], "one")
            elif operation == "complete":
                box.complete(row["id"], "one", row["fence"], "late answer")
            else:
                box.fail(row["id"], "one", row["fence"], "late failure")


@pytest.mark.parametrize("explicit", [False, True])
def test_uncertain_provider_result_closes_admission_and_expires_queued_work(
    box, explicit
):
    opened = session(box)
    first = receive(box)
    second = receive(box, msgid="second")
    claim = box.claim(first["id"], "one", lease_seconds=2, now=1002)
    if explicit:
        box.fail(
            first["id"],
            "one",
            claim["fence"],
            "invocation outcome unknown",
            uncertain=True,
            now=1003,
        )
    with pytest.raises(ValueError):
        box.claim(second["id"], "one", now=1004)
    assert receive(box, msgid="third", now=1004) is None
    assert box.reference(first["reference"], now=1004)["status"] == "uncertain"
    assert box.reference(second["reference"], now=1004)["status"] == "expired"
    closed = box.status(now=1004)["sessions"][0]
    assert closed["id"] == opened["id"] and closed["close_reason"] == "uncertain"
    assert closed["admission_open"] == 0


def test_stop_during_drain_keeps_cancelled_sibling_frames_cancelled(box):
    opened = session(box)
    row = answer(box, "Resposta longa. " * 30)
    assert box.dispatch_pending(lambda _: None, now=1004)
    box.close_session(opened["id"], now=1004)
    result = box.mark_socket_sent(row["id"], now=1005)
    assert result["delivery_status"] == "cancelled"
    assert box.status(now=1005)["queued_frames"] == 0
    assert (
        box.dispatch_pending(lambda _: pytest.fail("cancelled sibling"), now=1005)
        is None
    )


@pytest.mark.parametrize("stage", ["received", "in_progress", "answered"])
def test_revoked_human_account_invalidates_persisted_session(box, config, stage):
    session(box)
    row = receive(box)
    if stage != "received":
        row = box.claim(row["id"], "one", now=1002)
    if stage == "answered":
        row = box.complete(row["id"], "one", row["fence"], "Resposta.", now=1003)
    revoked = HumanMailbox(
        Mailbox(replace(config, operators=frozenset({"other-human"})))
    )
    if stage == "received":
        with pytest.raises(ValueError):
            revoked.claim(row["id"], "one", now=1004)
    elif stage == "in_progress":
        with pytest.raises(ValueError):
            revoked.complete(row["id"], "one", row["fence"], "Resposta.", now=1004)
    else:
        assert (
            revoked.dispatch_pending(
                lambda _: pytest.fail("revoked operator"), now=1004
            )
            is None
        )
    closed = revoked.status(now=1004)["sessions"][0]
    assert closed["close_reason"] == "authorization_changed"
    expected = {
        "received": "expired",
        "in_progress": "uncertain",
        "answered": "answered",
    }[stage]
    assert revoked.reference(row["reference"], now=1004)["status"] == expected
    if stage == "answered":
        assert (
            revoked.reference(row["reference"], now=1004)["delivery_status"]
            == "cancelled"
        )


def test_stop_before_later_fragment_preserves_already_sent_frame_evidence(
    box, monkeypatch
):
    opened = session(box)
    row = answer(box, "Resposta longa. " * 30)
    assert box.dispatch_pending(lambda _: None, now=1004)
    box.mark_socket_sent(row["id"], now=1004)
    original = box._maintain
    calls = 0

    def stop_before_second_fragment(db, now):
        nonlocal calls
        calls += 1
        if calls == 2:
            db.execute(
                "UPDATE human_sessions SET closed_at=?,admission_open=0,close_reason='operator' WHERE id=?",
                (now, opened["id"]),
            )
        original(db, now)

    monkeypatch.setattr(box, "_maintain", stop_before_second_fragment)
    assert (
        box.dispatch_pending(
            lambda _: pytest.fail("stopped after first fragment"), now=1005
        )
        is None
    )
    with box.mailbox.connection() as db:
        frames = db.execute(
            "SELECT status,socket_sent_at FROM human_outbox WHERE request_id=? ORDER BY part",
            (row["id"],),
        ).fetchall()
    assert frames[0]["status"] == "socket_sent" and frames[0]["socket_sent_at"] == 1004
    assert all(frame["status"] == "cancelled" for frame in frames[1:])


@pytest.mark.parametrize("changed", [{"server": "127.0.0.1:7777"}, {"tls": True}])
def test_human_transport_identity_prevents_reuse_on_other_endpoint(
    box, config, changed
):
    opened = session(box)
    row = answer(box)
    same = HumanMailbox(Mailbox(config))
    assert same.status(now=1004)["sessions"][0]["id"] == opened["id"]
    assert same.reference(row["reference"], now=1004)["response"] == row["response"]
    with pytest.raises(ValueError, match="different IRC transport"):
        HumanMailbox(Mailbox(replace(config, **changed)))
    # The opt-in extension never changes legacy transport identity behavior.
    disabled = HumanMailbox(Mailbox(replace(config, human_queries=False, **changed)))
    assert disabled.status()["enabled"] is False
    assert (
        HumanMailbox(Mailbox(config)).reference(row["reference"], now=1004)[
            "delivery_status"
        ]
        == "pending"
    )


def test_disabled_human_mode_does_not_record_transport_identity(config):
    mailbox = Mailbox(replace(config, human_queries=False))
    HumanMailbox(mailbox)
    with mailbox.connection() as db:
        assert (
            db.execute(
                "SELECT value FROM meta WHERE key='human_transport_identity'"
            ).fetchone()
            is None
        )
    HumanMailbox(Mailbox(config))
    with mailbox.connection() as db:
        assert db.execute(
            "SELECT value FROM meta WHERE key='human_transport_identity'"
        ).fetchone()
