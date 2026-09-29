#!/usr/bin/env python3
"""Validate the fail-closed scope of the Robson production Secret tag."""

from pathlib import Path
import re
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
ANSIBLE_ROOT = ROOT / "bootstrap/ansible"
TASK_FILE = ANSIBLE_ROOT / "roles/k8s-secrets/tasks/main.yml"
GROUP_VARS_FILE = ANSIBLE_ROOT / "group_vars/all/main.yml"
PLAYBOOK_FILE = ANSIBLE_ROOT / "bootstrap-k8s-secrets-only.yml"
TAG = "robson-prod-secret"
EXPECTED_CLUSTER_UID = "7c54362a-7c64-4d62-a6ec-a14d4843e8a5"

EXPECTED_TASKS = (
    "Require exclusive scoped production rotation tag",
    "Check pass is available",
    "Check kubectl is available",
    "Ensure ~/.kube directory exists",
    "Populate kubeconfig from pass",
    "Restrict kubeconfig permissions",
    "Read production cluster identity marker",
    "Validate production cluster identity marker",
    "Read robsond DB password from pass",
    "Try to read robsond projection-tenant-id from pass",
    "Require existing projection-tenant-id for scoped production rotation",
    "Generate and store new projection-tenant-id (first bootstrap only)",
    "Set tenant ID fact",
    "Validate projection-tenant-id is a canonical UUID",
    "Read robsond Binance API key from pass",
    "Read robsond Binance API secret from pass",
    "Read robsond API token from pass",
    "Validate robsond secret inputs",
    "Read deployed robsond Binance API key for scoped rotation",
    "Require replacement Binance API key for scoped rotation",
    "Create robsond-secret in robson",
)

EXPECTED_SECRET_COMMAND = r"""
kubectl --kubeconfig {{ kubeconfig_path }} create secret generic robsond-secret \
  --namespace {{ robsond_namespace }} \
  --from-literal=database-url="postgresql://{{ paradedb_robson_user }}:$ROBSON_DB_PASSWORD@robson-paradedb:5432/{{ paradedb_robson_db }}" \
  --from-literal=projection-tenant-id="{{ _robson_tenant_id }}" \
  --from-literal=binance-api-key="$ROBSON_BINANCE_API_KEY" \
  --from-literal=binance-api-secret="$ROBSON_BINANCE_API_SECRET" \
  --from-literal=api-token="$ROBSON_API_TOKEN" \
  --dry-run=client -o yaml \
| kubectl --kubeconfig {{ kubeconfig_path }} apply -f -
"""

EXPECTED_SECRET_ENVIRONMENT = {
    "ROBSON_DB_PASSWORD": "{{ robson_db_password_result.stdout | trim }}",
    "ROBSON_BINANCE_API_KEY": "{{ robson_binance_api_key_result.stdout | trim }}",
    "ROBSON_BINANCE_API_SECRET": "{{ robson_binance_api_secret_result.stdout | trim }}",
    "ROBSON_API_TOKEN": "{{ robson_api_token_result.stdout | trim }}",
}

EXPECTED_COMMAND_ACTIONS = {
    "Check pass is available": ("command", "which pass"),
    "Check kubectl is available": ("command", "which kubectl"),
    "Populate kubeconfig from pass": (
        "shell",
        "pass show {{ pass_kubeconfig_key }} > {{ kubeconfig_path }}",
    ),
    "Read production cluster identity marker": (
        "command",
        "kubectl --kubeconfig {{ kubeconfig_path }} get namespace kube-system "
        "-o jsonpath={.metadata.uid}",
    ),
    "Read robsond DB password from pass": (
        "command",
        "pass show {{ pass_robson_db_password_key }}",
    ),
    "Try to read robsond projection-tenant-id from pass": (
        "command",
        "pass show {{ pass_robson_tenant_id_key }}",
    ),
    "Generate and store new projection-tenant-id (first bootstrap only)": (
        "shell",
        "set -e NEW_UUID=$(cat /proc/sys/kernel/random/uuid) "
        "printf '%s' \"$NEW_UUID\" | pass insert --echo {{ pass_robson_tenant_id_key }} "
        ">/dev/null 2>&1 echo \"$NEW_UUID\"",
    ),
    "Read robsond Binance API key from pass": (
        "command",
        "pass show {{ pass_robson_binance_api_key }}",
    ),
    "Read robsond Binance API secret from pass": (
        "command",
        "pass show {{ pass_robson_binance_api_secret }}",
    ),
    "Read robsond API token from pass": (
        "command",
        "pass show {{ pass_robson_api_token_key }}",
    ),
    "Read deployed robsond Binance API key for scoped rotation": (
        "command",
        "kubectl --kubeconfig {{ kubeconfig_path }} get secret robsond-secret "
        "--namespace {{ robsond_namespace }} -o jsonpath={.data.binance-api-key}",
    ),
    "Create robsond-secret in robson": ("shell", EXPECTED_SECRET_COMMAND),
}

