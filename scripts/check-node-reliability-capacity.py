#!/usr/bin/env python3
"""Fail closed before shrinking node Allocatable; prints no workload contents."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import re
import subprocess


def quantity(value):
    match = re.fullmatch(r"([+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)([numkKMGTPE]i?)?", str(value))
    if not match:
        raise ValueError(f"Unsupported resource quantity: {value!r}")
    number, suffix = match.groups()
    factors = {"": 1, "n": Decimal("1e-9"), "u": Decimal("1e-6"), "m": Decimal("1e-3"), "k": 1000, "K": 1000}
    for power, unit in enumerate("MGTPE", 2):
        factors[unit] = 1000 ** power
    for power, unit in enumerate("KMGTPE", 1):
        factors[unit + "i"] = 1024 ** power
    if (suffix or "") not in factors:
        raise ValueError("Unsupported resource suffix")
    return Decimal(number) * factors[suffix or ""]


def pod_request(pod, resource):
    spec = pod["spec"]
    if spec.get("resources"):
        raise ValueError("Pod-level resources need an explicit capacity model")

    def request(container):
        return quantity(container.get("resources", {}).get("requests", {}).get(resource, "0"))

    concurrent_init = Decimal(0)
    init_peak = Decimal(0)
    for container in spec.get("initContainers", []):
        value = request(container)
        if container.get("restartPolicy") == "Always":
            concurrent_init += value
            init_peak = max(init_peak, concurrent_init)
        else:
            init_peak = max(init_peak, concurrent_init + value)
    steady = sum((request(c) for c in spec["containers"]), Decimal(0)) + concurrent_init
    return max(steady, init_peak) + quantity(spec.get("overhead", {}).get(resource, "0"))


def critical_service_hosts(pods, service):
    hosts = set()
    for pod in pods:
        if pod.get("metadata", {}).get("labels", {}).get("k8s-app") != service and pod.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/name") != service:
            continue
        if pod.get("metadata", {}).get("deletionTimestamp"):
            continue
        if any(c["type"] == "Ready" and c["status"] == "True" for c in pod.get("status", {}).get("conditions", [])):
            host = pod.get("spec", {}).get("nodeName")
            if host:
                hosts.add(host)
    return hosts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node")
    parser.add_argument("--peers", nargs="+", required=True)
    args = parser.parse_args()
    base = ["/usr/local/bin/k3s", "kubectl", "--request-timeout=10s"]

    def read(params):
        result = subprocess.run(base + params + ["-o", "json"], check=True, capture_output=True, text=True, timeout=12)
        return json.loads(result.stdout)

    nodes = {n["metadata"]["name"]: n for n in read(["get", "nodes"])["items"]}
    for peer in args.peers:
        if not any(c["type"] == "Ready" and c["status"] == "True" for c in nodes[peer]["status"]["conditions"]):
            raise SystemExit("STOP: another control-plane peer is not Ready")
        address = next(a["address"] for a in nodes[peer]["status"]["addresses"] if a["type"] == "InternalIP")
        subprocess.run(base + ["--server=https://" + address + ":6443", "get", "--raw=/readyz"], check=True, capture_output=True, text=True, timeout=12)
    critical_pods = read(["get", "pods", "-n", "kube-system"])["items"]
    for service in ("traefik", "kube-dns"):
        hosts = critical_service_hosts(critical_pods, service)
        if len(hosts) < 2 or not hosts.difference({args.node}):
            raise SystemExit("STOP: critical services need two Ready hosts before a canary restart")
    pods = read(["get", "pods", "-A", "--field-selector", "spec.nodeName=" + args.node])["items"]
    pods = [p for p in pods if p["status"].get("phase") not in ("Succeeded", "Failed")]
    capacity = nodes[args.node]["status"]["capacity"]
    cpu_target = quantity(capacity["cpu"]) - quantity("750m")
    memory_target = quantity(capacity["memory"]) - quantity("2560Mi") - quantity("500Mi")
    cpu_requested = sum((pod_request(p, "cpu") for p in pods), Decimal(0))
    memory_requested = sum((pod_request(p, "memory") for p in pods), Decimal(0))
    # Explicit allowance for unrequested helper containers; not a usage guarantee.
    memory_allowance = quantity("256Mi")
    meminfo = dict(re.findall(r"^(\w+):\s+(\d+) kB", Path("/proc/meminfo").read_text(), re.MULTILINE))
    available = Decimal(meminfo["MemAvailable"]) * 1024
    pressure = Path("/proc/pressure/memory").read_text()
    full = re.search(r"^full avg10=([0-9.]+)", pressure, re.MULTILINE)
    if not full:
        raise SystemExit("STOP: memory-pressure measurement unavailable")
    fits = cpu_requested <= cpu_target and memory_requested + memory_allowance <= memory_target
    healthy = available >= quantity("1Gi") and Decimal(full.group(1)) < Decimal("1.0")
    print(json.dumps({"node": args.node, "cpu_requested_m": float(cpu_requested * 1000), "cpu_target_m": float(cpu_target * 1000), "memory_requested_Mi": float(memory_requested / quantity("1Mi")), "memory_allowance_Mi": 256, "memory_target_Mi": float(memory_target / quantity("1Mi")), "memory_available_Mi": float(available / quantity("1Mi")), "fits": fits, "host_healthy": healthy}))
    if not fits or not healthy:
        raise SystemExit("STOP: reservations would exceed the checked capacity or host pressure budget")


if __name__ == "__main__":
    main()
