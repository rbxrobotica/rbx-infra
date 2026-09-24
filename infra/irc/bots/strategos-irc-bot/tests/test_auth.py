from strategos_irc_bot.auth import AuthorizationPolicy
from strategos_irc_bot.bot import CommandDispatcher
from strategos_irc_bot.client import MockStrategosClient


def test_auth_denies_unknown_account() -> None:
    policy = AuthorizationPolicy.from_accounts(["leandro"])
    assert not policy.is_allowed("mallory")
    assert not policy.is_allowed(None)


def test_auth_allows_configured_account_case_insensitively() -> None:
    policy = AuthorizationPolicy.from_accounts(["Leandro"])
    assert policy.is_allowed("leandro")


def test_read_only_blocks_approve() -> None:
    policy = AuthorizationPolicy.from_accounts(["leandro"], read_only=True)
    dispatcher = CommandDispatcher(policy, MockStrategosClient())
    response = dispatcher.handle("!mission approve ABC", "leandro")
    assert response is not None
    assert response.startswith("blocked:")


def test_private_messages_are_ignored() -> None:
    policy = AuthorizationPolicy.from_accounts(["leandro"])
    dispatcher = CommandDispatcher(policy, MockStrategosClient())
    assert dispatcher.handle("!status", "leandro", is_private=True) is None
