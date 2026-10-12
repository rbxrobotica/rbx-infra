"""Explicit persistence backend for the single-account IRC mailbox.

PostgreSQL schema creation is an administrative operation, never a side effect
of starting the adapter. All clients use one transaction-scoped advisory lock
under READ COMMITTED, preserving the mailbox's serialized state transitions.
No database operation is retried and PostgreSQL never falls back to SQLite.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

from .agent_config import AgentConfig


class MailboxStorageError(ValueError):
    """Persistence failed or destination identity is unsafe; never auto-retry."""


SCHEMA = "irc_mailbox"
SCHEMA_VERSION = "1"
ADVISORY_LOCK = 0x524258495243
POSTGRES_FAILURE = (
    "PostgreSQL mailbox operation failed; outcome may be uncertain; "
    "do not retry automatically"
)

# These names and columns are also the closed allowlist for offline import.
TABLE_COLUMNS = {
    "meta": ("key", "value"),
    "requests": (
        "id",
        "direction",
        "account",
        "peer",
        "timestamp",
        "text",
        "status",
        "response",
        "acknowledged",
    ),
    "outbox": ("id", "request_id", "line", "sent", "last_sent"),
    "fragments": ("request_id", "part", "total", "text"),
    "rate_limits": ("account", "window", "count"),
    "human_meta": ("key", "value"),
    "human_sessions": (
        "id",
        "alias",
        "account",
        "channel",
        "project",
        "revision",
        "model",
        "created_at",
        "expires_at",
        "max_questions",
        "accepted",
        "admission_open",
        "owner",
        "closed_at",
        "close_reason",
    ),
    "human_requests": (
        "id",
        "reference",
        "session_id",
        "account",
        "msgid",
        "server_time",
        "received_at",
        "text",
        "status",
        "owner",
        "fence",
        "claim_expires_at",
        "response",
        "error",
        "completed_at",
        "delivery_status",
    ),
    "human_outbox": (
        "id",
        "request_id",
        "part",
        "line",
        "status",
        "attempted_at",
        "socket_sent_at",
    ),
}

LEGACY_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS requests (
        id TEXT PRIMARY KEY, direction TEXT NOT NULL, account TEXT NOT NULL,
        peer TEXT NOT NULL, timestamp INTEGER NOT NULL, text TEXT NOT NULL,
        status TEXT NOT NULL, response TEXT, acknowledged INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, line TEXT NOT NULL,
        sent INTEGER NOT NULL DEFAULT 0, last_sent INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS fragments (
        request_id TEXT NOT NULL, part INTEGER NOT NULL, total INTEGER NOT NULL,
        text TEXT NOT NULL, PRIMARY KEY(request_id, part))""",
    """CREATE TABLE IF NOT EXISTS rate_limits (
        account TEXT PRIMARY KEY, "window" INTEGER NOT NULL, count INTEGER NOT NULL)""",
)
HUMAN_SCHEMA = (
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
)


class Connection(Protocol):
    """Small DB-API surface shared by both state machines."""

    def execute(self, query: str, params: tuple = ()) -> Any: ...

    def executemany(self, query: str, params: Any) -> Any: ...


class Row:
    """SQLite Row-compatible mapping and positional access for psycopg results."""

    def __init__(self, names: tuple[str, ...], values: tuple) -> None:
        self._names = names
        self._values = values

    def keys(self) -> tuple[str, ...]:
        return self._names

    def __getitem__(self, key: str | int) -> Any:
        return self._values[self._names.index(key) if isinstance(key, str) else key]

    def __iter__(self) -> Iterator:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


def _row_factory(cursor: Any) -> Any:
    names = tuple(col.name for col in cursor.description) if cursor.description else ()
    return lambda values: Row(names, values)


