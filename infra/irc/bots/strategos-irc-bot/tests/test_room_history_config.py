import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "configure_room_history", ROOT / "scripts/configure-room-history.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def catalog():
    return json.loads((ROOT / "rooms.json").read_text())


def test_history_is_opt_in_private_and_preserves_network_and_authentication():
    original = yaml.safe_load((ROOT / "ergo/ircd.yaml.example").read_text())
    prepared = module.prepare(deepcopy(original), catalog())
    for section in ("server", "accounts", "opers", "channels"):
        assert prepared[section] == original[section]
    assert prepared["history"]["persistent"] == {
        "enabled": True,
        "unregistered-channels": False,
        "registered-channels": "opt-in",
        "direct-messages": "disabled",
    }
    assert prepared["history"]["client-length"] == 0
    assert prepared["history"]["restrictions"]["query-cutoff"] == "join-time"


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant", "other"),
        ("retention_days", True),
        ("retention_days", 0),
        ("retention_days", 91),
    ],
)
def test_invalid_tenant_and_retention_fail_closed(field, value):
    rooms = catalog()
    rooms[field] = value
    with pytest.raises(ValueError):
        module.prepare({}, rooms)


@pytest.mark.parametrize("channel", ["#room\r\nOPER admin", "#room room", "#Room", ""])
def test_invalid_channel_cannot_inject_commands(channel):
    rooms = catalog()
    rooms["rooms"][0]["channel"] = channel
    with pytest.raises(ValueError):
        module.prepare({}, rooms)


def test_duplicate_rooms_and_existing_backend_fail_closed():
    rooms = catalog()
    rooms["rooms"].append(deepcopy(rooms["rooms"][0]))
    with pytest.raises(ValueError):
        module.prepare({}, rooms)
    with pytest.raises(ValueError):
        module.prepare({"datastore": {"postgresql": {"enabled": True}}}, catalog())


def test_private_output_and_no_overwrite(tmp_path, monkeypatch, capsys):
    output = tmp_path / "private.yaml"
    monkeypatch.setattr(
        "sys.argv",
        [
            "configure-room-history.py",
            "--config",
            str(ROOT / "ergo/ircd.yaml.example"),
            "--rooms",
            str(ROOT / "rooms.json"),
            "--output",
            str(output),
        ],
    )
    module.main()
    assert output.stat().st_mode & 0o777 == 0o600
    assert "password:" not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        module.main()