ANSIBLE_ACTION_KEYS = frozenset(
    {"assert", "command", "file", "set_fact", "shell"}
)
INCLUDE_ACTION_KEYS = frozenset(
    {
        f"{prefix}{action}"
        for prefix in ("", "ansible.builtin.", "ansible.legacy.")
        for action in (
            "import_playbook",
            "import_role",
            "import_tasks",
            "include_role",
            "include_tasks",
        )
    }
)
KUBECTL_MUTATION = re.compile(
    r"\bkubectl\b.*\b(?:annotate|apply|create|delete|label|patch|replace|rollout|scale)\b",
    re.DOTALL,
)


class ContractError(Exception):
    """The scoped production Secret contract is unsafe or incomplete."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def tags_for(node: dict) -> list[str]:
    tags = node.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    require(isinstance(tags, list), "task tags must be a string or list")
    require(all(isinstance(tag, str) for tag in tags), "every task tag must be text")
    return tags


def nodes(value: object, path: tuple[object, ...] = ()):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from nodes(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from nodes(child, (*path, index))


def conditions(value: object) -> set[str]:
    if isinstance(value, str):
        items = [value]
    else:
        require(isinstance(value, list), "guard conditions must be a string or list")
        items = value
    require(all(isinstance(item, str) for item in items), "guard condition must be text")
    return {"".join(item.split()) for item in items}


def canonical_command(value: object) -> str:
    require(isinstance(value, str), "command body must be text")
    return " ".join(value.split())


def load_documents(path: Path) -> list[object]:
    try:
        return list(yaml.safe_load_all(path.read_text()))
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(f"cannot parse {path.relative_to(ROOT)}") from exc


def validate_tag_scope() -> list[dict]:
    reachable_yaml_paths = sorted(
        set(TASK_FILE.parent.parent.rglob("*.yml"))
        | set(TASK_FILE.parent.parent.rglob("*.yaml"))
        | {PLAYBOOK_FILE}
    )
    for path in reachable_yaml_paths:
        for document in load_documents(path):
            for location, node in nodes(document):
                if "tags" in node:
                    require(
                        "always" not in tags_for(node),
                        f"always tag is forbidden in {path.relative_to(ROOT)} at {location}",
                    )
                require(
                    not (INCLUDE_ACTION_KEYS & node.keys()),
                    f"dynamic/static task inclusion is forbidden in {path.relative_to(ROOT)} "
                    f"at {location}",
                )

    tagged: list[tuple[Path, tuple[object, ...], dict]] = []
    yaml_paths = sorted(set(ANSIBLE_ROOT.rglob("*.yml")) | set(ANSIBLE_ROOT.rglob("*.yaml")))
    for path in yaml_paths:
        for document_index, document in enumerate(load_documents(path)):
            for node_path, node in nodes(document):
                if "tags" in node and TAG in tags_for(node):
                    tagged.append((path, (document_index, *node_path), node))

    wrong_file = [
        (path.relative_to(ROOT), location)
        for path, location, _ in tagged
        if path != TASK_FILE
    ]
    require(not wrong_file, f"{TAG} is used outside {TASK_FILE.relative_to(ROOT)}: {wrong_file}")

    top_level: list[dict] = []
    for _, location, task in tagged:
        require(
            len(location) == 2 and isinstance(location[1], int),
            f"{TAG} must be on a top-level role task; found at {location}",
        )
        require(
            not ({"block", "rescue", "always"} & task.keys()),
            f"{TAG} must not be attached to a task block at {location}",
        )
        require(
            isinstance(task.get("name"), str),
            f"{TAG} task without a text name at {location}",
        )
        top_level.append(task)

    actual_names = tuple(task["name"] for task in top_level)
    require(
        actual_names == EXPECTED_TASKS,
        f"{TAG} task inventory/order changed: expected {EXPECTED_TASKS}, got {actual_names}",
    )
    return top_level


def validate() -> None:
    top_level = validate_tag_scope()
    tasks = {task["name"]: task for task in top_level}

    playbook_documents = load_documents(PLAYBOOK_FILE)
    require(
        playbook_documents
        == [
            [
                {
                    "name": "Bootstrap k8s secrets from pass",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "tags": "k8s-secrets",
                    "roles": ["k8s-secrets"],
                }
            ]
        ],
        "scoped Secret playbook execution graph changed",
    )

    exclusive_guard = tasks["Require exclusive scoped production rotation tag"]
    require(
        conditions(exclusive_guard.get("assert", {}).get("that"))
        == {
            "ansible_run_tags|length==1",
            "ansible_run_tags|first=='robson-prod-secret'",
        },
        "exclusive scoped-tag assertion changed",
    )
    require(
        conditions(exclusive_guard.get("when")) == {f"'{TAG}'inansible_run_tags"},
        "exclusive scoped-tag assertion no longer runs for the rotation tag",
    )

    for name, (expected_module, expected_body) in EXPECTED_COMMAND_ACTIONS.items():
        task = tasks[name]
        actual_modules = ANSIBLE_ACTION_KEYS & task.keys()
        require(
            actual_modules == {expected_module},
            f"{name} action changed: expected {expected_module}, got {sorted(actual_modules)}",
        )
        require(
            canonical_command(task.get(expected_module)) == canonical_command(expected_body),
            f"{name} command body changed",
        )

    kubectl_mutators = []
    for name, task in tasks.items():
        action = task.get("shell", task.get("command", ""))
        if isinstance(action, str) and KUBECTL_MUTATION.search(action):
            kubectl_mutators.append(name)
    require(
        kubectl_mutators == ["Create robsond-secret in robson"],
        f"unexpected scoped kubectl mutation task(s): {kubectl_mutators}",
    )

    group_vars = load_documents(GROUP_VARS_FILE)
    require(len(group_vars) == 1 and isinstance(group_vars[0], dict), "invalid all group vars")
    require(
        group_vars[0].get("rbx_cluster_kube_system_uid") == EXPECTED_CLUSTER_UID,
        "the immutable production kube-system UID marker changed",
    )

    marker_reader = tasks["Read production cluster identity marker"]
    require(
        canonical_command(marker_reader.get("command"))
        == canonical_command(
            "kubectl --kubeconfig {{ kubeconfig_path }} get namespace kube-system "
            "-o jsonpath={.metadata.uid}"
        ),
        "production cluster identity reader command changed",
    )
    require(marker_reader.get("changed_when") is False, "cluster marker reader must be read-only")
    require(marker_reader.get("no_log") is True, "cluster marker reader must use no_log")
    require(
        marker_reader.get("register") == "production_cluster_identity",
        "cluster marker reader register changed",
    )
    marker_guard = tasks["Validate production cluster identity marker"]
    require(
        conditions(marker_guard.get("assert", {}).get("that"))
        == {"production_cluster_identity.stdout|trim==rbx_cluster_kube_system_uid"},
        "production cluster identity assertion changed",
    )
    require(marker_guard.get("no_log") is True, "cluster marker guard must use no_log")
    ordered_names = list(tasks)
    marker_index = ordered_names.index("Validate production cluster identity marker")
    first_secret_read = min(
        index
        for index, name in enumerate(ordered_names)
        if name.startswith("Read ") and ("from pass" in name or "secret" in name.lower())
    )
    require(
        marker_index < first_secret_read,
        "cluster identity must be validated before reading production secrets",
    )

    guard = tasks["Require existing projection-tenant-id for scoped production rotation"]
    assertions = guard.get("assert", {}).get("that")
    require(
        "tenant_id_existing.rc==0" in conditions(assertions),
        "scoped rotation no longer requires an existing production tenant ID",
    )
    require(
        f"'{TAG}'inansible_run_tags" in conditions(guard.get("when")),
        "existing-tenant assertion is no longer scoped to the production rotation tag",
    )

    generator = tasks["Generate and store new projection-tenant-id (first bootstrap only)"]
    generator_conditions = conditions(generator.get("when"))
    require(
        "tenant_id_existing.rc!=0" in generator_conditions,
        "first-bootstrap tenant generation condition was removed",
    )
    require(
        f"'{TAG}'notinansible_run_tags" in generator_conditions,
        "scoped production rotation can generate a replacement tenant ID",
    )
    require(tasks["Set tenant ID fact"].get("no_log") is True, "tenant ID fact must use no_log")

    input_guard = tasks["Validate robsond secret inputs"]
    input_assertions = conditions(input_guard.get("assert", {}).get("that"))
    require(
        {
            "robson_db_password_result.stdout|trim|length>0",
            "robson_binance_api_key_result.stdout|trim|length>0",
            "robson_binance_api_secret_result.stdout|trim|length>0",
            "robson_api_token_result.stdout|trim|length>0",
            "robson_binance_api_key_result.stdout|trim|regex_search('^[A-Za-z0-9]{64}$')",
            "robson_binance_api_secret_result.stdout|trim|regex_search('^[A-Za-z0-9]{64}$')",
        }.issubset(input_assertions),
        "production Robson input validation was weakened",
    )
    require(input_guard.get("no_log") is True, "production input guard must use no_log")

    deployed_key = tasks["Read deployed robsond Binance API key for scoped rotation"]
    require(
        canonical_command(deployed_key.get("command"))
        == canonical_command(
            "kubectl --kubeconfig {{ kubeconfig_path }} get secret robsond-secret "
            "--namespace {{ robsond_namespace }} -o jsonpath={.data.binance-api-key}"
        ),
        "deployed Binance key reader command changed",
    )
    require(deployed_key.get("changed_when") is False, "deployed key reader must be read-only")
    require(deployed_key.get("no_log") is True, "deployed key reader must use no_log")
    require(
        deployed_key.get("register") == "deployed_robson_binance_api_key",
        "deployed key reader register changed",
    )
    require(
        conditions(deployed_key.get("when")) == {f"'{TAG}'inansible_run_tags"},
        "deployed key reader is no longer scoped",
    )

    replacement_guard = tasks["Require replacement Binance API key for scoped rotation"]
    require(
        conditions(replacement_guard.get("assert", {}).get("that"))
        == {
            "deployed_robson_binance_api_key.stdout|length>0",
            "(deployed_robson_binance_api_key.stdout|b64decode|trim)!=(robson_binance_api_key_result.stdout|trim)",
        },
        "replacement Binance API key assertions changed",
    )
    require(replacement_guard.get("no_log") is True, "replacement key guard must use no_log")
    require(
        conditions(replacement_guard.get("when")) == {f"'{TAG}'inansible_run_tags"},
        "replacement key guard is no longer scoped",
    )

    mutation_name = "Create robsond-secret in robson"
    changed_tasks = [name for name, task in tasks.items() if task.get("changed_when") is True]
    require(changed_tasks == [mutation_name], f"unexpected mutating task(s): {changed_tasks}")
    mutation = tasks[mutation_name]
    require(
        set(mutation) == {"name", "shell", "environment", "changed_when", "no_log", "tags"},
        "robsond-secret mutation task shape changed",
    )
    require(
        canonical_command(mutation.get("shell")) == canonical_command(EXPECTED_SECRET_COMMAND),
        "robsond-secret mutation contains a changed or additional command",
    )
    require(
        mutation.get("environment") == EXPECTED_SECRET_ENVIRONMENT,
        "robsond-secret mutation environment changed",
    )
    require(mutation.get("no_log") is True, "robsond-secret mutation must use no_log")
    require(tags_for(mutation) == [TAG], "robsond-secret mutation tag set changed")


def main() -> int:
    try:
        validate()
    except (ContractError, KeyError, TypeError, ValueError) as exc:
        print(f"Robson production Secret scope contract failed: {exc}", file=sys.stderr)
        return 1
    print("Robson production Secret scope contract: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
