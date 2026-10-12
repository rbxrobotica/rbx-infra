"""Opt-in synthetic PostgreSQL databases, never an application's configured DSN."""

from __future__ import annotations

import os
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4

import pytest

from strategos_irc_bot.agent_config import AgentConfig


def _test_admin_parameters(raw: str) -> dict[str, str | int]:
    """Reject unsafe targets before the first connection or database mutation."""
    try:
        url = urlsplit(raw)
        port = url.port
        if (
            url.scheme not in {"postgres", "postgresql"}
            or url.hostname not in {"127.0.0.1", "::1"}
            or port is None
            or url.path != "/irc_mailbox_test"
            or not url.username
            or url.query
            or url.fragment
        ):
            raise ValueError
    except ValueError:
        pytest.fail(
            "RBX_IRC_TEST_POSTGRES_URL must identify an explicit numeric loopback "
            "port and the disposable seed database irc_mailbox_test, with "
            "a username and no query or fragment",
            pytrace=False,
        )
    return {
        "host": url.hostname,
        "hostaddr": url.hostname,
        "port": port,
        "user": unquote(url.username),
        "password": unquote(url.password or ""),
        "dbname": "irc_mailbox_test",
        "sslmode": "disable",
        "gssencmode": "disable",
        "connect_timeout": 5,
        "application_name": "rbx-irc-synthetic-tests",
        "options": "-c statement_timeout=30000 -c lock_timeout=5000",
    }


@pytest.fixture
def postgres_config_factory(monkeypatch, tmp_path):
    """Create one empty database per call; callers initialize their own schema.

    The administrator URL is a dedicated test input, unrelated to production
    configuration. The factory returns AgentConfig and sets its unique DSN
    environment variable. Every database it owns is dropped after the test.
    """
    raw = os.environ.get("RBX_IRC_TEST_POSTGRES_URL")
    if not raw:
        message = (
            "RBX_IRC_TEST_POSTGRES_URL is required for PostgreSQL integration tests"
        )
        if os.environ.get("RBX_IRC_REQUIRE_POSTGRES") == "1":
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    parameters = _test_admin_parameters(raw)
    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        pytest.fail("Install the postgres extra to run PostgreSQL tests", pytrace=False)

    # libpq environment/service defaults must not redirect this test connection.
    for name in tuple(os.environ):
        if name.startswith("PG"):
            monkeypatch.delenv(name)
    databases = []

    def connect():
        # A test may introduce ambient libpq variables to verify rejection.
        # Teardown still connects only to the fixture's explicit seed target.
        for name in tuple(os.environ):
            if name.startswith("PG"):
                monkeypatch.delenv(name)
        try:
            return psycopg.connect(**parameters, autocommit=True)
        except psycopg.Error:
            pytest.fail(
                "Synthetic PostgreSQL administrator connection failed", pytrace=False
            )

    def factory(**overrides):
        if {"storage_backend", "database_url_env"} & overrides.keys():
            raise ValueError(
                "Synthetic storage backend and DSN references are fixture-owned"
            )
        suffix = uuid4().hex
        database = f"irc_test_{suffix}"
        with connect() as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))
            )
        databases.append(database)

        host = parameters["host"]
        authority = f"[{host}]" if ":" in host else host
        username = quote(parameters["user"], safe="")
        password = quote(parameters["password"], safe="")
        dsn = f"postgresql://{username}:{password}@{authority}:{parameters['port']}/{database}?sslmode=disable"
        variable = f"RBX_IRC_DATABASE_URL_{suffix.upper()}"
        monkeypatch.setenv(variable, dsn)
        values = {
            "agent_id": "local",
            "server": "127.0.0.1:6667",
            "tls": False,
            "nick": "rbx-local",
            "account": "rbx-local",
            "password_env": "RBX_AGENT_IRC_PASSWORD",
            "channel": "#rbx-agents",
            "peers": {"thinkcentre": "rbx-thinkcentre"},
            "operators": frozenset({"leandro"}),
            "state_dir": tmp_path / suffix / "local",
            "human_queries": True,
            "storage_backend": "postgresql",
            "database_url_env": variable,
        }
        values.update(overrides)
        return AgentConfig(**values)

    try:
        yield factory
    finally:
        if databases:
            with connect() as connection:
                for database in reversed(databases):
                    connection.execute(
                        sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                            sql.Identifier(database)
                        )
                    )


@pytest.fixture
def postgres_config(postgres_config_factory):
    """Default human-query configuration with an empty dedicated test database."""
    return postgres_config_factory()
