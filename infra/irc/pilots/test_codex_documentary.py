"""Executor contract tests use only disposable fake executables; no inference."""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import codex_documentary as executor


USAGE = {"input_tokens": 10, "cached_input_tokens": 3, "output_tokens": 4,
         "reasoning_output_tokens": 1}


def successful_events():
    return [{"type": "thread.started", "thread_id": "synthetic-thread"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message",
                                                "text": "Resposta sintética."}},
            {"type": "turn.completed", "usage": dict(USAGE)}]


class DocumentaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fake-codex-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / "fake-codex"
        self.record = self.root / "invocations.jsonl"
        self.home = self.root / "auth-home"
        self.home.mkdir()

    def fake(self, code, *, version=executor.EXPECTED_VERSION):
        source = "#!/usr/bin/python3\nimport json, os, signal, sys, time\n"
        source += f"record = {str(self.record)!r}\n"
        source += "with open(record, 'a') as out:\n"
        source += "    out.write(json.dumps({'args': sys.argv[1:], 'env': dict(os.environ), 'cwd': os.getcwd()}) + '\\n')\n"
        source += "if sys.argv[1:] == ['--version']:\n"
        source += f"    print({version!r})\n    raise SystemExit(0)\n"
        source += code + "\n"
        self.binary.write_text(source, encoding="utf-8")
        self.binary.chmod(0o700)

    def emit(self, events=None, suffix=""):
        payload = "\n".join(json.dumps(e) for e in
                            (successful_events() if events is None else events))
        self.fake(f"sys.stdin.read()\nprint({payload!r}, flush=True)\n" + suffix)

    def run_fake(self, **kwargs):
        return executor.run_codex_documentary(
            "Pergunta e documentação sintéticas.", model="explicit-test-model",
            binary=self.binary, codex_home=self.home, live_verified=True, **kwargs)

    def assert_failed(self, result, code):
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, code)
        self.assertIsNone(result.answer)
        self.assertIsNone(result.usage)

    def test_gate_is_closed_without_starting_any_process(self):
        with patch.object(executor.subprocess, "Popen") as popen:
            result = executor.run_codex_documentary("Question", model="explicit")
        self.assertEqual(result.status, "not_run")
        self.assertEqual(result.error_code, "live_gate_not_verified")
        popen.assert_not_called()

    def test_model_is_required_and_empty_or_option_values_are_rejected(self):
        with self.assertRaises(TypeError):
            executor.run_codex_documentary("Question")
        for model in ("", "--help", "model name", None):
            with self.subTest(model=model), patch.object(executor.subprocess, "Popen") as popen:
                result = executor.run_codex_documentary("Question", model=model,
                                                       live_verified=True)
                self.assert_failed(result, "invalid_model")
                popen.assert_not_called()

    def test_success_uses_stdin_clean_environment_and_restrictive_flags(self):
        prompt = "Dado literal: $(touch /tmp/nope) `false` á 🧪"
        stdin_record = self.root / "stdin.txt"
        payload = "\n".join(json.dumps(e) for e in successful_events())
        self.fake(f"open({str(stdin_record)!r}, 'w').write(sys.stdin.read())\n"
                  f"print({payload!r})")
        poison = {"CODEX_API_KEY": "secret-canary", "OPENAI_API_KEY": "secret-canary",
                  "CODEX_ACCESS_TOKEN": "secret-canary", "CODEX_THREAD_ID": "parent",
                  "HTTPS_PROXY": "secret-canary", "RUST_LOG": "trace"}
        with patch.dict(os.environ, poison):
            result = executor.run_codex_documentary(
                prompt, model="explicit-test-model", binary=self.binary,
                codex_home=self.home, live_verified=True)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.answer, "Resposta sintética.")
        self.assertEqual(result.usage, USAGE)
        self.assertEqual(result.executor_version, executor.EXPECTED_VERSION)
        self.assertEqual(stdin_record.read_text(), prompt)
        invocations = [json.loads(line) for line in self.record.read_text().splitlines()]
        self.assertEqual(len(invocations), 2)
        invocation = invocations[1]
        args, env = invocation["args"], invocation["env"]
        self.assertNotIn(prompt, args)
        self.assertEqual(args[-1], "-")
        self.assertEqual(set(env), {"PATH", "HOME", "CODEX_HOME", "TMPDIR", "LANG"})
        self.assertEqual(env["CODEX_HOME"], str(self.home))
        self.assertNotIn("secret-canary", json.dumps(env))
        for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral",
                     "--strict-config", "--json", "--no-daemon"):
            self.assertIn(flag, args)
        self.assertNotIn("--sandbox", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        settings = [args[i + 1] for i, arg in enumerate(args) if arg == "-c"]
        self.assertIn('forced_login_method="chatgpt"', settings)
        self.assertIn('approval_policy="never"', settings)
        self.assertIn('default_permissions="irc_documentary"', settings)
        self.assertIn('permissions.irc_documentary.network.enabled=false', settings)
        self.assertNotIn('features.skip_host_skill_discovery=true', settings)
        for server in ("node_repl", "rbx-agent-irc"):
            self.assertIn(f"mcp_servers.{server}.enabled=false", settings)
            self.assertIn(f'mcp_servers.{server}.command="/usr/bin/false"', settings)
            self.assertIn(f"mcp_servers.{server}.args=[]", settings)
        filesystem = next(v for v in settings if v.startswith("permissions.irc_documentary.filesystem="))
        self.assertIn(json.dumps(invocation["cwd"]) + '="read"', filesystem)
        self.assertFalse(Path(invocation["cwd"]).exists())

    def test_version_mismatch_cannot_start_inference_command(self):
        self.fake("raise RuntimeError('should not reach exec')", version="codex-cli 9.9.9")
        self.assert_failed(self.run_fake(), "unsupported_version")
        self.assertEqual(len(self.record.read_text().splitlines()), 1)

    def test_malformed_duplicate_and_nonfinite_json_are_rejected(self):
        for payload in ("not JSON", "[]", '{"type":"turn.started","type":"error"}',
                        '{"type":"turn.started","value":NaN}'):
            with self.subTest(payload=payload):
                self.fake(f"sys.stdin.read()\nprint({payload!r})")
                result = self.run_fake()
                self.assertIn(result.error_code, {"invalid_json", "invalid_event"})
                self.assertIsNone(result.answer)

    def test_tools_and_unknown_items_fail_immediately(self):
        for item_type in ("command_execution", "file_change", "mcp_tool_call",
                          "web_search", "plan_update", "future_tool"):
            with self.subTest(item_type=item_type):
                events = successful_events()[:2] + [
                    {"type": "item.started", "item": {"type": item_type}}]
                self.emit(events, "time.sleep(20)")
                began = time.monotonic()
                self.assert_failed(self.run_fake(), "tool_event")
                self.assertLess(time.monotonic() - began, 2)

    def test_observed_cli_startup_warning_fails_with_structure_only(self):
        # Shape observed from the installed 0.160.0 CLI with a credential-free
        # loopback provider, matching the tagged upstream ErrorItem emitter.
        events = [successful_events()[0],
                  {"type": "item.completed", "item": {
                      "id": "private-id-canary", "type": "error",
                      "message": "private-warning-canary"}}]
        self.emit(events, "time.sleep(20)")
        result = self.run_fake()
        self.assert_failed(result, "startup_warning")
        self.assertEqual(result.event_shape, {
            "type": "item.completed", "keys": ["item", "type"],
            "phase": "before_turn", "item_type": "error",
            "item_keys": ["id", "message", "type"]})
        self.assertNotIn("private-id-canary", repr(result))
        self.assertNotIn("private-warning-canary", repr(result))

    def test_runtime_warnings_also_fail_closed(self):
        self.emit(successful_events()[:2] + [{"type": "item.completed", "item": {
            "id": "private-id", "type": "error", "message": "private-warning"}}])
        result = self.run_fake()
        self.assert_failed(result, "executor_warning")
        self.assertEqual(result.event_shape["phase"], "active_turn")
        self.assertNotIn("private-warning", repr(result))

    def test_exact_disabled_code_mode_warning_is_recorded_once_before_turn(self):
        warning = {"type": "item.completed", "item": {
            "id": "private-warning-id", "type": "error",
            "message": executor.CODE_MODE_DISABLED_WARNING}}
        events = successful_events()
        self.emit(events[:1] + [warning] + events[1:])
        result = self.run_fake()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.usage, USAGE)
        self.assertEqual(result.startup_warnings, ("code_mode_intentionally_disabled",))
        self.assertNotIn("private-warning-id", repr(result))
        self.assertNotIn(executor.CODE_MODE_DISABLED_WARNING, repr(result))

    def test_disabled_code_mode_warning_variants_and_wrong_positions_fail(self):
        warning = {"type": "item.completed", "item": {
            "id": "private-warning-id", "type": "error",
            "message": executor.CODE_MODE_DISABLED_WARNING}}
        variant = {**warning, "item": {**warning["item"],
                   "message": executor.CODE_MODE_DISABLED_WARNING + " "}}
        fallback = {**warning, "item": {**warning["item"], "message":
                    executor.CODE_MODE_DISABLED_WARNING.replace(
                        "Code mode will fail closed", "Falling back to direct tools")}}
        startup = successful_events()[:1]
        cases = [
            ([warning], "startup_warning"),
            (startup + [variant], "startup_warning"),
            (startup + [fallback], "startup_warning"),
            (startup + [warning, warning], "startup_warning"),
            (startup + [{**warning, "type": "item.started"}], "startup_warning"),
            (successful_events()[:2] + [warning], "executor_warning"),
        ]
        for events, code in cases:
            with self.subTest(events=events):
                self.emit(events)
                self.assert_failed(self.run_fake(), code)

    def test_invalid_json_does_not_report_the_previous_event_shape(self):
        prior = json.dumps(successful_events()[0])
        self.fake(f"sys.stdin.read()\nprint({prior!r})\nprint('private-invalid-json')")
        result = self.run_fake()
        self.assert_failed(result, "invalid_json")
        self.assertIsNone(result.event_shape)
        self.assertNotIn("private-invalid-json", repr(result))

    def test_usage_is_required_and_must_be_nonnegative_integers(self):
        for usage in (None, {}, {**USAGE, "input_tokens": True},
                      {**USAGE, "output_tokens": -1},
                      {**USAGE, "cached_input_tokens": 11}):
            with self.subTest(usage=usage):
                events = successful_events()
                events[-1]["usage"] = usage
                self.emit(events)
                result = self.run_fake()
                self.assertIn(result.error_code, {"missing_usage", "invalid_usage"})
                self.assertIsNone(result.usage)

    def test_missing_answer_incomplete_turn_and_extra_turn_fail(self):
        cases = [
            (successful_events()[:2] + [successful_events()[-1]], "missing_answer"),
            (successful_events()[:-1], "incomplete_turn"),
            (successful_events() + [{"type": "turn.started"}], "unexpected_event"),
        ]
        for events, code in cases:
            with self.subTest(code=code):
                self.emit(events)
                self.assert_failed(self.run_fake(), code)

    def test_nonzero_exit_and_failure_messages_are_not_returned(self):
        self.emit(suffix="sys.stderr.write('secret-canary')\nraise SystemExit(7)")
        result = self.run_fake()
        self.assert_failed(result, "nonzero_exit")
        self.assertEqual(result.exit_code, 7)
        self.assertNotIn("secret-canary", repr(result))
        self.emit([{"type": "error", "message": "secret-canary"}])
        result = self.run_fake()
        self.assert_failed(result, "executor_failed")
        self.assertNotIn("secret-canary", repr(result))

    def test_stdout_and_stderr_are_both_bounded(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                self.fake(f"sys.stdin.read()\nsys.{stream}.write('x' * 20000)\n"
                          f"sys.{stream}.flush()\ntime.sleep(20)")
                self.assert_failed(self.run_fake(max_output_bytes=1024), "output_limit")

    def test_timeout_kills_direct_child_and_descendant(self):
        pid_file = self.root / "pids.json"
        self.fake("sys.stdin.read()\nchild = os.fork()\n"
                  "if child == 0:\n    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                  "    time.sleep(20)\n    raise SystemExit(0)\n"
                  f"open({str(pid_file)!r}, 'w').write(json.dumps([os.getpid(), child]))\n"
                  "time.sleep(20)")
        began = time.monotonic()
        self.assert_failed(self.run_fake(timeout_s=0.5), "timeout")
        self.assertLess(time.monotonic() - began, 2)
        for pid in json.loads(pid_file.read_text()):
            state_file = Path(f"/proc/{pid}/stat")
            # SIGKILL is asynchronous; allow the scheduler to finish the child.
            deadline = time.monotonic() + 0.5
            while state_file.exists():
                try:
                    state = state_file.read_text().split(") ", 1)[1][0]
                except FileNotFoundError:
                    break
                # A terminated orphan may remain a zombie until it is reaped.
                if state == "Z":
                    break
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)

    def test_closed_stdout_does_not_bypass_timeout(self):
        self.fake("sys.stdin.read()\nos.close(1)\nos.close(2)\ntime.sleep(20)")
        self.assert_failed(self.run_fake(timeout_s=0.3), "timeout")

    def test_child_not_reading_stdin_cannot_block_the_deadline(self):
        self.fake("time.sleep(20)")
        began = time.monotonic()
        result = executor.run_codex_documentary(
            "x" * executor.MAX_PROMPT_BYTES, model="explicit", binary=self.binary,
            codex_home=self.home, live_verified=True, timeout_s=0.3)
        self.assert_failed(result, "timeout")
        self.assertLess(time.monotonic() - began, 2)
        for line in self.record.read_text().splitlines():
            self.assertFalse(Path(json.loads(line)["cwd"]).exists())

    def test_excessive_prompt_and_invalid_limits_do_not_start_process(self):
        with patch.object(executor.subprocess, "Popen") as popen:
            result = executor.run_codex_documentary("á" * executor.MAX_PROMPT_BYTES,
                                                    model="explicit", live_verified=True)
            self.assert_failed(result, "input_limit")
            for limit in (0, -1, float("inf"), True, 121):
                result = executor.run_codex_documentary("Question", model="explicit",
                                                        timeout_s=limit, live_verified=True)
                self.assert_failed(result, "invalid_limits")
            for limit in (0, -1, True, executor.MAX_STREAM_BYTES + 1):
                result = executor.run_codex_documentary("Question", model="explicit",
                                                        max_output_bytes=limit,
                                                        live_verified=True)
                self.assert_failed(result, "invalid_limits")
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
