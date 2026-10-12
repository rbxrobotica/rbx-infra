"""Bounded, supervised Codex consultation; no IRC or API-key fallback.

There is deliberately no command-line entrypoint or switch opening the live
gate. A trusted caller must first verify effective permissions, instruction
sources, MCP configuration, and ChatGPT authentication. ``live_verified`` is a
caller assertion, not a preflight implementation or a security boundary.

The flags target Codex 0.160.0. In particular, loaded legacy ``sandbox_mode``
settings can supersede the named profile despite --ignore-user-config. This
module does not certify that those settings are absent. Ephemeral sessions do
not prohibit Codex runtime/cache/auth writes. Token usage is reported after a
run; the deadline and stream limits are not a hard inference-token ceiling.
"""

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time


CODEX_BINARY = Path("/usr/lib/chatgpt/resources/codex")
EXPECTED_VERSION = "codex-cli 0.160.0"
MAX_PROMPT_BYTES = 32768
MAX_STREAM_BYTES = 1048576
MAX_TIMEOUT_SECONDS = 120
CODE_MODE_DISABLED_WARNING = (
    "Code Mode is unavailable because code-mode host is disabled. "
    "Code mode will fail closed; enable `features.code_mode_host` and install "
    "`codex-code-mode-host`."
)
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "shell_snapshot", "view_image", "apps",
    "plugins", "remote_plugin", "hooks", "browser_use", "browser_use_external",
    "computer_use", "image_generation", "multi_agent", "memories", "chronicle",
    "code_mode", "code_mode_host", "workspace_dependencies", "skill_search",
    "skill_mcp_dependency_install", "tool_suggest", "goals", "sleep_tool",
    "daemon_auto_start",
)


@dataclass(frozen=True)
class DocumentaryResult:
    status: str
    answer: str | None = None
    usage: dict | None = None
    elapsed_ms: int = 0
    exit_code: int | None = None
    error_code: str | None = None
    executor_version: str | None = None
    event_shape: dict | None = None
    startup_warnings: tuple[str, ...] = ()


class _Failure(Exception):
    def __init__(self, code, exit_code=None):
        self.code, self.exit_code = code, exit_code
        super().__init__(code)


def isolated_environment(directory, codex_home):
    """Paths are not credentials; do not inherit any caller environment values."""
    return {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
            "CODEX_HOME": str(codex_home), "TMPDIR": str(directory),
            "LANG": "C.UTF-8"}


def _toml_string(value):
    # JSON string escaping is compatible with TOML basic strings for these
    # generated absolute paths. It is never used as shell escaping.
    return json.dumps(str(value), ensure_ascii=False)


def build_argv(binary, directory, model, codex_home=None):
    home = Path(codex_home) if codex_home is not None else Path.home() / ".codex"
    # Explicitly suppress the host inventory found during read-only preflight.
    # A fresh inventory is still required; these entries are not a universal
    # MCP/skill denylist and empty maps do not erase merged configuration.
    skills = [home / "skills" / ".system" / name / "SKILL.md"
              for name in ("imagegen", "openai-docs", "skill-creator", "skill-installer")]
    skills.append(home / "skills" / "kulinaryos-auth-audit" / "SKILL.md")
    settings = [
        'forced_login_method="chatgpt"', 'model_provider="openai"',
        'approval_policy="never"', 'default_permissions="irc_documentary"',
        "permissions.irc_documentary.filesystem={" +
        _toml_string(directory) + '="read"}',
        "permissions.irc_documentary.network.enabled=false",
        "project_doc_max_bytes=0", "project_doc_fallback_filenames=[]",
        'history.persistence="none"', 'developer_instructions=""',
        "include_environment_context=false", "include_apps_instructions=false",
        "include_collaboration_mode_instructions=false",
        "skills.include_instructions=false", "skills.bundled.enabled=false",
        'web_search="disabled"',
        'shell_environment_policy.inherit="none"',
        "mcp_servers.node_repl.enabled=false",
        'mcp_servers.node_repl.command="/usr/bin/false"',
        "mcp_servers.node_repl.args=[]",
        "mcp_servers.rbx-agent-irc.enabled=false",
        'mcp_servers.rbx-agent-irc.command="/usr/bin/false"',
        "mcp_servers.rbx-agent-irc.args=[]",
        "skills.config=[" + ",".join("{path=" + _toml_string(path) +
                                      ",enabled=false}" for path in skills) + "]",
    ]
    # --ignore-user-config removes the original MCP transport definitions.
    # Disabled entries still undergo transport validation, so each must retain
    # a valid, inert stdio transport instead of an enabled=false-only table.
    args = [str(binary), "--no-daemon", "exec", "--strict-config",
            "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "--skip-git-repo-check", "--json", "--color", "never",
            "--cd", str(directory), "--model", model]
    for setting in settings:
        args.extend(("-c", setting))
    for feature in DISABLED_FEATURES:
        args.extend(("--disable", feature))
    return args + ["-"]


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise _Failure("invalid_json")
        value[key] = item
    return value


