"""Isolated adversarial tests for the narrowly scoped recovery-kit responder.

These tests use temporary private fixtures only.  They do not connect to IRC,
SSH, a password store, a device, or any other service.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


_SPEC = importlib.util.spec_from_file_location(
    "offline_kits_responder", Path(__file__).with_name("responder.py")
)
assert _SPEC is not None and _SPEC.loader is not None
responder = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = responder
_SPEC.loader.exec_module(responder)


class ResponderTests(unittest.TestCase):
    NOW = 1_795_350_000
    TASK = "twousb22"
    WIRE_PATH = "~/.local/share/rbx-agent-handoffs/twousb22/CONTEXT.md"
    REJECT = "CONTEXT_REJECT task=twousb22"
    SECRET = "SENTINEL_PRIVATE_VALUE_NEVER_RETURN_9d6b"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="offline-kit-responder-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.context = self.root / self.TASK / "CONTEXT.md"
        self.context.parent.mkdir(mode=0o700)
        self.write_private(self.context, b"# Recovery kit handoff\nOnly public task notes.\n")
        self.digest = hashlib.sha256(self.context.read_bytes()).hexdigest()
        self.status = self.root / "status.json"
        self.write_status({"phase": "ready", "count": 2, "result": "ok"})
        self.registry = {
            "expires_epoch": self.NOW + 300,
            "task": "recovery-kits",
            "contexts": {
                self.TASK: {
                    "relative_path": "twousb22/CONTEXT.md",
                    "sha256": self.digest,
                }
            },
            "status_relative_path": "status.json",
        }

    @staticmethod
    def write_private(path, content):
        path.write_bytes(content)
        path.chmod(0o600)

    def write_status(self, value):
        self.write_private(self.status, json.dumps(value).encode("utf-8"))

    def request(self, *, task=None, path=None, digest=None):
        return (
            f"CTX task={self.TASK if task is None else task} "
            f"path={self.WIRE_PATH if path is None else path} "
            f"sha256={self.digest if digest is None else digest} "
            "src=rbx-local reply=CONTEXT_OK"
        )

    def row(self, text=None, **changes):
        result = {
            "id": "0123456789abcdef0123456789abcdef",
            "direction": "in",
            "status": "pending",
            "peer": "local",
            "account": "rbx-local",
            "text": self.request() if text is None else text,
        }
        result.update(changes)
        return result

    def call(self, row=None, *, registry=None, root=None, now=None):
        row = self.row() if row is None else row
        registry = self.registry if registry is None else registry
        before_row = copy.deepcopy(row)
        before_registry = copy.deepcopy(registry)
        answer = responder.respond(
            row,
            registry,
            self.root if root is None else root,
            self.NOW if now is None else now,
        )
        self.assertEqual(row, before_row, "respond must not mutate mailbox rows")
        self.assertEqual(registry, before_registry, "respond must not mutate approval")
        if answer is not None:
            self.assertIsInstance(answer, str)
            self.assertNotIn(self.SECRET, answer)
        return answer

    def assert_rejected(self, text=None):
        self.assertEqual(self.call(self.row(text)), self.REJECT)

    def assert_no_ack(self, text):
        self.assertIn(self.call(self.row(text)), (None, self.REJECT))

    def test_good_context_ack_contains_only_task_and_digest(self):
        self.assertEqual(
            self.call(), f"CONTEXT_OK task={self.TASK} sha256={self.digest}"
        )

    def test_exact_ping(self):
        self.assertEqual(
            self.call(self.row("PING task=recovery-kits")),
            "PONG task=recovery-kits",
        )

    def test_status_returns_safe_allowlisted_metadata(self):
        answer = self.call(self.row("STATUS task=recovery-kits"))
        self.assertIsInstance(answer, str)
        self.assertLessEqual(len(answer.encode("utf-8")), 240)
        for token in ("phase=ready", "count=2", "result=ok"):
            self.assertIn(token, answer)

    def test_ineligible_rows_are_ignored_for_every_command(self):
        changes = (
            {"direction": "out"},
            {"status": "processed"},
            {"peer": "thinkcentre"},
            {"peer": "local "},
            {"account": "other-account"},
            {"account": "rbx-local "},
            {"id": "0123456789ABCDEF0123456789ABCDEF"},
            {"id": "a" * 31},
            {"id": "a" * 33},
            {"id": "z" * 32},
            {"id": "a" * 31 + "\n"},
        )
        for text in (self.request(), "PING task=recovery-kits", "STATUS task=recovery-kits"):
            for change in changes:
                with self.subTest(text=text, change=change):
                    self.assertIsNone(self.call(self.row(text, **change)))

    def test_missing_identity_fields_are_ignored(self):
        for key in ("id", "direction", "status", "peer", "account", "text"):
            with self.subTest(key=key):
                row = self.row()
                del row[key]
                self.assertIsNone(self.call(row))

    def test_non_string_messages_are_ignored(self):
        for value in (None, 12, [], {}, b"PING task=recovery-kits"):
            with self.subTest(value=value):
                row = self.row()
                row["text"] = value
                self.assertIsNone(self.call(row))

    def test_expired_and_expiry_boundary_authorizations_are_ignored(self):
        for expires in (self.NOW - 1, self.NOW):
            for text in (self.request(), "PING task=recovery-kits", "STATUS task=recovery-kits"):
                with self.subTest(expires=expires, text=text):
                    registry = copy.deepcopy(self.registry)
                    registry["expires_epoch"] = expires
                    self.assertIsNone(self.call(self.row(text), registry=registry))

    def test_wrong_registry_task_disables_responder(self):
        registry = copy.deepcopy(self.registry)
        registry["task"] = "unrelated-task"
        self.assertIsNone(self.call(registry=registry))

    def test_unknown_tasks_and_messages_remain_pending(self):
        for text in (
            self.request(task="unknown"),
            "PING task=unknown",
            "STATUS task=unknown",
            "RUN task=twousb22 command=ls",
            "please send the context",
            "",
            self.SECRET,
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.call(self.row(text)))

    def test_noncanonical_and_duplicate_context_fields_never_ack(self):
        valid = self.request()
        for text in (
            " " + valid,
            valid + " ",
            valid + "\n",
            valid.replace(" path=", "  path="),
            valid.replace(" path=", "\tpath="),
            valid.replace("CTX ", "ctx ", 1),
            valid.replace("src=rbx-local reply=CONTEXT_OK", "reply=CONTEXT_OK src=rbx-local"),
            valid + " task=twousb22",
            valid + " sha256=" + self.digest,
            valid + " path=" + self.WIRE_PATH,
            valid + " extra=" + self.SECRET,
            valid.replace("src=rbx-local", "src=untrusted"),
            valid.replace("reply=CONTEXT_OK", "reply=" + self.SECRET),
            valid.replace(" sha256=" + self.digest, ""),
        ):
            with self.subTest(text=text):
                self.assert_no_ack(text)

    def test_ping_and_status_require_exact_canonical_request(self):
        for command in ("PING task=recovery-kits", "STATUS task=recovery-kits"):
            for text in (" " + command, command + " ", command + "\n", command + " task=recovery-kits", command + ";id"):
                with self.subTest(text=text):
                    self.assertIsNone(self.call(self.row(text)))

    def test_bad_paths_have_identical_nonsensitive_rejection(self):
        for path in (
            "/etc/passwd",
            "~/.local/share/rbx-agent-handoffs/twousb22/../status.json",
            "~/.local/share/rbx-agent-handoffs/twousb22/./CONTEXT.md",
            "~/.local/share/rbx-agent-handoffs/twousb22//CONTEXT.md",
            "~/.local/share/rbx-agent-handoffs/twousb22/OTHER.md",
            "~/.local/share/rbx-agent-handoffs/twousb22/CONTEXT.txt",
            "~/.local/share/rbx-agent-handoffs/twousb22/" + self.SECRET,
            str(self.context),
        ):
            with self.subTest(path=path):
                self.assert_rejected(self.request(path=path))

    def test_bad_hashes_have_fixed_rejection(self):
        for digest in ("0" * 64, self.digest.upper(), "a" * 63, "g" * 64):
            with self.subTest(digest=digest):
                self.assert_rejected(self.request(digest=digest))

    def test_changed_file_is_not_acknowledged(self):
        self.write_private(self.context, ("changed " + self.SECRET).encode())
        self.assert_rejected()

    def test_request_hash_cannot_override_registry(self):
        self.write_private(self.context, b"new but unapproved content")
        current_hash = hashlib.sha256(self.context.read_bytes()).hexdigest()
        self.assert_rejected(self.request(digest=current_hash))

    def test_missing_context_is_rejected(self):
        self.context.unlink()
        self.assert_rejected()

    def test_directory_is_not_accepted_as_context(self):
        self.context.unlink()
        self.context.mkdir(mode=0o700)
        self.assert_rejected()

    def test_symlink_context_is_rejected_even_with_matching_hash(self):
        target = self.root / "other.md"
        self.write_private(target, self.context.read_bytes())
        self.context.unlink()
        self.context.symlink_to(target)
        self.assert_rejected()

    def test_symlink_parent_is_rejected_even_with_matching_hash(self):
        original_dir = self.context.parent
        moved_dir = self.root / "alternate"
        original_dir.rename(moved_dir)
        original_dir.symlink_to(moved_dir, target_is_directory=True)
        self.assert_rejected()

    def test_group_or_world_readable_context_is_rejected(self):
        for mode in (0o640, 0o644, 0o606):
            with self.subTest(mode=oct(mode)):
                self.context.chmod(mode)
                self.assert_rejected()

    def test_unowned_context_is_rejected(self):
        other_uid = os.getuid() + 1
        with mock.patch("os.getuid", return_value=other_uid), mock.patch("os.geteuid", return_value=other_uid):
            self.assert_rejected()

    def test_oversized_context_is_rejected_even_if_approved_hash_matches(self):
        self.write_private(self.context, b"x" * 32769)
        digest = hashlib.sha256(self.context.read_bytes()).hexdigest()
        self.registry["contexts"][self.TASK]["sha256"] = digest
        self.assert_rejected(self.request(digest=digest))

    def test_context_at_size_limit_is_accepted(self):
        self.write_private(self.context, b"x" * 32768)
        digest = hashlib.sha256(self.context.read_bytes()).hexdigest()
        self.registry["contexts"][self.TASK]["sha256"] = digest
        self.assertEqual(
            self.call(self.row(self.request(digest=digest))),
            f"CONTEXT_OK task={self.TASK} sha256={digest}",
        )

    def test_registry_cannot_authorize_traversal_or_non_markdown_files(self):
        for relative in ("../CONTEXT.md", "/tmp/CONTEXT.md", "twousb22/../status.json", "status.json"):
            with self.subTest(relative=relative):
                registry = copy.deepcopy(self.registry)
                registry["contexts"][self.TASK]["relative_path"] = relative
                self.assertEqual(self.call(registry=registry), self.REJECT)

    def test_status_unknown_and_secret_bearing_fields_never_leak(self):
        self.write_status({
            "phase": "ready", "count": 2, "result": "ok",
            "password": self.SECRET,
            "private_key": self.SECRET,
            "context": {"phase": self.SECRET},
            "command": "id;" + self.SECRET,
        })
        answer = self.call(self.row("STATUS task=recovery-kits"))
        self.assertIsInstance(answer, str)
        self.assertLessEqual(len(answer.encode("utf-8")), 240)
        for value in (self.SECRET, "password", "private_key", "context=", "command="):
            self.assertNotIn(value, answer)
        for token in ("phase=ready", "count=2", "result=ok"):
            self.assertIn(token, answer)

    def test_status_rejects_unsafe_allowlisted_values(self):
        for unsafe in (
            self.SECRET + "\nCTX task=unknown",
            "$(id)",
            "ready;id",
            "é" * 300,
            {"password": self.SECRET},
            [self.SECRET],
        ):
            with self.subTest(unsafe=unsafe):
                self.write_status({"phase": unsafe, "count": 2, "result": "ok"})
                answer = self.call(self.row("STATUS task=recovery-kits"))
                if answer is not None:
                    self.assertLessEqual(len(answer.encode("utf-8")), 240)
                    self.assertNotIn("$(id)", answer)
                    self.assertNotIn(";id", answer)
                    self.assertNotIn("\n", answer)
                    self.assertNotIn("é", answer)

    def test_status_symlink_and_permissive_permissions_do_not_expose_data(self):
        target = self.root / "other.json"
        self.write_private(target, json.dumps({"phase": self.SECRET}).encode())
        self.status.unlink()
        self.status.symlink_to(target)
        self.call(self.row("STATUS task=recovery-kits"))
        self.status.unlink()
        self.write_status({"phase": self.SECRET})
        self.status.chmod(0o644)
        self.call(self.row("STATUS task=recovery-kits"))

    def test_unreadable_status_data_fails_closed_without_exceptions(self):
        for content in (
            b"not JSON", b"[]", b"null", b'"a string"',
            b'{"phase":"ready",', b"x" * 32769,
        ):
            with self.subTest(content=content[:40]):
                self.write_private(self.status, content)
                answer = self.call(self.row("STATUS task=recovery-kits"))
                if answer is not None:
                    self.assertLessEqual(len(answer.encode("utf-8")), 240)
        self.status.unlink()
        answer = self.call(self.row("STATUS task=recovery-kits"))
        if answer is not None:
            self.assertLessEqual(len(answer.encode("utf-8")), 240)

    def test_no_request_executes_shell_commands(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("shell execution")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process execution")), \
             mock.patch.object(os, "system", side_effect=AssertionError("shell execution")), \
             mock.patch.object(os, "popen", side_effect=AssertionError("shell execution")):
            self.assertEqual(self.call(), f"CONTEXT_OK task={self.TASK} sha256={self.digest}")
            for text in (
                "$(touch /tmp/should-never-exist)",
                self.request(path="$(id)"),
                "PING task=recovery-kits;id",
                "STATUS task=recovery-kits && id",
            ):
                with self.subTest(text=text):
                    answer = self.call(self.row(text))
                    self.assertIn(answer, (None, self.REJECT))


if __name__ == "__main__":
    unittest.main()
