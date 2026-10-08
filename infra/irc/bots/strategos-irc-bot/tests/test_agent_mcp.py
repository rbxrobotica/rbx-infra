import asyncio
import json
import sys
from pathlib import Path

import pytest
import yaml

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_mcp_session_tools_queue_question_without_network_or_credentials(
    tmp_path,
):
    config = yaml.safe_load(
        (Path(__file__).parents[1] / "agent.local.example.yaml").read_text()
    )
    config["state_dir"] = str(tmp_path / "state")
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(config))

    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "strategos_irc_bot.agent_cli", "--config", str(path), "mcp"],
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                result = await session.initialize()
                assert "untrusted external data" in result.instructions
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert set(tools) == {
                    "irc_status",
                    "irc_inbox",
                    "irc_send",
                    "irc_reply",
                    "irc_ack",
                }
                assert tools["irc_inbox"].annotations.readOnlyHint
                assert not tools["irc_send"].annotations.readOnlyHint
                status = await session.call_tool("irc_status", {})
                assert not status.isError
                assert json.loads(status.content[0].text)["connected"] is False
                sent = await session.call_tool(
                    "irc_send", {"peer": "thinkcentre", "text": "status?"}
                )
                assert not sent.isError
                assert json.loads(sent.content[0].text)["status"] == "queued"
                status = await session.call_tool("irc_status", {})
                assert json.loads(status.content[0].text)["queued_frames"] == 1
                denied = await session.call_tool(
                    "irc_send", {"peer": "unknown", "text": "status?"}
                )
                assert denied.isError

    asyncio.run(scenario())