def _invalid_constant(_value):
    raise _Failure("invalid_json")


def _schema_name(value):
    # Only short structural labels, never IDs, text, messages, paths or values.
    if isinstance(value, str) and re.fullmatch(r"[a-z_][a-z0-9_.]{0,63}", value):
        return value
    return "<invalid>"


def _event_shape(event, phase):
    if not isinstance(event, dict):
        return {"json_type": type(event).__name__, "phase": phase}
    shape = {"type": _schema_name(event.get("type")),
             "keys": sorted(_schema_name(key) for key in event)[:16],
             "phase": phase}
    item = event.get("item")
    if isinstance(item, dict):
        shape["item_type"] = _schema_name(item.get("type"))
        shape["item_keys"] = sorted(_schema_name(key) for key in item)[:16]
    return shape


class _Events:
    """Fail on unexpected activity while consuming, without retaining raw logs."""

    def __init__(self):
        self.pending = bytearray()
        self.thread_started = False
        self.turn_started = False
        self.completed = False
        self.answer = None
        self.usage = None
        self.last_shape = None
        self.startup_warnings = ()

    def feed(self, data):
        self.pending.extend(data)
        while b"\n" in self.pending:
            line, _, self.pending = self.pending.partition(b"\n")
            self._event(line)

    def _event(self, line):
        self.last_shape = None
        try:
            event = json.loads(line.decode("utf-8"),
                               object_pairs_hook=_unique_object,
                               parse_constant=_invalid_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise _Failure("invalid_json") from None
        phase = ("completed" if self.completed else "active_turn" if self.turn_started
                 else "before_turn" if self.thread_started else "before_thread")
        self.last_shape = _event_shape(event, phase)
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise _Failure("invalid_event")
        kind = event["type"]
        if self.completed:
            raise _Failure("unexpected_event")
        if kind in {"error", "turn.failed"}:
            raise _Failure("executor_failed")
        if kind == "thread.started":
            if self.thread_started or not isinstance(event.get("thread_id"), str):
                raise _Failure("invalid_event")
            self.thread_started = True
        elif kind == "turn.started":
            if not self.thread_started or self.turn_started:
                raise _Failure("invalid_event")
            self.turn_started = True
        elif kind in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise _Failure("invalid_event")
            # Codex 0.160.0 maps startup/runtime warnings to ErrorItem, including
            # before turn.started. Do not accept unknown warnings or mislabel
            # them as malformed protocol. Source: openai/codex rust-v0.160.0,
            # exec/src/event_processor_with_jsonl_output.rs collect_warning and
            # collect_thread_events; independently observed with a local HTTP
            # 400 provider, temporary CODEX_HOME and no authentication.
            if item["type"] == "error":
                # This exact warning confirms the intentionally disabled host;
                # it grants no fallback. Accept it once at the observed startup
                # position, and preserve only a fixed diagnostic label.
                if (kind == "item.completed" and self.thread_started and
                        not self.turn_started and not self.startup_warnings and
                        isinstance(item.get("id"), str) and
                        item.get("message") == CODE_MODE_DISABLED_WARNING):
                    self.startup_warnings = ("code_mode_intentionally_disabled",)
                    return
                raise _Failure("executor_warning" if self.turn_started else "startup_warning")
            if not self.turn_started:
                raise _Failure("invalid_event")
            if item["type"] not in {"agent_message", "reasoning"}:
                raise _Failure("tool_event")
            if kind == "item.completed" and item["type"] == "agent_message":
                if not isinstance(item.get("text"), str) or not item["text"].strip():
                    raise _Failure("invalid_answer")
                self.answer = item["text"].strip()
        elif kind == "turn.completed":
            if not self.turn_started:
                raise _Failure("invalid_event")
            usage = event.get("usage")
            required = ("input_tokens", "cached_input_tokens", "output_tokens")
            if not isinstance(usage, dict) or any(k not in usage for k in required):
                raise _Failure("missing_usage")
            selected = required + (("reasoning_output_tokens",)
                                   if "reasoning_output_tokens" in usage else ())
            if any(type(usage[k]) is not int or usage[k] < 0 for k in selected):
                raise _Failure("invalid_usage")
            if usage["cached_input_tokens"] > usage["input_tokens"]:
                raise _Failure("invalid_usage")
            self.usage = {k: usage[k] for k in selected}
            self.completed = True
        else:
            raise _Failure("unexpected_event")

    def finish(self):
        if self.pending:
            self._event(self.pending)
            self.pending.clear()
        if not self.completed:
            raise _Failure("incomplete_turn")
        if self.answer is None:
            raise _Failure("missing_answer")


def _stop_group(process):
    # Kill the whole session even when the direct child has already exited.
    # A child retaining stdout/stderr must not evade the deadline.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=1)


def _capture(argv, prompt, *, directory, env, deadline, budget, on_stdout=None):
    """One shared byte budget covers both streams and both subprocesses."""
    process = subprocess.Popen(argv, cwd=directory, env=env, shell=False,
                               start_new_session=True, close_fds=True,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    stdout = bytearray()
    remaining_input = memoryview(prompt)
    try:
        with selectors.DefaultSelector() as selector:
            for pipe in (process.stdout, process.stderr):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ)
            if remaining_input:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            else:
                process.stdin.close()
            while selector.get_map() or process.poll() is None:
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    raise _Failure("timeout")
                for key, _ in selector.select(min(remaining_time, 0.1)):
                    pipe = key.fileobj
                    if pipe is process.stdin:
                        try:
                            count = os.write(pipe.fileno(), remaining_input[:8192])
                        except BrokenPipeError:
                            raise _Failure("process_error") from None
                        remaining_input = remaining_input[count:]
                        if not remaining_input:
                            selector.unregister(pipe)
                            pipe.close()
                        continue
                    data = os.read(pipe.fileno(), min(8192, budget[0] + 1))
                    if not data:
                        selector.unregister(pipe)
                        pipe.close()
                        continue
                    budget[0] -= len(data)
                    if budget[0] < 0:
                        raise _Failure("output_limit")
                    if pipe is process.stdout:
                        if on_stdout is None:
                            stdout.extend(data)
                        else:
                            on_stdout(data)
                    # Count stderr, but never retain or publish its contents.
        code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        if code != 0:
            raise _Failure("nonzero_exit", code)
        return bytes(stdout)
    finally:
        _stop_group(process)
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()


def run_codex_documentary(prompt, *, model, timeout_s=60,
                          max_output_bytes=262144, live_verified=False,
                          binary=CODEX_BINARY, codex_home=None):
    """Run once after caller-owned preflight; return no raw diagnostic output.

    ``model`` is mandatory. No retry, automatic model selection, IRC publish,
    or paid API fallback occurs. A failed result never includes a partial answer
    or treats missing token telemetry as zero usage. The caller must account
    for potentially consumed quota even after timeout or another failure.
    """
    started = time.monotonic()
    version = None
    events = None
    if live_verified is not True:
        return DocumentaryResult("not_run", error_code="live_gate_not_verified")
    try:
        if (not isinstance(model, str) or
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model)):
            raise _Failure("invalid_model")
        if not isinstance(prompt, str) or not prompt.strip():
            raise _Failure("invalid_prompt")
        data = prompt.encode("utf-8")
        if len(data) > MAX_PROMPT_BYTES:
            raise _Failure("input_limit")
        if (type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or
                not 0 < timeout_s <= MAX_TIMEOUT_SECONDS or
                type(max_output_bytes) is not int or
                not 0 < max_output_bytes <= MAX_STREAM_BYTES):
            raise _Failure("invalid_limits")
        if os.name != "posix":
            raise _Failure("unsupported_platform")
        binary = Path(binary).resolve(strict=True)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise _Failure("invalid_binary")
        home = Path(codex_home) if codex_home is not None else Path.home() / ".codex"
        home = home.resolve(strict=True)
        if not home.is_dir():
            raise _Failure("invalid_codex_home")
        deadline, budget = started + timeout_s, [max_output_bytes]
        with tempfile.TemporaryDirectory(prefix="irc-documentary-") as directory:
            env = isolated_environment(directory, home)
            reported = _capture([str(binary), "--version"], b"", directory=directory,
                                env=env, deadline=deadline, budget=budget)
            if reported.strip() != EXPECTED_VERSION.encode("ascii"):
                raise _Failure("unsupported_version")
            version = EXPECTED_VERSION
            events = _Events()
            _capture(build_argv(binary, directory, model, home), data, directory=directory,
                     env=env, deadline=deadline, budget=budget, on_stdout=events.feed)
            events.finish()
            return DocumentaryResult("ok", events.answer, events.usage,
                                     int((time.monotonic() - started) * 1000),
                                     0, executor_version=version,
                                     startup_warnings=events.startup_warnings)
    except _Failure as error:
        code, exit_code = error.code, error.exit_code
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        code, exit_code = "process_error", None
    return DocumentaryResult("failed", elapsed_ms=int((time.monotonic() - started) * 1000),
                             exit_code=exit_code, error_code=code,
                             executor_version=version,
                             event_shape=events.last_shape if events else None,
                             startup_warnings=events.startup_warnings if events else ())
