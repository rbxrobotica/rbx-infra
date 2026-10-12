"""Bounded, opt-in human sessions in the existing private agent mailbox.

Claims are deliberately not recoverable: a lost lease may hide a completed
external call. Publication similarly permits one automatic attempt, not exactly
once delivery. No model or provider is invoked by this module.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from collections.abc import Callable

from .agent_protocol import TEXT_BYTES, clean_text
from .agent_store import Mailbox


class HumanMailbox:
    def __init__(self, mailbox: Mailbox) -> None:
        self.mailbox = mailbox
        self.config = mailbox.config
        if not self.enabled:
            return
        # Additive tables only: old Mailbox readers/writers retain their schema.
        with mailbox.connection() as db:
            transport = json.dumps(
                {"server": self.config.server, "tls": self.config.tls},
                sort_keys=True,
                separators=(",", ":"),
            )
            db.execute(
                "INSERT OR IGNORE INTO meta VALUES ('human_transport_identity', ?)",
                (transport,),
            )
            recorded = db.execute(
                "SELECT value FROM meta WHERE key='human_transport_identity'"
            ).fetchone()[0]
            if recorded != transport:
                raise ValueError("human mailbox belongs to a different IRC transport")
            for statement in (
                """CREATE TABLE IF NOT EXISTS human_meta (
                    key TEXT PRIMARY KEY, value INTEGER NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS human_sessions (
                    id TEXT PRIMARY KEY, alias TEXT NOT NULL, account TEXT NOT NULL,
                    channel TEXT NOT NULL, project TEXT NOT NULL, revision TEXT NOT NULL,
                    model TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
                    max_questions INTEGER NOT NULL, accepted INTEGER NOT NULL DEFAULT 0,
                    admission_open INTEGER NOT NULL DEFAULT 1, owner TEXT,
                    closed_at REAL, close_reason TEXT)""",
                """CREATE TABLE IF NOT EXISTS human_requests (
                    id TEXT PRIMARY KEY, reference TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL, account TEXT NOT NULL,
                    msgid TEXT NOT NULL UNIQUE, server_time REAL NOT NULL,
                    received_at REAL NOT NULL, text TEXT NOT NULL,
                    status TEXT NOT NULL, owner TEXT, fence INTEGER,
                    claim_expires_at REAL, response TEXT, error TEXT,
                    completed_at REAL, delivery_status TEXT NOT NULL DEFAULT 'none')""",
                """CREATE TABLE IF NOT EXISTS human_outbox (
                    id INTEGER PRIMARY KEY, request_id TEXT NOT NULL,
                    part INTEGER NOT NULL, line TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempted_at REAL,
                    socket_sent_at REAL, UNIQUE(request_id, part))""",
                "CREATE INDEX IF NOT EXISTS human_request_session ON human_requests(session_id)",
            ):
                db.execute(statement)

    @property
    def enabled(self) -> bool:
        return getattr(self.config, "human_queries", False) is True

    def _enabled(self) -> None:
        if not self.enabled:
            raise ValueError("human queries are disabled")

    @staticmethod
    def _now(now: float | None) -> float:
        value = time.time() if now is None else now
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("invalid current time")
        return float(value)

    @staticmethod
    def _bounded(value: int, name: str, maximum: int) -> None:
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be between 1 and {maximum}")

    def _account(self, account: str) -> str | None:
        if not isinstance(account, str):
            return None
        account = account.casefold()
        agents = {self.config.account.casefold()} | {
            a.casefold() for a in self.config.peers.values()
        }
        return (
            account
            if account in self.config.operators and account not in agents
            else None
        )

    @staticmethod
    def _counter(db: sqlite3.Connection, key: str) -> int:
        db.execute("INSERT OR IGNORE INTO human_meta VALUES (?, 0)", (key,))
        db.execute("UPDATE human_meta SET value=value+1 WHERE key=?", (key,))
        return db.execute(
            "SELECT value FROM human_meta WHERE key=?", (key,)
        ).fetchone()[0]

    @staticmethod
    def _session(row: sqlite3.Row) -> dict:
        return dict(row) | {"session_id": row["id"]}

    @staticmethod
    def _request(db: sqlite3.Connection, request_id: str) -> dict:
        row = db.execute(
            """SELECT r.*, s.alias, s.channel, s.project, s.revision, s.model,
                s.created_at AS session_created_at, s.expires_at AS session_expires_at,
                s.closed_at AS session_closed_at, s.close_reason AS session_close_reason
                FROM human_requests r JOIN human_sessions s ON s.id=r.session_id
                WHERE r.id=?""",
            (request_id,),
        ).fetchone()
        if row is None:
            raise ValueError("human request is missing or past retention")
        return dict(row) | {
            "request_id": row["id"],
            "origin": "human",
            "untrusted_content": True,
        }

    def _maintain(self, db: sqlite3.Connection, now: float) -> None:
        self.mailbox._expire(db, int(now))
        revoked = [
            (now, row["id"])
            for row in db.execute(
                "SELECT id,account FROM human_sessions WHERE closed_at IS NULL"
            )
            if self._account(row["account"]) is None
        ]
        db.executemany(
            """UPDATE human_sessions SET admission_open=0,closed_at=?,close_reason='authorization_changed'
                WHERE id=?""",
            revoked,
        )
        db.execute(
            """UPDATE human_sessions SET admission_open=0,closed_at=expires_at,
                close_reason='deadline' WHERE closed_at IS NULL AND expires_at<=?""",
            (now,),
        )
        db.execute(
            """UPDATE human_requests SET status='uncertain',error='claim or session ended'
                WHERE status='in_progress' AND (claim_expires_at<=? OR session_id IN
                (SELECT id FROM human_sessions WHERE closed_at IS NOT NULL))""",
            (now,),
        )
        db.execute(
            """UPDATE human_sessions SET admission_open=0,closed_at=?,close_reason='uncertain'
                WHERE closed_at IS NULL AND id IN
                (SELECT session_id FROM human_requests WHERE status='uncertain')""",
            (now,),
        )
        db.execute(
            """UPDATE human_requests SET status='expired' WHERE status='received'
                AND session_id IN (SELECT id FROM human_sessions WHERE closed_at IS NOT NULL)""",
        )
        db.execute(
            """UPDATE human_outbox SET status='cancelled' WHERE status='pending'
                AND request_id IN (SELECT r.id FROM human_requests r
                JOIN human_sessions s ON s.id=r.session_id
                WHERE s.closed_at IS NOT NULL OR r.claim_expires_at<=?)""",
            (now,),
        )
        db.execute(
            """UPDATE human_requests SET delivery_status='cancelled'
                WHERE delivery_status='pending' AND id IN
                (SELECT request_id FROM human_outbox WHERE status='cancelled')""",
        )
        cutoff = now - self.config.retention_seconds
        db.execute(
            "DELETE FROM human_outbox WHERE request_id IN (SELECT id FROM human_requests WHERE received_at<?)",
            (cutoff,),
        )
        db.execute("DELETE FROM human_requests WHERE received_at<?", (cutoff,))
        db.execute(
            """DELETE FROM human_sessions WHERE expires_at<? AND id NOT IN
                (SELECT session_id FROM human_requests)""",
            (cutoff,),
        )

    def open_session(
        self,
        alias: str,
        account: str,
        project: str,
        revision: str,
        model: str,
        duration_seconds: int = 600,
        max_questions: int = 3,
        now: float | None = None,
    ) -> dict:
        self._enabled()
        requested_now = now
        now = self._now(now)
        self._bounded(
            duration_seconds, "duration_seconds", min(600, self.config.ttl_seconds)
        )
        self._bounded(max_questions, "max_questions", 3)
        account = self._account(account)
        if account is None:
            raise ValueError("session requires an authorized human account")
        if alias != "flightdeck-dev" or project != "rbx-flightdeck":
            raise ValueError(
                "only flightdeck-dev / rbx-flightdeck documentary sessions are supported"
            )
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("revision must be a full lowercase Git SHA")
        if not isinstance(model, str) or not re.fullmatch(
            r"[A-Za-z0-9._-]{1,80}", model
        ):
            raise ValueError("invalid explicit model")
        with self.mailbox.connection() as db:
            now = self._now(requested_now)
            self._maintain(db, now)
            if db.execute(
                "SELECT 1 FROM human_sessions WHERE alias=? AND closed_at IS NULL",
                (alias,),
            ).fetchone():
                raise ValueError("alias already has an active session")
            if (
                db.execute("SELECT count(*) FROM human_sessions").fetchone()[0]
                >= self.config.capacity
            ):
                raise ValueError(
                    "human session capacity is full; wait for retention cleanup"
                )
            session_id = uuid.uuid4().hex
            db.execute(
                """INSERT INTO human_sessions
                    (id,alias,account,channel,project,revision,model,created_at,expires_at,max_questions)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id,
                    alias,
                    account,
                    self.config.channel,
                    project,
                    revision,
                    model,
                    now,
                    now + duration_seconds,
                    max_questions,
                ),
            )
            return self._session(
                db.execute(
                    "SELECT * FROM human_sessions WHERE id=?", (session_id,)
                ).fetchone()
            )

    def close_session(self, session_id: str, now: float | None = None) -> dict:
        self._enabled()
        requested_now = now
        now = self._now(now)
        with self.mailbox.connection() as db:
            now = self._now(requested_now)
            self._maintain(db, now)
            if not db.execute(
                "SELECT 1 FROM human_sessions WHERE id=?", (session_id,)
            ).fetchone():
                raise ValueError("human session is missing")
            db.execute(
                """UPDATE human_sessions SET admission_open=0,closed_at=?,close_reason='operator'
                    WHERE id=? AND closed_at IS NULL""",
                (now, session_id),
            )
            self._maintain(db, now)
            return self._session(
                db.execute(
                    "SELECT * FROM human_sessions WHERE id=?", (session_id,)
                ).fetchone()
            )

    def receive(
        self,
        alias: str,
        account: str,
        msgid: str,
        server_time: float,
        text: str,
        now: float | None = None,
    ) -> dict | None:
        self._enabled()
        requested_now = now
        now = self._now(now)
        account = self._account(account)
        if account is None or alias != "flightdeck-dev":
            return None
        if (
            not isinstance(msgid, str)
            or not 1 <= len(msgid) <= 128
            or msgid.startswith(":")
            or any(c.isspace() or ord(c) <= 32 or ord(c) == 127 for c in msgid)
        ):
            return None
        if isinstance(server_time, bool) or not isinstance(server_time, (float, int)):
            return None
        try:
            if len(msgid.encode("utf-8")) > 128:
                return None
            server_time = self._now(server_time)
            clean_text(text)
            if len(text.encode("utf-8")) > TEXT_BYTES:
                return None
        except (ValueError, UnicodeError):
            return None
        with self.mailbox.connection() as db:
            now = self._now(requested_now)
            self._maintain(db, now)
            existing = db.execute(
                "SELECT id FROM human_requests WHERE msgid=?", (msgid,)
            ).fetchone()
            if existing:
                row = self._request(db, existing["id"])
                if (row["account"], row["alias"], row["server_time"], row["text"]) != (
                    account,
                    alias,
                    server_time,
                    text,
                ):
                    return None
                return row | {"duplicate": True}
            if not now - 30 <= server_time <= now + 5:
                return None
            session = db.execute(
                """SELECT * FROM human_sessions WHERE alias=? AND account=?
                    AND closed_at IS NULL AND admission_open=1 AND created_at<=?
                    AND expires_at>?""",
                (alias, account, server_time, now),
            ).fetchone()
            if session is None or server_time >= session["expires_at"]:
                return None
            count = db.execute(
                "SELECT (SELECT count(*) FROM requests)+(SELECT count(*) FROM human_requests)"
            ).fetchone()[0]
            if count >= self.config.capacity:
                raise ValueError("mailbox is full; wait for retention cleanup")
            window = now - now % 60
            rate = db.execute(
                "SELECT window,count FROM rate_limits WHERE account=?", (account,)
            ).fetchone()
            count = rate["count"] if rate and rate["window"] == window else 0
            if count >= 6:
                return None
            db.execute(
                "INSERT OR REPLACE INTO rate_limits VALUES (?,?,?)",
                (account, window, count + 1),
            )
            request_id = uuid.uuid4().hex
            reference = f"FD-{self._counter(db, 'reference'):04d}"
            db.execute(
                """INSERT INTO human_requests
                    (id,reference,session_id,account,msgid,server_time,received_at,text,status)
                    VALUES (?,?,?,?,?,?,?,?,'received')""",
                (
                    request_id,
                    reference,
                    session["id"],
                    account,
                    msgid,
                    server_time,
                    now,
                    text,
                ),
            )
            db.execute(
                """UPDATE human_sessions SET accepted=accepted+1,
                    admission_open=CASE WHEN accepted+1>=max_questions THEN 0 ELSE 1 END
                    WHERE id=?""",
                (session["id"],),
            )
            return self._request(db, request_id) | {"duplicate": False}

    def inbox(self, limit: int = 20, now: float | None = None) -> list[dict]:
        self._enabled()
        self._bounded(limit, "limit", 100)
        with self.mailbox.connection() as db:
            self._maintain(db, self._now(now))
            rows = db.execute(
                "SELECT id FROM human_requests WHERE status='received' ORDER BY received_at,id LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._request(db, row["id"]) for row in rows]

    def claim(
        self,
        request_id: str,
        owner: str,
        lease_seconds: int = 60,
        now: float | None = None,
    ) -> dict:
        self._enabled()
        requested_now = now
        now = self._now(now)
        self._bounded(lease_seconds, "lease_seconds", 600)
        if not isinstance(owner, str) or not re.fullmatch(
            r"[A-Za-z0-9._:-]{1,80}", owner
        ):
            raise ValueError("invalid claim owner")
        with self.mailbox.connection() as db:
            now = self._now(requested_now)
            self._maintain(db, now)
            row = self._request(db, request_id)
            if row["status"] != "received" or row["session_closed_at"] is not None:
                raise ValueError("human request is unavailable for claim")
            session = db.execute(
                "SELECT owner FROM human_sessions WHERE id=?", (row["session_id"],)
            ).fetchone()
            if session["owner"] not in (None, owner):
                raise ValueError("session is pinned to a different consumer")
            if db.execute(
                "SELECT 1 FROM human_requests WHERE session_id=? AND status='in_progress'",
                (row["session_id"],),
            ).fetchone():
                raise ValueError("session already has an in-progress claim")
            fence = self._counter(db, "fence")
            db.execute(
                "UPDATE human_sessions SET owner=? WHERE id=?",
                (owner, row["session_id"]),
            )
            db.execute(
                """UPDATE human_requests SET status='in_progress',owner=?,fence=?,claim_expires_at=?
                    WHERE id=?""",
                (
                    owner,
                    fence,
                    min(now + lease_seconds, row["session_expires_at"]),
                    request_id,
                ),
            )
            return self._request(db, request_id)

    @staticmethod
    def _check_claim(row: dict, owner: str, fence: int, now: float) -> None:
        if (
            row["status"] != "in_progress"
            or type(fence) is not int
            or row["owner"] != owner
            or row["fence"] != fence
            or row["claim_expires_at"] <= now
            or row["session_closed_at"] is not None
            or row["session_expires_at"] <= now
        ):
            raise ValueError("claim owner, fence, lease or session is no longer valid")

    def complete(
        self,
        request_id: str,
        owner: str,
        fence: int,
        text: str,
        now: float | None = None,
    ) -> dict:
        self._enabled()
        requested_now = now
        now = self._now(now)
        text = clean_text(text)
        from .human_protocol import render_human_reply

        with self.mailbox.connection() as db:
            now = self._now(requested_now)
            self._maintain(db, now)
            row = self._request(db, request_id)
            self._check_claim(row, owner, fence, now)
            lines = render_human_reply(
                row["alias"], row["account"], row["reference"], text, row["channel"]
            )
            for part, line in enumerate(lines, 1):
                db.execute(
                    "INSERT INTO human_outbox(request_id,part,line) VALUES (?,?,?)",
                    (request_id, part, line),
                )
            db.execute(
                """UPDATE human_requests SET status='answered',response=?,completed_at=?,delivery_status='pending'
                    WHERE id=?""",
                (text, now, request_id),
            )
            return self._request(db, request_id)

    def fail(
        self,
        request_id: str,
        owner: str,
        fence: int,
        reason: str,
        uncertain: bool = False,
        now: float | None = None,
    ) -> dict:
        self._enabled()
        requested_now = now
        now = self._now(now)
        reason = clean_text(reason)
        if type(uncertain) is not bool or len(reason.encode("utf-8")) > 240:
            raise ValueError(
                "failure reason exceeds 240 bytes or uncertain is not boolean"
            )
        with self.mailbox.connection() as db:
            now = self._now(requested_now)
            self._maintain(db, now)
            row = self._request(db, request_id)
            self._check_claim(row, owner, fence, now)
            db.execute(
                "UPDATE human_requests SET status=?,error=?,completed_at=? WHERE id=?",
                ("uncertain" if uncertain else "failed", reason, now, request_id),
            )
            self._maintain(db, now)
            return self._request(db, request_id)

    def reference(self, ref: str, now: float | None = None) -> dict:
        self._enabled()
        with self.mailbox.connection() as db:
            self._maintain(db, self._now(now))
            row = db.execute(
                "SELECT id FROM human_requests WHERE reference=? OR id=?", (ref, ref)
            ).fetchone()
            if row is None:
                raise ValueError("human reference is missing or past retention")
            return self._request(db, row["id"])

    def status(self, now: float | None = None) -> dict:
        if not self.enabled:
            return {
                "enabled": False,
                "sessions": [],
                "requests": {},
                "queued_frames": 0,
            }
        with self.mailbox.connection() as db:
            self._maintain(db, self._now(now))
            return {
                "enabled": True,
                "sessions": [
                    self._session(row)
                    for row in db.execute(
                        "SELECT * FROM human_sessions ORDER BY created_at,id"
                    )
                ],
                "requests": dict(
                    db.execute(
                        "SELECT status,count(*) FROM human_requests GROUP BY status"
                    )
                ),
                "queued_frames": db.execute(
                    "SELECT count(*) FROM human_outbox WHERE status='pending'"
                ).fetchone()[0],
                "delivery": dict(
                    db.execute(
                        "SELECT delivery_status,count(*) FROM human_requests GROUP BY delivery_status"
                    )
                ),
            }

    def dispatch_pending(
        self,
        sender: Callable[[list[str]], None],
        now: float | None = None,
    ) -> dict | None:
        """Reserve once, then fence synchronous socket writes against session stop.

        ``sender`` must synchronously write the supplied single body without
        awaiting drain. The caller awaits drain afterwards, then calls mark_socket_sent.
        Crashes, callback exceptions and lost acknowledgements never cause an
        automatic resend. A socket write does not prove client display or reading.
        """
        self._enabled()
        instant = self._now(now)
        with self.mailbox.connection() as db:
            instant = self._now(now)
            self._maintain(db, instant)
            pending = db.execute(
                """SELECT id,request_id FROM human_outbox WHERE status='pending'
                    AND request_id NOT IN (SELECT request_id FROM human_outbox
                    WHERE status='output_uncertain') ORDER BY id LIMIT 1""",
            ).fetchone()
            if pending is None:
                return None
            request_id = pending["request_id"]
            frame_id = pending["id"]
            db.execute(
                "UPDATE human_outbox SET status='output_uncertain',attempted_at=? WHERE id=?",
                (instant, frame_id),
            )
            db.execute(
                "UPDATE human_requests SET delivery_status='output_uncertain' WHERE id=?",
                (request_id,),
            )
        # The first commit is the durable at-most-once reservation. The second
        # transaction prevents a concurrent close from crossing the socket write.
        with self.mailbox.connection() as db:
            instant = self._now(now)
            self._maintain(db, instant)
            row = self._request(db, request_id)
            if (
                row["status"] != "answered"
                or row["session_closed_at"] is not None
                or row["session_expires_at"] <= instant
                or row["claim_expires_at"] <= instant
            ):
                db.execute(
                    "UPDATE human_outbox SET status='cancelled' WHERE request_id=? AND status IN ('pending','output_uncertain')",
                    (request_id,),
                )
                db.execute(
                    "UPDATE human_requests SET delivery_status='cancelled' WHERE id=?",
                    (request_id,),
                )
                return None
            lines = [
                db.execute(
                    "SELECT line FROM human_outbox WHERE id=?", (frame_id,)
                ).fetchone()["line"]
            ]
            sender(lines)
            return self._request(db, request_id)

    def mark_socket_sent(self, request_id: str, now: float | None = None) -> dict:
        """Record successful drain, without claiming human receipt or display."""
        self._enabled()
        requested_now = now
        now = self._now(now)
        with self.mailbox.connection() as db:
            now = self._now(requested_now)
            self._maintain(db, now)
            row = self._request(db, request_id)
            if row["delivery_status"] != "output_uncertain":
                raise ValueError("human output has no uncertain socket attempt")
            db.execute(
                "UPDATE human_outbox SET status='socket_sent',socket_sent_at=? WHERE request_id=? AND status='output_uncertain'",
                (now, request_id),
            )
            states = {
                r[0]
                for r in db.execute(
                    "SELECT status FROM human_outbox WHERE request_id=?", (request_id,)
                )
            }
            delivery = (
                "cancelled"
                if "cancelled" in states
                else "pending" if "pending" in states else "socket_sent"
            )
            db.execute(
                "UPDATE human_requests SET delivery_status=? WHERE id=?",
                (delivery, request_id),
            )
            return self._request(db, request_id)
