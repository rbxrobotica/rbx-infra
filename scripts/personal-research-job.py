"""Render a reviewed one-off Job from the live CronJob. Never submits it."""

import argparse
import json
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("collect", "report", "migrate"))
    parser.add_argument("name", help="unique Kubernetes Job name")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,61}[a-z0-9]", args.name):
        parser.error("invalid Job name")
    cron = json.loads(subprocess.check_output(["kubectl", "-n", "personal-research", "get", "cronjob", "collector", "-o", "json"]))
    spec = cron["spec"]["jobTemplate"]["spec"]
    pod = spec["template"]["spec"]
    container = pod["containers"][0]
    if not re.fullmatch(r"ghcr.io/rbxrobotica/personal-research(?::sha-[0-9a-f]{7,40})?@sha256:[0-9a-f]{64}", container["image"]):
        parser.error("publish and pin an immutable image digest first")
    if args.mode == "report":
        container["args"] = ["--latest", "--html-stdout", "--output", "/work"]
    elif args.mode == "migrate":
        container["command"] = ["python3", "scripts/migrate_swiss_watch.py"]
        container["args"] = []
        container["env"] = [
            {"name": "CAREER_MIGRATION_DATABASE_URL", "valueFrom": {"secretKeyRef": {"name": "migration", "key": "CAREER_MIGRATION_DATABASE_URL"}}},
            {"name": "PGSSLROOTCERT", "value": "/etc/postgresql/ca.crt"},
        ]
        spec["backoffLimit"] = 0
    print(json.dumps({"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": args.name, "namespace": "personal-research"}, "spec": spec}, indent=2))


if __name__ == "__main__":
    main()
