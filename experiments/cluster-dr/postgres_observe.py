#!/usr/bin/env python3
"""Local-socket PostgreSQL metadata only; no table rows, secrets or maintenance."""

import datetime
import json
import re
import shutil
import subprocess


SQL = """
SELECT json_build_object(
 'version', current_setting('server_version'),
 'in_recovery', pg_is_in_recovery(),
 'settings', (SELECT json_object_agg(name, json_build_object('setting',setting,'unit',unit)) FROM pg_settings WHERE name IN
   ('archive_mode','archive_timeout','max_connections','shared_buffers','wal_level',
    'synchronous_commit','wal_compression','work_mem','max_wal_size')),
 'archiver', (SELECT json_build_object('archived_count',archived_count,
   'failed_count',failed_count,'last_archived_time',last_archived_time,
   'last_failed_time',last_failed_time,'stats_reset',stats_reset) FROM pg_stat_archiver),
 'wal', (SELECT json_build_object('bytes',wal_bytes,'records',wal_records,
   'stats_reset',stats_reset) FROM pg_stat_wal),
 'replication_connections', (SELECT count(*) FROM pg_stat_replication),
 'replication_slots', (SELECT count(*) FROM pg_replication_slots),
 'databases', (SELECT json_agg(json_build_object('name',datname,
   'bytes',pg_database_size(datid),'connections',numbackends)) FROM pg_stat_database
   WHERE datname IS NOT NULL AND datname NOT IN ('template0','template1'))
);
"""


def read(database, query):
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}", database):
        raise ValueError("unsupported_database_name")
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C.UTF-8",
           "PGHOST": "/var/run/postgresql", "PGPORT": "5432",
           "PGPASSFILE": "/dev/null",
           "PGAPPNAME": "rbx-dr-readonly-metadata",
           "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000"}
    args = ["runuser", "-u", "postgres", "--", "/usr/bin/psql", "-X", "-w", "-q", "-t", "-A",
            "--dbname", database, "--command", query]
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=12)
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise RuntimeError("metadata_read_unavailable")
    return json.loads(result.stdout)


def main():
    result = {"type": "read-only PostgreSQL sizing metadata",
              "observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "table_rows_read": False, "state_changed": False,
              "pgbackrest_available": bool(shutil.which("pgbackrest"))}
    result["metadata"] = read("postgres", SQL)
    databases = result["metadata"]["databases"] or []
    if len(databases) > 100:
        raise RuntimeError("database_count_bound")
    for database in databases:
        try:
            database["extensions"] = read(database["name"],
                "SELECT coalesce(json_agg(json_build_object('name',extname,'version',extversion)), '[]'::json) FROM pg_extension;")
        except (ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            database["extension_gap"] = type(error).__name__
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"completed": False, "error_type": type(error).__name__}))
        raise SystemExit(1)