class PostgreSQLConnection:
    def __init__(self, connection: Any) -> None:
        self.raw = connection

    @staticmethod
    def _query(query: str) -> str:
        # Only trusted internal SQL reaches this adapter; data always uses params.
        # Escaping literal percent signs also preserves SQL LIKE patterns.
        return query.replace("%", "%%").replace("?", "%s")

    def execute(self, query: str, params: tuple = ()) -> Any:
        return self.raw.execute(self._query(query), params)

    def executemany(self, query: str, params: Any) -> Any:
        cursor = self.raw.cursor()
        cursor.executemany(self._query(query), params)
        return cursor


def _psycopg() -> Any:
    try:
        import psycopg
    except ImportError:
        raise MailboxStorageError(
            "PostgreSQL backend requires the postgres optional dependency"
        ) from None
    return psycopg


def postgres_parameters(config: AgentConfig) -> dict[str, Any]:
    """Resolve a validated DSN without including connection values in errors."""
    if getattr(config, "storage_backend", "sqlite") != "postgresql":
        raise MailboxStorageError(
            "PostgreSQL operation requires storage_backend=postgresql"
        )
    variable = getattr(config, "database_url_env", None)
    dsn = os.environ.get(variable or "")
    if not dsn:
        raise MailboxStorageError("PostgreSQL database environment variable is not set")
    # libpq's ambient settings must not silently change destination, identity,
    # certificate verification, service definitions or session options.
    if any(name.startswith("PG") for name in os.environ):
        raise MailboxStorageError(
            "unset ambient PG* variables before using the mailbox DSN"
        )
    psycopg = _psycopg()
    try:
        params = psycopg.conninfo.conninfo_to_dict(dsn)
    except Exception:
        raise MailboxStorageError(
            "invalid PostgreSQL mailbox connection settings"
        ) from None
    allowed = {
        "host",
        "port",
        "dbname",
        "user",
        "password",
        "sslmode",
        "sslrootcert",
        "sslcert",
        "sslkey",
    }
    if set(params) - allowed or any(
        not params.get(key) for key in ("host", "dbname", "user", "sslmode")
    ):
        raise MailboxStorageError(
            "PostgreSQL DSN requires explicit host/dbname/user/sslmode and allowed settings"
        )
    host = params["host"]
    if "," in host or host.startswith("/") or any(c.isspace() for c in host):
        raise MailboxStorageError("PostgreSQL DSN requires a single explicit TCP host")
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if params["sslmode"] != "verify-full" and not (
        loopback and params["sslmode"] == "disable"
    ):
        raise MailboxStorageError("remote PostgreSQL requires sslmode=verify-full")
    port = params.get("port", "5432")
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise MailboxStorageError("invalid PostgreSQL mailbox port")
    params.update(
        port=port,
        connect_timeout=5,
        application_name="rbx-irc-mailbox",
        # Disable transparent GSS encryption preference: remote TLS verification
        # must not be replaced by an ambient Kerberos/GSS setup.
        gssencmode="disable",
        options=(
            "-c search_path=pg_catalog,irc_mailbox,pg_temp "
            "-c lock_timeout=5000 -c statement_timeout=10000 "
            "-c idle_in_transaction_session_timeout=10000"
        ),
    )
    return params


def mailbox_identity(config: AgentConfig) -> str:
    return f"{config.agent_id}:{config.account}:{config.channel}"


def transport_identity(config: AgentConfig) -> str:
    return json.dumps(
        {"server": config.server, "tls": config.tls},
        sort_keys=True,
        separators=(",", ":"),
    )


def _verify_postgres(db: Connection, config: AgentConfig) -> None:
    values = dict(db.execute("SELECT key,value FROM meta").fetchall())
    if values.get("schema_version") != SCHEMA_VERSION:
        raise MailboxStorageError(
            "PostgreSQL mailbox schema is not initialized at the required version"
        )
    if values.get("identity") != mailbox_identity(config):
        raise MailboxStorageError(
            "PostgreSQL mailbox belongs to a different agent/account/channel"
        )
    if values.get("human_transport_identity") != transport_identity(config):
        raise MailboxStorageError(
            "PostgreSQL mailbox belongs to a different IRC transport"
        )


