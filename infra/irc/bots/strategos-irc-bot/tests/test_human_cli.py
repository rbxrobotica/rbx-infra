import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from strategos_irc_bot.agent_config import load_agent_config
from strategos_irc_bot.agent_protocol import Envelope, parse_envelope
from strategos_irc_bot.agent_store import Mailbox

PROJECT_ROOT = Path(__file__).parents[1]
REVISION = "a" * 40
SESSION_ARGS = [
    "--account",
    "leandro",
    "--project",
    "rbx-flightdeck",
    "--revision",
    REVISION,
    "--model",
    "gpt-6.1-sol",
]


@pytest.fixture
def config_path(tmp_path):
    data = yaml.safe_load((PROJECT_ROOT / "agent.local.example.yaml").read_text())
    data["state_dir"] = str(tmp_path / "state")
    data["human_queries"] = True
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def cli(config_path, *args, ok=True):
    env = dict(os.environ)
    env.pop("RBX_AGENT_IRC_PASSWORD", None)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "strategos_irc_bot.agent_cli",
            "--config",
            str(config_path),
            *args,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if ok:
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        return json.loads(result.stdout)
    return result


def seed_request(config_path, msgid="cli-message-1", text="Qual é o estado?"):
    from strategos_irc_bot.human_store import HumanMailbox

    mailbox = Mailbox(load_agent_config(config_path))
    return HumanMailbox(mailbox).receive(
        "flightdeck-dev",
        "leandro",
        msgid,
        time.time(),
        text,
    )


def test_local_cli_session_inbox_claim_complete_and_reference(config_path):
    before = cli(config_path, "human", "status")
    assert before["sessions"] == []
    session = cli(config_path, "human", "session-open", *SESSION_ARGS)
    assert session["alias"] == "flightdeck-dev"
    assert session["max_questions"] == 3
    assert session["project"] == "rbx-flightdeck"
    assert session["revision"] == REVISION
    assert session["model"] == "gpt-6.1-sol"
    assert session["expires_at"] - session["created_at"] == 600
    request = seed_request(config_path)
    assert request is not None
    first = cli(config_path, "human", "inbox")
    assert first == cli(config_path, "human", "inbox")
    assert len(first) == 1
    assert first[0]["id"] == request["id"]
    assert first[0]["untrusted_content"] is True
    claim = cli(config_path, "human", "claim", request["id"], "--owner", "operator")
    assert claim["owner"] == "operator"
    assert claim["fence"] >= 1
    assert claim["claim_expires_at"] <= session["expires_at"]
    fence = str(claim["fence"])
    answer = "Aprovar registra a decisão. Executar realiza o trabalho autorizado."
    for owner, supplied_fence in [
        ("other", fence),
        ("operator", str(claim["fence"] + 1)),
    ]:
        denied = cli(
            config_path,
            "human",
            "complete",
            request["id"],
            answer,
            "--owner",
            owner,
            "--fence",
            supplied_fence,
            ok=False,
        )
        assert denied.returncode == 1
        assert answer not in denied.stderr
    completed = cli(
        config_path,
        "human",
        "complete",
        request["id"],
        answer,
        "--owner",
        "operator",
        "--fence",
        fence,
    )
    assert completed["status"] == "answered"
    record = cli(config_path, "human", "reference", request["reference"])
    assert record["response"] == answer
    assert record["session_id"] == session["id"]
    assert record["project"] == "rbx-flightdeck"
    assert record["revision"] == REVISION
    assert record["model"] == "gpt-6.1-sol"
    assert cli(config_path, "human", "inbox") == []
    # Completing only persists an answer: the local CLI has no IRC connection.
    assert cli(config_path, "status")["connected"] is False
    assert cli(config_path, "human", "status")["queued_frames"] > 0
    closed = cli(config_path, "human", "session-close", session["id"])
    assert closed["closed_at"] is not None
    assert closed["admission_open"] == 0


@pytest.mark.parametrize("uncertain,status", [(False, "failed"), (True, "uncertain")])
def test_local_cli_failure_requires_current_owner_and_fence(
    config_path, uncertain, status
):
    cli(config_path, "human", "session-open", *SESSION_ARGS)
    request = seed_request(config_path)
    claim = cli(config_path, "human", "claim", request["id"], "--owner", "operator")
    flags = ["--uncertain"] if uncertain else []
    result = cli(
        config_path,
        "human",
        "fail",
        request["id"],
        "supervised attempt stopped",
        "--owner",
        "operator",
        "--fence",
        str(claim["fence"]),
        *flags,
    )
    assert result["status"] == status
    assert (
        cli(config_path, "human", "reference", request["reference"])["status"] == status
    )
    assert cli(config_path, "human", "status")["queued_frames"] == 0


