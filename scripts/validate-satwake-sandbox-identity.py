#!/usr/bin/env python3
"""Offline, fail-closed validation of the inert Satwake buyer identity contract.

Default mode runs only local kubectl client rendering. --verify-pass reads the
two recorded identity values locally but never prints them or calls ZITADEL.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import yaml


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "bootstrap/zitadel/satwake-buyer-sandbox.json"
OVERLAY = ROOT / "apps/prod/rbx-briefing-btc-sandbox"
TENANT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://rbx.ia.br/tenant")


class ValidationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def derived_tenant(subject: str) -> uuid.UUID:
    require(bool(subject) and len(subject) <= 255 and subject == subject.strip(),
            "invalid service subject")
    require(not any(ord(char) < 32 or ord(char) == 127 for char in subject),
            "invalid service subject")
    return uuid.uuid5(TENANT_NAMESPACE, subject)


def render_overlay() -> list[dict]:
    env = dict(os.environ, KUBECONFIG=os.devnull)
    try:
        rendered = subprocess.run(
            ["kubectl", "kustomize", str(OVERLAY)],
            cwd=ROOT, env=env, capture_output=True, text=True, check=True, timeout=30,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValidationError("local manifest rendering failed") from exc
    try:
        items = list(yaml.safe_load_all(rendered))
    except yaml.YAMLError as exc:
        raise ValidationError("rendered YAML could not be parsed") from exc
    require(all(isinstance(item, dict) for item in items), "non-object manifest")
    return items


def env_map(deployment: dict) -> dict:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    require(len(containers) == 1, "unexpected deployment container count")
    return {entry["name"]: entry for entry in containers[0]["env"]}


def env_value(items: dict, name: str) -> str | None:
    return items.get(name, {}).get("value")


def verify_preparation(contract: dict, manifests: list[dict]) -> None:
    client = contract["public_client"]
    machine = contract["commerce_machine_user"]
    origin = contract["sandbox_web_origin"]
    require(contract["status"] == "preparation_only", "contract is not preparation-only")
    require(origin.endswith(".invalid") and origin.startswith("https://"),
            "sandbox origin must remain reserved and HTTPS")
    require(client["id"] is None and machine["id"] is None
            and machine["project_id"] is None, "provider IDs were invented")
    require(client["redirect_uri"] == origin + "/briefing-btc/api/auth/callback",
            "OIDC callback does not match the reserved origin")
    require(client["post_logout_redirect_uri"] == origin + "/briefing-btc/signed-out",
            "OIDC logout URI does not match the reserved origin")
    require(client["app_type"] == "OIDC_APP_TYPE_USER_AGENT"
            and client["auth_method"] == "OIDC_AUTH_METHOD_TYPE_NONE"
            and client["response_types"] == ["OIDC_RESPONSE_TYPE_CODE"]
            and set(client["grant_types"]) == {
                "OIDC_GRANT_TYPE_AUTHORIZATION_CODE", "OIDC_GRANT_TYPE_TOKEN_EXCHANGE"}
            and client["pkce_method"] == "S256", "unexpected public PKCE contract")
    require(machine["authentication"] == "private_key_jwt", "unexpected machine auth")
    require(machine["project_grant_role_keys"] == [], "unexpected project roles")
    require(machine["source_properties"] == [
        "RBX_COMMERCE_CLIENT_ID", "RBX_COMMERCE_MACHINE_KEY_JSON",
        "RBX_COMMERCE_AUDIENCE"], "unexpected Commerce credential properties")
    require(machine["source_secret"] != "rbx-session-bff-commerce",
            "production service account reuse")
    require(client["source_secret"] != "rbx-briefing-btc-session-bff-oidc",
            "production PKCE client reuse")
    require(contract["sandbox_policy_gateway"] == "http://127.0.0.1:1/authorize",
            "policy gateway is no longer fail-closed")

    forbidden = {"Application", "Ingress", "IngressRoute", "Certificate",
                 "Role", "RoleBinding"}
    require(not any(item["kind"] in forbidden for item in manifests),
            "overlay contains activation resource")
    deployments = {item["metadata"]["name"]: item for item in manifests
                   if item["kind"] == "Deployment"}
    require(set(deployments) == {"rbx-briefing-btc-sandbox",
                                 "rbx-briefing-btc-sandbox-session-bff"},
            "unexpected sandbox deployments")
    require(all(item["spec"]["replicas"] == 0 for item in deployments.values()),
            "sandbox deployment is not scaled to zero")
    require(all(item["metadata"].get("namespace") == "rbx-briefing-btc-sandbox"
                for item in manifests if item["kind"] != "Namespace"),
            "resource outside sandbox namespace")

    bff = env_map(deployments["rbx-briefing-btc-sandbox-session-bff"])
    web = env_map(deployments["rbx-briefing-btc-sandbox"])
    required_bff = {
        "RBX_IDENTITY_ISSUER": contract["issuer"],
        "RBX_SESSION_BFF_REDIRECT_URI": client["redirect_uri"],
        "RBX_SESSION_BFF_SCOPES": " ".join(client["scopes"]),
        "RBX_COMMERCE_SCOPES": " ".join(machine["requested_scopes"]),
        "RBX_COMMERCE_BASE_URL": contract["commerce_api"],
        "RBX_COMMERCE_TOKEN_ENDPOINT": contract["issuer"] + "/oauth/v2/token",
        "RBX_PRODUCT_KEY": "briefing-btc",
        "RBX_SESSION_BFF_ACCESS_MODE": "annotate",
        "RBX_SESSION_COOKIE_SECURE": "true",
        "RBX_SESSION_BFF_DISCOVER_OIDC": "true",
        "RBX_AUTHZ_GATEWAY_URL": contract["sandbox_policy_gateway"],
    }
    for name, value in required_bff.items():
        require(env_value(bff, name) == value, f"BFF {name} drift")
    require(env_value(web, "ORIGIN") == origin, "web origin drift")
    require(env_value(web, "COMMERCE_BASE_URL") == contract["commerce_api"],
            "web Commerce API drift")
    require(env_value(web, "RBX_SESSION_COOKIE_NAME") ==
            env_value(bff, "RBX_SESSION_COOKIE_NAME"), "session cookie mismatch")
    require(env_value(web, "RBX_SESSION_CSRF_COOKIE_NAME") ==
            env_value(bff, "RBX_SESSION_CSRF_COOKIE_NAME"), "CSRF cookie mismatch")
    require("sandbox" in env_value(web, "RBX_SESSION_COOKIE_NAME"),
            "session cookie is not sandbox-specific")
    require(bff["RBX_COMMERCE_TOKEN_EXCHANGE_CLIENT_ID"]["valueFrom"]["secretKeyRef"] == {
        "name": client["source_secret"], "key": client["source_property"]},
        "token-exchange client ID source drift")
    bff_container = deployments["rbx-briefing-btc-sandbox-session-bff"]["spec"]["template"]["spec"]["containers"][0]
    bff_secrets = bff_container["envFrom"]
    require({entry["secretRef"]["name"] for entry in bff_secrets} == {
        client["source_secret"], machine["source_secret"]},
        "BFF credential source drift")
    web_container = deployments["rbx-briefing-btc-sandbox"]["spec"]["template"]["spec"]["containers"][0]
    require({entry["secretRef"]["name"] for entry in web_container["envFrom"]} == {
        "rbx-briefing-btc-sandbox-content"}, "content source drift")
    require(web["COMMERCE_CONSUMPTION_SERVICE_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "rbx-briefing-btc-sandbox-consumption",
        "key": "COMMERCE_CONSUMPTION_SERVICE_KEY"},
        "consumption credential source drift")

    external = {item["metadata"]["name"]: item for item in manifests
                if item["kind"] == "ExternalSecret"}
    require(set(external) == {client["source_secret"], machine["source_secret"],
                              "rbx-briefing-btc-sandbox-consumption",
                              "rbx-briefing-btc-sandbox-content"},
            "unexpected sandbox Secret mirrors")
    require(all(entry["remoteRef"]["key"] == name
                for name, item in external.items() for entry in item["spec"]["data"]),
            "sandbox Secret mirror reads another source")
    for source_name, keys in (
        (client["source_secret"], [client["source_property"]]),
        (machine["source_secret"], machine["source_properties"]),
    ):
        require(source_name in external, "identity Secret mirror absent")
        actual = external[source_name]["spec"]["data"]
        require({entry["secretKey"] for entry in actual} == set(keys),
                "identity Secret property mismatch")
        require(all(entry["remoteRef"]["key"] == source_name for entry in actual),
                "identity Secret source mismatch")


def read_pass(entry: str) -> str:
    try:
        raw = subprocess.run(["pass", "show", entry], capture_output=True,
                             check=True, timeout=20).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValidationError("recorded pass entry unavailable") from exc
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("recorded pass entry malformed") from exc
    require(value and "\n" not in value and "\r" not in value,
            "recorded pass entry malformed")
    return value


def verify_recorded_subject(contract: dict) -> None:
    machine = contract["commerce_machine_user"]
    subject = read_pass(machine["verified_token_subject_pass_entry"])
    tenant_text = read_pass(machine["public_tenant_pass_entry"])
    try:
        tenant = uuid.UUID(tenant_text)
    except ValueError as exc:
        raise ValidationError("recorded sandbox tenant malformed") from exc
    require(tenant_text == str(tenant) and tenant == derived_tenant(subject),
            "recorded subject and public tenant differ")
    require(tenant != uuid.UUID(contract["production_public_tenant_id"]),
            "recorded sandbox tenant equals production")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-pass", action="store_true",
                        help="also compare the two local recorded pass entries")
    args = parser.parse_args()
    try:
        contract = json.loads(CONTRACT.read_text())
        verify_preparation(contract, render_overlay())
        if args.verify_pass:
            verify_recorded_subject(contract)
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        print(f"sandbox identity contract failed: {exc}", file=sys.stderr)
        return 1
    print("sandbox identity preparation contract: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
