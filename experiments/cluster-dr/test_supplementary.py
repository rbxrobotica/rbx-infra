"""Offline security contracts for the PostgreSQL, history and local-PV helpers."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest import mock


HERE = Path(__file__).parent


def load_helper(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


observe = load_helper("supplementary_observe", "observe.py")
with mock.patch.dict(sys.modules, {"observe": observe}):
    history = load_helper("supplementary_history", "history.py")
postgres = load_helper("supplementary_postgres", "postgres_observe.py")
volumes = load_helper("supplementary_volumes", "volume_metadata.py")


class SupplementarySafetyContracts(unittest.TestCase):
    def setUp(self):
        for method in ("Popen", "run"):
            guard = mock.patch.object(
                observe.subprocess, method,
                side_effect=AssertionError("real subprocess, SSH or network access forbidden"),
            )
            guard.start()
            self.addCleanup(guard.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "storage"
        self.root.mkdir()
        guard = mock.patch.object(volumes, "ROOT", self.root)
        guard.start()
        self.addCleanup(guard.stop)

    def pv_item(self, path=None):
        if path is None:
            path = self.root / "pvc-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa_fixture_data"
        return {"pv": "fixture-pv", "namespace": "fixture", "claim": "data",
                "node": "fixture-node", "path": str(path)}

    def test_postgres_uses_a_clean_local_readonly_noninteractive_connection(self):
        private = "SYNTHETIC_AMBIENT_PASSWORD"
        response = types.SimpleNamespace(returncode=0, stdout='{"version":"16.fixture"}', stderr="")
        with mock.patch.dict(os.environ, {
            "PGHOST": "remote.invalid", "PGPASSWORD": private,
            "PGSERVICE": private, "PGPASSFILE": private,
            "PGOPTIONS": "-c default_transaction_read_only=off",
        }), mock.patch.object(postgres.subprocess, "run", return_value=response) as run:
            result = postgres.read("rbx_comms", "SELECT 1;")
        args = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertEqual(args[:5], ["runuser", "-u", "postgres", "--", "/usr/bin/psql"])
        self.assertIn("-X", args)
        self.assertIn("-w", args)
        self.assertNotIn("-W", args)
        self.assertEqual(args[args.index("--dbname") + 1], "rbx_comms")
        self.assertEqual(options["env"]["PGHOST"], "/var/run/postgresql")
        self.assertEqual(options["env"]["PGPORT"], "5432")
        self.assertEqual(options["env"]["PGPASSFILE"], "/dev/null")
        self.assertIn("default_transaction_read_only=on", options["env"]["PGOPTIONS"])
        self.assertIn("statement_timeout=5000", options["env"]["PGOPTIONS"])
        self.assertIn("lock_timeout=1000", options["env"]["PGOPTIONS"])
        self.assertFalse(options.get("shell", False))
        self.assertLessEqual(options["timeout"], 12)
        self.assertNotIn(private, repr((args, options, result)))
        self.assertNotIn("PGSERVICE", options["env"])
        self.assertNotIn("PGPASSWORD", options["env"])

    def test_database_names_cannot_be_connection_strings_options_or_sql(self):
        with mock.patch.object(postgres.subprocess, "run") as run:
            for database in (
                "postgresql://user:private@remote/db", "host=remote dbname=postgres",
                "-hremote", "dbname=postgres", "rbx;DROP DATABASE rbx", "", "a" * 64,
            ):
                with self.subTest(database=database):
                    with self.assertRaisesRegex(ValueError, "unsupported_database_name"):
                        postgres.read(database, "SELECT 1;")
            run.assert_not_called()

    def test_postgres_main_queries_only_allowlisted_catalogs_and_no_secret_fields(self):
        queries = []

        def metadata_read(database, query):
            queries.append(query)
            if database == "postgres":
                return {"databases": [{"name": "rbx_comms", "bytes": 100, "connections": 1}],
                        "settings": {"shared_buffers": {"setting": "16384", "unit": "8kB"}}}
            self.assertEqual(database, "rbx_comms")
            return [{"name": "plpgsql", "version": "1.0"}]

        output = io.StringIO()
        with mock.patch.object(postgres, "read", side_effect=metadata_read), \
                mock.patch.object(postgres.shutil, "which", return_value=None), \
                contextlib.redirect_stdout(output):
            postgres.main()
        allowed = {"pg_settings", "pg_stat_archiver", "pg_stat_wal", "pg_stat_replication",
                   "pg_replication_slots", "pg_stat_database", "pg_extension"}
        for query in queries:
            catalogs = set(re.findall(r"\bFROM\s+([A-Za-z_][A-Za-z_0-9]*)", query, re.I))
            self.assertTrue(catalogs)
            self.assertLessEqual(catalogs, allowed)
            self.assertNotRegex(query.lower(), r"\b(insert|update|delete|alter|create|drop|copy|vacuum)\b")
            self.assertNotRegex(query.lower(), r"pg_authid|pg_shadow|rolpassword|archive_command|primary_conninfo")
            self.assertNotRegex(query, r"SELECT\s+\*")
        # Raw pg_settings values are meaningless for sizing without their units.
        settings_fields = set(re.findall(r"'([a-z_]+)'", queries[0]))
        self.assertLessEqual({"setting", "unit"}, settings_fields)
        report = json.loads(output.getvalue())
        self.assertFalse(report["table_rows_read"])
        self.assertFalse(report["state_changed"])
        self.assertEqual(report["metadata"]["databases"][0]["extensions"][0]["name"], "plpgsql")
        self.assertEqual(report["metadata"]["settings"]["shared_buffers"],
                         {"setting": "16384", "unit": "8kB"})

    def test_historical_vectors_drop_all_unapproved_labels(self):
        private = "SYNTHETIC_PRIVATE_LABEL"
        report = history.vector({"status": "success", "data": {
            "resultType": "vector", "result": [{
                "metric": {"namespace": "fixture", "node": "node-a", "metrics_path": "/metrics",
                           "persistentvolumeclaim": "data", "instance": private,
                           "pod": private, "token": private, "__name__": private},
                "value": [1700000000, "12.5"],
            }],
        }})
        self.assertEqual(report[0]["value"], 12.5)
        self.assertEqual(set(report[0]["labels"]),
                         {"namespace", "node", "metrics_path", "persistentvolumeclaim"})
        self.assertNotIn(private, json.dumps(report))

    def test_history_refuses_nonvectors_nonfinite_values_and_series_overflow(self):
        fixtures = [
            {"status": "error", "error": "PRIVATE_PROVIDER_ERROR"},
            {"status": "success", "data": {"resultType": "matrix", "result": []}},
            {"status": "success", "data": {"resultType": "vector", "result": [
                {"metric": {}, "value": [1700000000, "0"]}
            ] * 1001}},
        ]
        fixtures.extend({"status": "success", "data": {"resultType": "vector", "result": [
            {"metric": {}, "value": [1700000000, value]}
        ]}} for value in ("NaN", "Inf", "+Inf", "-Inf", "1e309"))
        fixtures.extend({"status": "success", "data": {"resultType": "vector", "result": [
            {"metric": {}, "value": [stamp, "1"]}
        ]}} for stamp in (True, float("nan"), float("inf"), "PRIVATE_TIMESTAMP",
                         {"payload": "PRIVATE_TIMESTAMP"}))
        for fixture in fixtures:
            with self.subTest(kind=fixture.get("data", {}).get("resultType")):
                with self.assertRaises(observe.ObservationError) as raised:
                    history.vector(fixture)
                self.assertNotIn("PRIVATE_", str(raised.exception))

    def test_wrong_cluster_refuses_before_any_historical_query(self):
        output = io.StringIO()
        with mock.patch.object(observe, "execute", return_value="wrong-cluster") as execute, \
                mock.patch.object(history, "get") as get, \
                contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(observe.ObservationError, "cluster_identity_mismatch"):
                history.main()
        get.assert_not_called()
        execute.assert_called_once_with(
            ["get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}"]
        )
        self.assertEqual(output.getvalue(), "")

    def test_volume_paths_outside_the_root_or_through_symlinks_never_run_du(self):
        outside = self.base / "outside"
        outside.mkdir()
        link = self.root / "pvc-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb_fixture_link"
        link.symlink_to(outside, target_is_directory=True)
        for path in (outside, link, self.root / "unexpected-directory"):
            with self.subTest(path=path.name):
                with mock.patch.object(volumes.subprocess, "run") as run:
                    report = volumes.measure(self.pv_item(path))
                run.assert_not_called()
                self.assertIn("gap", report)
                self.assertNotIn("allocated_bytes", report)
                self.assertNotIn(str(path), json.dumps(report))

    def test_volume_du_is_fixed_metadata_only_and_never_opens_file_payloads(self):
        item = self.pv_item()
        path = Path(item["path"])
        path.mkdir()
        private = "SYNTHETIC_PRIVATE_FILE_CONTENT"
        (path / "data.bin").write_text(private)
        reply = types.SimpleNamespace(returncode=0, stdout=f"4096\t{path}\n", stderr="")
        for wrappers in (False, True):
            with self.subTest(wrappers=wrappers):
                with mock.patch.object(volumes.shutil, "which", return_value="present" if wrappers else None), \
                        mock.patch.object(volumes.subprocess, "run", return_value=reply) as run, \
                        mock.patch("builtins.open", side_effect=AssertionError("payload read forbidden")), \
                        mock.patch("io.open", side_effect=AssertionError("payload read forbidden")), \
                        mock.patch("os.open", side_effect=AssertionError("payload read forbidden")):
                    report = volumes.measure(item)
                expected = ["du", "-s", "-x", "-B1", "--", str(path)]
                if wrappers:
                    expected = ["ionice", "-c", "3", "nice", "-n", "19", *expected]
                self.assertEqual(run.call_args.args[0], expected)
                self.assertFalse(run.call_args.kwargs.get("shell", False))
                self.assertLessEqual(run.call_args.kwargs["timeout"], 15)
                self.assertEqual(report["allocated_bytes"], 4096)
                self.assertTrue(report["observation_not_transactionally_consistent"])
                self.assertNotIn(private, json.dumps(report))
                self.assertNotIn(str(path), json.dumps(report))

    def test_du_failures_mismatches_and_malformed_output_are_gaps_not_zero(self):
        item = self.pv_item()
        path = Path(item["path"])
        path.mkdir()
        replies = [
            types.SimpleNamespace(returncode=1, stdout="", stderr="PRIVATE_DU_ERROR"),
            types.SimpleNamespace(returncode=0, stdout="9\t/another/volume\n", stderr=""),
            types.SimpleNamespace(returncode=0, stdout=f"not-a-number\t{path}\n", stderr=""),
            types.SimpleNamespace(returncode=0, stdout=f"-1\t{path}\n", stderr=""),
            types.SimpleNamespace(returncode=0, stdout=f"-0\t{path}\n", stderr=""),
            types.SimpleNamespace(returncode=0, stdout="x" * 8193, stderr=""),
            volumes.subprocess.TimeoutExpired("PRIVATE_COMMAND", 15, output="PRIVATE_OUTPUT"),
            PermissionError("PRIVATE_PERMISSION_DETAIL"),
        ]
        for reply in replies:
            with self.subTest(reply_type=type(reply).__name__):
                kwargs = {"side_effect": reply} if isinstance(reply, BaseException) else {"return_value": reply}
                with mock.patch.object(volumes.shutil, "which", return_value=None), \
                        mock.patch.object(volumes.subprocess, "run", **kwargs):
                    report = volumes.measure(item)
                self.assertIn("gap", report)
                self.assertNotIn("allocated_bytes", report)
                self.assertNotIn("PRIVATE_", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