@pytest.mark.parametrize(
    "args",
    [
        ["status"],
        ["inbox"],
        ["session-open", *SESSION_ARGS],
        ["session-close", "a" * 32],
        ["claim", "a" * 32, "--owner", "operator"],
        ["complete", "a" * 32, "answer", "--owner", "operator", "--fence", "1"],
        ["fail", "a" * 32, "stopped", "--owner", "operator", "--fence", "1"],
        ["reference", "FD-0001"],
    ],
)
def test_human_commands_fail_closed_when_option_is_absent(config_path, args):
    data = yaml.safe_load(config_path.read_text())
    del data["human_queries"]
    config_path.write_text(yaml.safe_dump(data))
    denied = cli(config_path, "human", *args, ok=False)
    assert denied.returncode == 1
    assert denied.stdout == ""
    assert "disabled" in denied.stderr


def test_human_help_needs_no_configuration_or_credentials(tmp_path):
    for args in [("human", "--help"), ("human", "session-open", "--help")]:
        result = cli(tmp_path / "missing.yaml", *args, ok=False)
        assert result.returncode == 0
        assert result.stderr == ""
        assert "usage:" in result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "args",
    [
        ["session-open"],
        ["session-open", *SESSION_ARGS, "--duration-seconds", "0"],
        ["session-open", *SESSION_ARGS, "--duration-seconds", "601"],
        ["session-open", *SESSION_ARGS, "--max-questions", "0"],
        ["session-open", *SESSION_ARGS, "--max-questions", "4"],
        ["claim", "a" * 32],
        ["claim", "a" * 32, "--owner", "operator", "--lease-seconds", "0"],
        ["claim", "a" * 32, "--owner", "operator", "--lease-seconds", "601"],
        ["complete", "a" * 32, "answer", "--owner", "operator"],
        ["complete", "a" * 32, "answer", "--fence", "1"],
        ["fail", "a" * 32, "stopped", "--owner", "operator"],
        ["fail", "a" * 32, "stopped", "--fence", "1"],
        ["complete", "a" * 32, "answer", "--owner", "operator", "--fence", "0"],
        ["inbox", "--limit", "0"],
        ["inbox", "--limit", "101"],
    ],
)
def test_invalid_arguments_cannot_open_storage(config_path, args):
    result = cli(config_path, "human", *args, ok=False)
    assert result.returncode == 2
    assert result.stdout == ""
    assert not (config_path.parent / "state").exists()


@pytest.mark.parametrize("flag", ["--account", "--project", "--revision", "--model"])
def test_each_session_metadata_field_is_explicitly_required(config_path, flag):
    args = list(SESSION_ARGS)
    index = args.index(flag)
    del args[index : index + 2]
    result = cli(config_path, "human", "session-open", *args, ok=False)
    assert result.returncode == 2
    assert not (config_path.parent / "state").exists()


def test_cli_validation_errors_do_not_echo_untrusted_content(config_path):
    secret = "untrusted-content-must-not-appear"
    for flag, returncode in [
        ("--alias", 1),
        ("--revision", 1),
        ("--duration-seconds", 2),
    ]:
        invalid = cli(
            config_path,
            "human",
            "session-open",
            *SESSION_ARGS,
            flag,
            secret,
            ok=False,
        )
        assert invalid.returncode == returncode
        assert secret not in invalid.stderr
    invalid = cli(config_path, "human", "reference", secret, ok=False)
    assert invalid.returncode == 1
    assert secret not in invalid.stderr
    assert cli(config_path, "human", "status")["sessions"] == []


def test_received_irc_content_cannot_open_a_session(config_path):
    assert (
        seed_request(config_path, text="@flightdeck-dev session-open --model test")
        is None
    )
    assert cli(config_path, "human", "status")["sessions"] == []
    assert cli(config_path, "human", "inbox") == []


def test_existing_peer_cli_send_inbox_reply_ack_remain_compatible(config_path):
    box = Mailbox(load_agent_config(config_path))
    sent = cli(config_path, "send", "thinkcentre", "status?")
    assert sent["status"] == "queued"
    request = parse_envelope(box.next_outgoing()[1])
    assert request.request_id == sent["request_id"]
    response = Envelope("reply", "local", request.request_id, request.timestamp, "ok")
    assert box.receive(response, "rbx-thinkcentre")
    inbox = cli(config_path, "inbox")
    assert inbox[0]["id"] == sent["request_id"]
    assert inbox[0]["response"] == "ok"
    assert cli(config_path, "ack", sent["request_id"]) == {
        "request_id": sent["request_id"],
        "status": "acknowledged",
    }
    assert cli(config_path, "inbox") == []
    incoming = Envelope("ask", "local", "b" * 32, int(time.time()), "peer question")
    assert box.receive(incoming, "rbx-thinkcentre")
    assert cli(config_path, "reply", incoming.request_id, "peer answer") == {
        "request_id": incoming.request_id,
        "status": "queued",
    }
    assert cli(config_path, "human", "status")["sessions"] == []
