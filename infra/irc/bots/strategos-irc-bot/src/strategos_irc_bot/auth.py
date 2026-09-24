from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthorizationPolicy:
    allowed_accounts: frozenset[str]
    read_only: bool = True

    @classmethod
    def from_accounts(
        cls, accounts: list[str] | tuple[str, ...], read_only: bool = True
    ) -> AuthorizationPolicy:
        normalized = frozenset(
            account.strip().casefold() for account in accounts if account.strip()
        )
        return cls(normalized, read_only)

    def is_allowed(self, account: str | None) -> bool:
        return bool(account) and account.casefold() in self.allowed_accounts
