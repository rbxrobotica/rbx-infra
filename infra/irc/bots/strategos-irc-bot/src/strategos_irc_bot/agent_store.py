"""Private durable mailbox, shared by the IRC daemon and local CLI/MCP clients."""

from __future__ import annotations

import os
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from typing import Iterator

from .agent_config import AgentConfig
from .agent_protocol import TEXT_BYTES, Envelope, chunks, clean_text, render


class Mailbox:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        path = config.state_dir
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("state_dir must be an owned private directory (0700)")
        self.path = path / "mailbox.sqlite3"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise ValueError("mailbox must be an owned private file (0600)")
        finally:
            os.close(fd)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, direction TEXT NOT NULL, account TEXT NOT NULL,
                    peer TEXT NOT NULL, timestamp INTEGER NOT NULL, text TEXT NOT NULL,
                    status TEXT NOT NULL, response TEXT, acknowledged INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, line TEXT NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0, last_sent INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS fragments (
                    request_id TEXT NOT NULL, part INTEGER NOT NULL, total INTEGER NOT NULL,
                    text TEXT NOT NULL, PRIMARY KEY(request_id, part)
                );
                CREATE TABLE IF NOT EXISTS rate_limits (
                    account TEXT PRIMARY KEY, window INTEGER NOT NULL, count INTEGER NOT NULL
                );
            """)
            identity = f"{config.agent_id}:{config.account}:{config.channel}"
            db.execute("INSERT OR IGNORE INTO meta VALUES ('identity', ?)", (identity,))
            if (
                db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()[0]
                != identity
            ):
                raise ValueError(
                    "state_dir belongs to a different agent/account/channel"
                )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    def _expire(self, db: sqlite3.Connection, now: int) -> None:
        db.execute(
            "UPDATE requests SET status='expired' WHERE timestamp<? AND status='pending'",
            (now - self.config.ttl_seconds,),
        )
        db.execute(
            "DELETE FROM outbox WHERE request_id IN (SELECT id FROM requests WHERE timestamp<?)",
            (now - self.config.ttl_seconds,),
        )
        db.execute(
            "DELETE FROM fragments WHERE request_id IN (SELECT id FROM requests WHERE timestamp<?)",
            (now - self.config.ttl_seconds,),
        )
        db.execute(
            "DELETE FROM requests WHERE timestamp<?",
            (now - self.config.retention_seconds,),
        )
        db.execute("DELETE FROM rate_limits WHERE window<?", (now - 60,))

    def _capacity(self, db: sqlite3.Connection) -> None:
        if (
            db.execute("SELECT count(*) FROM requests").fetchone()[0]
            >= self.config.capacity
        ):
            raise ValueError("mailbox is full; wait for retention cleanup")

    def send(self, peer: str, text: str) -> str:
        if peer not in self.config.peers:
            raise ValueError("unknown peer")
        text = clean_text(text)
        if len(text.encode("utf-8")) > TEXT_BYTES:
            raise ValueError("question exceeds 240 UTF-8 bytes")
        now, request_id = int(time.time()), uuid.uuid4().hex
        with self.connection() as db:
            self._expire(db, now)
            self._capacity(db)
            db.execute(
                "INSERT INTO requests VALUES (?, 'out', ?, ?, ?, ?, 'pending', NULL, 0)",
                (request_id, self.config.peers[peer], peer, now, text),
            )
            self._queue(db, request_id, Envelope("ask", peer, request_id, now, text))
        return request_id

    def _queue(
        self, db: sqlite3.Connection, request_id: str, envelope: Envelope
    ) -> None:
        db.execute(
            "INSERT INTO outbox(request_id, line) VALUES (?, ?)",
            (request_id, render(envelope)),
        )

    def receive(self, envelope: Envelope, account: str, now: int | None = None) -> bool:
        now = int(time.time()) if now is None else now
        account = account.casefold()
        if envelope.target != self.config.agent_id or account == self.config.account:
            return False
        if not now - self.config.ttl_seconds <= envelope.timestamp <= now + 30:
            return False
        peer = next((k for k, v in self.config.peers.items() if v == account), None)
        if account not in self.config.operators and peer is None:
            return False
        with self.connection() as db:
            self._expire(db, now)
            existing = db.execute(
                "SELECT * FROM requests WHERE id=?", (envelope.request_id,)
            ).fetchone()
            if envelope.kind == "ask":
                if existing:
                    if (
                        existing["direction"] == "in"
                        and existing["status"] == "replied"
                        and existing["account"] == account
                        and existing["timestamp"] == envelope.timestamp
                        and existing["text"] == envelope.text
                    ):
                        # A requester can retry after losing the answer during a
                        # disconnect. Resend the cached answer, never re-run work.
                        db.execute(
                            "UPDATE outbox SET sent=0 WHERE request_id=?",
                            (envelope.request_id,),
                        )
                    return False
                self._capacity(db)
                window = now - now % 60
                rate = db.execute(
                    "SELECT window,count FROM rate_limits WHERE account=?", (account,)
                ).fetchone()
                count = rate[1] if rate and rate[0] == window else 0
                if count >= 6:
                    return False
                db.execute(
                    "INSERT OR REPLACE INTO rate_limits VALUES (?, ?, ?)",
                    (account, window, count + 1),
                )
                db.execute(
                    "INSERT INTO requests VALUES (?, 'in', ?, ?, ?, ?, 'pending', NULL, 0)",
                    (
                        envelope.request_id,
                        account,
                        peer or account,
                        envelope.timestamp,
                        envelope.text,
                    ),
                )
                return True
            if (
                not existing
                or existing["direction"] != "out"
                or existing["status"] != "pending"
                or existing["account"] != account
                or existing["timestamp"] != envelope.timestamp
            ):
                return False
            parts = db.execute(
                "SELECT * FROM fragments WHERE request_id=? ORDER BY part",
                (envelope.request_id,),
            ).fetchall()
            if any(
                p["total"] != envelope.total or p["part"] == envelope.part
                for p in parts
            ):
                return False
            db.execute(
                "INSERT INTO fragments VALUES (?, ?, ?, ?)",
                (envelope.request_id, envelope.part, envelope.total, envelope.text),
            )
            parts = db.execute(
                "SELECT text FROM fragments WHERE request_id=? ORDER BY part",
                (envelope.request_id,),
            ).fetchall()
            if len(parts) == envelope.total:
                db.execute(
                    "UPDATE requests SET status='answered',response=? WHERE id=?",
                    ("".join(p[0] for p in parts), envelope.request_id),
                )
                db.execute(
                    "DELETE FROM fragments WHERE request_id=?", (envelope.request_id,)
                )
            return True

    def reply(self, request_id: str, text: str) -> None:
        text = clean_text(text)
        parts = chunks(text)
        with self.connection() as db:
            self._expire(db, int(time.time()))
            row = db.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if not row or row["direction"] != "in" or row["status"] != "pending":
                raise ValueError("request is missing, expired or already answered")
            for index, part in enumerate(parts, 1):
                self._queue(
                    db,
                    request_id,
                    Envelope(
                        "reply",
                        row["peer"],
                        request_id,
                        row["timestamp"],
                        part,
                        index,
                        len(parts),
                    ),
                )
            db.execute(
                "UPDATE requests SET status='replied',response=? WHERE id=?",
                (text, request_id),
            )

    def inbox(self, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self.connection() as db:
            self._expire(db, int(time.time()))
            rows = db.execute(
                """SELECT * FROM requests WHERE
                (direction='in' AND status='pending') OR
                (direction='out' AND status='answered' AND acknowledged=0)
                ORDER BY timestamp,id LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) | {"untrusted_content": True} for row in rows]

    def acknowledge(self, request_id: str) -> None:
        with self.connection() as db:
            changed = db.execute(
                "UPDATE requests SET acknowledged=1 WHERE id=? AND direction='out' AND status='answered'",
                (request_id,),
            ).rowcount
            if not changed:
                raise ValueError(
                    "only completed outbound responses can be acknowledged"
                )

    def next_outgoing(self) -> tuple[int, str] | None:
        with self.connection() as db:
            self._expire(db, int(time.time()))
            row = db.execute(
                """SELECT id,line FROM outbox WHERE sent=0 OR
                (last_sent<? AND line LIKE '!ask %' AND request_id IN
                 (SELECT id FROM requests WHERE direction='out' AND status='pending'))
                ORDER BY last_sent,id LIMIT 1""",
                (int(time.time()) - 15,),
            ).fetchone()
            return (row[0], row[1]) if row else None

    def sent(self, row_id: int) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE outbox SET sent=1,last_sent=? WHERE id=?",
                (int(time.time()), row_id),
            )

    def heartbeat(self, connected: bool) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO meta VALUES ('heartbeat', ?)",
                (str(int(time.time())) if connected else "0",),
            )

    def status(self) -> dict:
        now = int(time.time())
        with self.connection() as db:
            self._expire(db, now)
            heartbeat = db.execute(
                "SELECT value FROM meta WHERE key='heartbeat'"
            ).fetchone()
            counts = dict(
                db.execute(
                    "SELECT status,count(*) FROM requests GROUP BY status"
                ).fetchall()
            )
            return {
                "agent_id": self.config.agent_id,
                "channel": self.config.channel,
                "connected": bool(heartbeat and now - int(heartbeat[0]) < 10),
                "requests": counts,
                "queued_frames": db.execute(
                    "SELECT count(*) FROM outbox WHERE sent=0"
                ).fetchone()[0],
            }
