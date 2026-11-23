#!/usr/bin/env python3
"""Fail closed if Robson GitOps regresses to static or direct-Google auth."""

from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
LEGACY_NAMES = {
    "ROBSON_API_TOKEN",
    "ROBSON_LEGACY_API_TOKEN",
    "ROBSON_GOOGLE_WEB_CLIENT_ID",
    "ROBSON_GOOGLE_CLI_CLIENT_ID",
    "ROBSON_ALLOWED_EMAIL",
}
EXPECTED_SECRET_KEYS = {
    "ROBSON_OIDC_AUDIENCE": "oidc-audience",
    "ROBSON_OIDC_ALLOWED_CLIENT_IDS": "oidc-allowed-client-ids",
    "ROBSON_OIDC_TENANT_BINDING": "oidc-tenant-binding",
    "ROBSON_OIDC_ALLOWED_SUBJECTS": "oidc-allowed-subjects",
}


class ContractError(Exception):
    """The desired RBX Identity deployment contract is incomplete."""


def load(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(f"cannot parse {path.relative_to(ROOT)}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{path.relative_to(ROOT)} is not one YAML object")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def validate_environment(environment: str, secret_name: str) -> None:
    base = ROOT / "apps" / ("prod" if environment == "production" else "testnet") / "robson"
    config_path = base / "robsond-config.yml"
    deployment_path = base / "robsond-deploy.yml"
    config = load(config_path).get("data", {})
    require(isinstance(config, dict), f"{config_path.relative_to(ROOT)} data must be a map")
    require(
        config.get("ROBSON_OIDC_ISSUER") == "https://auth.merovelis.com",
        f"{environment} issuer must remain the canonical RBX Identity issuer",
    )
    require(
        config.get("ROBSON_OIDC_JWKS_URI")
        == "https://auth.merovelis.com/oauth/v2/keys",
        f"{environment} JWKS URI must stay on the issuer origin",
    )
    require(
        LEGACY_NAMES.isdisjoint(config),
        f"{environment} ConfigMap contains a removed auth variable",
    )

    deployment = load(deployment_path)
    containers = deployment["spec"]["template"]["spec"]["containers"]
    require(len(containers) == 1, f"{environment} Robson must have one daemon container")
    env_entries = containers[0].get("env", [])
    by_name = {entry.get("name"): entry for entry in env_entries}
    require(
        LEGACY_NAMES.isdisjoint(by_name),
        f"{environment} Deployment contains a removed auth variable",
    )
    for name, key in EXPECTED_SECRET_KEYS.items():
        require(name in by_name, f"{environment} Deployment is missing {name}")
        reference = by_name[name].get("valueFrom", {}).get("secretKeyRef", {})
        require(
            reference == {"name": secret_name, "key": key},
            f"{environment} {name} must use {secret_name}/{key}",
        )


def validate() -> None:
    validate_environment("production", "robsond-secret")
    validate_environment("testnet", "robsond-testnet-secret")

    managed = "\n".join(
        [
            (ROOT / "bootstrap/ansible/roles/k8s-secrets/defaults/main.yml").read_text(),
            (ROOT / "bootstrap/ansible/roles/k8s-secrets/tasks/main.yml").read_text(),
        ]
    )
    require("pass_robson_api_token" not in managed, "production API-token pass input remains")
    require(
        "pass_robson_testnet_api_token" not in managed,
        "testnet API-token pass input remains",
    )
    require("--from-literal=api-token" not in managed, "Kubernetes API token is still emitted")


def main() -> int:
    try:
        validate()
    except (ContractError, KeyError, TypeError) as exc:
        print(f"Robson RBX Identity contract failed: {exc}", file=sys.stderr)
        return 1
    print("Robson RBX Identity contract: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
