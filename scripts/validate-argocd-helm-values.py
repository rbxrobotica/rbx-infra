#!/usr/bin/env python3
"""Reject ambiguous inline Helm values: ArgoCD does not merge these fields."""
from pathlib import Path
import sys
import yaml


def main():
    root = Path(__file__).resolve().parents[1]
    paths = [Path(p) for p in sys.argv[1:]] if len(sys.argv) > 1 else [
        p for folder in ("platform", "gitops", "apps")
        for p in (root / folder).rglob("*") if p.suffix in (".yaml", ".yml")
    ]
    errors = []
    applications = 0
    for path in paths:
        for doc in yaml.safe_load_all(path.read_text()):
            if not isinstance(doc, dict) or doc.get("kind") != "Application":
                continue
            applications += 1
            spec = doc.get("spec", {})
            sources = spec.get("sources", []) + ([spec["source"]] if "source" in spec else [])
            for source in sources:
                helm = source.get("helm", {})
                if helm.get("values", "").strip() and helm.get("valuesObject") is not None:
                    errors.append(f"{path}: valuesObject discards values; use one inline representation")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Helm inline values contract: {applications} Applications checked")


if __name__ == "__main__":
    main()