@contextmanager
def _postgres_transaction(
    raw: Any, config: AgentConfig, *, allow_hold: bool = False, verify: bool = True
) -> Iterator[PostgreSQLConnection]:
    psycopg = _psycopg()
    try:
        with raw.transaction():
            db = PostgreSQLConnection(raw)
            db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            # READ COMMITTED takes a new snapshot after this lock is acquired.
            # Every deadline is evaluated by the stores after yielding the lock.
            db.execute("SELECT pg_advisory_xact_lock(?)", (ADVISORY_LOCK,))
            if verify:
                _verify_postgres(db, config)
                if (
                    not allow_hold
                    and db.execute(
                        "SELECT 1 FROM meta WHERE key='migration_hold'"
                    ).fetchone()
                ):
                    raise MailboxStorageError(
                        "PostgreSQL mailbox is held for offline reconciliation"
                    )
            yield db
    except psycopg.Error:
        raise MailboxStorageError(POSTGRES_FAILURE) from None


def _connect(config: AgentConfig) -> Any:
    psycopg = _psycopg()
    params = postgres_parameters(config)
    try:
        return psycopg.connect(**params, autocommit=True, row_factory=_row_factory)
    except psycopg.Error:
        raise MailboxStorageError(POSTGRES_FAILURE) from None


@contextmanager
def postgres_connection(
    config: AgentConfig, *, allow_hold: bool = False, verify: bool = True
) -> Iterator[PostgreSQLConnection]:
    """One administrative transaction; no retry after an unknown outcome.

    ``verify=False`` is exclusively for schema initialization. Offline migration
    uses ``allow_hold=True`` while the adapter and all other writers are stopped.
    """
    raw = _connect(config)
    try:
        with _postgres_transaction(
            raw, config, allow_hold=allow_hold, verify=verify
        ) as db:
            yield db
    finally:
        raw.close()


def reset_postgres_sequences(db: Connection) -> None:
    """Transactionally advance known identity sequences after offline import.

    Unlike setval(), ALTER TABLE ... RESTART rolls back with the copied rows.
    This command is administrative and requires schema-owner credentials.
    """
    for table in ("outbox", "human_outbox"):
        maximum = db.execute(f"SELECT max(id) FROM {table}").fetchone()[0]
        next_id = 1 if maximum is None else maximum + 1
        if type(next_id) is not int or not 1 <= next_id <= 9223372036854775807:
            raise MailboxStorageError(
                "imported mailbox identity sequence is out of range"
            )
        # Identifiers are fixed and the sole interpolated value is a bounded int.
        db.execute(
            f"ALTER TABLE irc_mailbox.{table} ALTER COLUMN id RESTART WITH {next_id}"
        )


def initialize_postgres(config: AgentConfig) -> dict:
    """Initialize an empty dedicated DB schema using its owner credentials.

    Runtime credentials need only schema USAGE, table DML and sequence USAGE.
    An existing schema must already match the version and mailbox identities;
    this command never adopts or overwrites an unknown schema.
    """
    with postgres_connection(config, allow_hold=True, verify=False) as db:
        if db.execute("SELECT to_regnamespace(?)", (SCHEMA,)).fetchone()[0]:
            _verify_postgres(db, config)
            return {"schema_version": SCHEMA_VERSION, "initialized": False}
        db.execute("CREATE SCHEMA irc_mailbox")
        db.execute("REVOKE ALL ON SCHEMA irc_mailbox FROM PUBLIC")
        for statement in (*LEGACY_SCHEMA, *HUMAN_SCHEMA):
            statement = statement.replace(" INTEGER", " BIGINT").replace(
                " REAL", " DOUBLE PRECISION"
            )
            statement = statement.replace(
                "id BIGINT PRIMARY KEY, request_id",
                "id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, request_id",
            )
            statement = statement.replace(
                "CREATE TABLE IF NOT EXISTS ", "CREATE TABLE irc_mailbox."
            ).replace(" ON human_requests(", " ON irc_mailbox.human_requests(")
            db.execute(statement)
        db.execute("REVOKE ALL ON ALL TABLES IN SCHEMA irc_mailbox FROM PUBLIC")
        db.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA irc_mailbox FROM PUBLIC")
        db.executemany(
            "INSERT INTO meta(key,value) VALUES (?,?)",
            (
                ("schema_version", SCHEMA_VERSION),
                ("identity", mailbox_identity(config)),
                ("human_transport_identity", transport_identity(config)),
            ),
        )
        return {"schema_version": SCHEMA_VERSION, "initialized": True}


