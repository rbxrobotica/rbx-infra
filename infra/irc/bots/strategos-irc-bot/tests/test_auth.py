from strategos_irc_bot.auth import AuthorizationPolicy
from strategos_irc_bot.bot import CommandDispatcher
from strategos_irc_bot.client import ChatContext, MockStrategosClient


class RecordingStrategosClient(MockStrategosClient):
    def __init__(self) -> None:
        self.context: ChatContext | None = None

    def status(self, context: ChatContext) -> str:
        self.context = context
        return "ok"


def test_auth_denies_unknown_account() -> None:
    policy = AuthorizationPolicy.from_accounts(["leandro"])
    assert not policy.is_allowed("mallory")
    assert not policy.is_allowed(None)


def test_auth_allows_configured_account_case_insensitively() -> None:
    policy = AuthorizationPolicy.from_accounts(["Leandro"])
    assert policy.is_allowed("leandro")


def test_read_only_blocks_approve() -> None:
    policy = AuthorizationPolicy.from_accounts(["leandro"], read_only=True)
    dispatcher = CommandDispatcher(policy, MockStrategosClient(), "rbx")
    response = dispatcher.handle(
        "!mission approve ABC", "leandro", channel="#strategos"
    )
    assert response is not None
    assert response.startswith("blocked:")


def test_private_messages_are_ignored() -> None:
    policy = AuthorizationPolicy.from_accounts(["leandro"])
    dispatcher = CommandDispatcher(policy, MockStrategosClient(), "rbx")
    assert (
        dispatcher.handle(
            "!status", "leandro", channel="strategos-bot", is_private=True
        )
        is None
    )


def test_ordinary_channel_conversation_is_ignored() -> None:
    policy = AuthorizationPolicy.from_accounts(["leandro"])
    dispatcher = CommandDispatcher(policy, MockStrategosClient(), "rbx")
    assert (
        dispatcher.handle("vamos revisar a missão", "leandro", channel="#strategos")
        is None
    )
    assert dispatcher.handle("bom dia", "mallory", channel="#strategos") is None


def test_authenticated_command_carries_account_channel_and_tenant_context() -> None:
    policy = AuthorizationPolicy.from_accounts(["leandro"])
    client = RecordingStrategosClient()
    dispatcher = CommandDispatcher(policy, client, "rbx")

    assert dispatcher.handle("!status", "leandro", channel="#strategos") == "ok"
    assert client.context == ChatContext("leandro", "#strategos", "rbx")
