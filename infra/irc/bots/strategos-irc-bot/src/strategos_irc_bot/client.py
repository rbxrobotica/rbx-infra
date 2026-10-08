from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChatContext:
    account: str
    channel: str
    tenant_id: str


class StrategosClient(Protocol):
    def status(self, context: ChatContext) -> str: ...

    def mission_list(self, context: ChatContext) -> str: ...

    def mission_status(self, context: ChatContext, mission_id: str) -> str: ...

    def deploy_status(self, context: ChatContext) -> str: ...

    def cost_today(self, context: ChatContext) -> str: ...

    def risk_open(self, context: ChatContext) -> str: ...


class MockStrategosClient:
    """Safe placeholder that performs no network or state-changing calls."""

    def status(self, context: ChatContext) -> str:
        return "Strategos API disabled; mock status: adapter online, read-only"

    def mission_list(self, context: ChatContext) -> str:
        return "Strategos API disabled; no canonical mission list queried"

    def mission_status(self, context: ChatContext, mission_id: str) -> str:
        return f"Strategos API disabled; mission {mission_id} status unavailable"

    def deploy_status(self, context: ChatContext) -> str:
        return "Strategos API disabled; deploy status unavailable"

    def cost_today(self, context: ChatContext) -> str:
        return "Strategos API disabled; cost status unavailable"

    def risk_open(self, context: ChatContext) -> str:
        return "Strategos API disabled; risk status unavailable"