class MailboxDatabase:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.backend = getattr(config, "storage_backend", "sqlite")
        self.path = None
        self._transport_connection = None
        if self.backend == "postgresql":
            with self.connection():
                pass
        elif self.backend == "sqlite":
            self._initialize_sqlite()
        else:
            raise ValueError("unknown mailbox storage backend")

    def _initialize_sqlite(self) -> None:
        path = self.config.state_dir
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
            for statement in LEGACY_SCHEMA:
                db.execute(statement)
            identity = mailbox_identity(self.config)
            db.execute(
                "INSERT INTO meta VALUES ('identity', ?) ON CONFLICT(key) DO NOTHING",
                (identity,),
            )
            if (
                db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()[0]
                != identity
            ):
                raise ValueError(
                    "state_dir belongs to a different agent/account/channel"
                )

    def initialize_human(self) -> None:
        if self.backend == "postgresql":
            # All tables were installed by the separate administrative init.
            return
        with self.connection() as db:
            identity = transport_identity(self.config)
            db.execute(
                "INSERT INTO meta VALUES ('human_transport_identity', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (identity,),
            )
            if (
                db.execute(
                    "SELECT value FROM meta WHERE key='human_transport_identity'"
                ).fetchone()[0]
                != identity
            ):
                raise ValueError("human mailbox belongs to a different IRC transport")
            for statement in HUMAN_SCHEMA:
                db.execute(statement)

    def has_human_tables(self, db: Connection) -> bool:
        if self.backend == "postgresql":
            return True
        return bool(
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='human_requests'"
            ).fetchone()
        )

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        if self.backend == "postgresql":
            if self._transport_connection is not None:
                # No reconnect while owning the daemon's session advisory lock.
                with _postgres_transaction(
                    self._transport_connection, self.config
                ) as db:
                    yield db
            else:
                with postgres_connection(self.config) as db:
                    yield db
            return
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    @contextmanager
    def transport_owner(self) -> Iterator[None]:
        """Hold sole PG daemon ownership on the same session used for all I/O.

        A disconnected ownership session cannot reconnect and continue sending.
        CLI consumers use separate transaction locks; the transport owner is a
        separate lock namespace so those consumers remain operable.
        """
        if self.backend != "postgresql":
            raise MailboxStorageError(
                "SQLite transport ownership uses its existing local file lock"
            )
        if self._transport_connection is not None:
            raise MailboxStorageError(
                "PostgreSQL transport ownership is already active"
            )
        raw = _connect(self.config)
        psycopg = _psycopg()
        try:
            db = PostgreSQLConnection(raw)
            if not db.execute(
                "SELECT pg_try_advisory_lock(?)", (ADVISORY_LOCK + 1,)
            ).fetchone()[0]:
                raise MailboxStorageError(
                    "another PostgreSQL mailbox transport is already active"
                )
            self._transport_connection = raw
            with self.connection():
                pass
            yield
        except psycopg.Error:
            raise MailboxStorageError(POSTGRES_FAILURE) from None
        finally:
            self._transport_connection = None
            # Closing releases the session lock even after a failed transaction.
            raw.close()
