from __future__ import annotations

from typing import Protocol


class StrategosClient(Protocol):
    def status(self) -> str: ...

    def mission_list(self) -> str: ...

    def mission_status(self, mission_id: str) -> str: ...

    def deploy_status(self) -> str: ...

    def cost_today(self) -> str: ...

    def risk_open(self) -> str: ...


class MockStrategosClient:
    """Safe placeholder that performs no network or state-changing calls."""

    def status(self) -> str:
        return "Strategos API disabled; mock status: adapter online, read-only"

    def mission_list(self) -> str:
        return "Strategos API disabled; no canonical mission list queried"

    def mission_status(self, mission_id: str) -> str:
        return f"Strategos API disabled; mission {mission_id} status unavailable"

    def deploy_status(self) -> str:
        return "Strategos API disabled; deploy status unavailable"

    def cost_today(self) -> str:
        return "Strategos API disabled; cost status unavailable"

    def risk_open(self) -> str:
        return "Strategos API disabled; risk status unavailable"
