# Database Architecture

**Date:** 2026-04-07
**Status:** Active

## Single database host: jaguar (161.97.147.76)

All application databases run on jaguar as ParadeDB (PostgreSQL-compatible).
No application should run its own database StatefulSet inside k3s.

## Databases

| Database | User | Application |
|----------|------|-------------|
| robson | robson | Robson v1 (Django) |
| robson_v2 | robson_v2 | robsond (Rust daemon) |
| truthmetal | truthmetal | Truthmetal |
| pdns | pdns | PowerDNS (used by pantera/ns1) |

## Connection pattern for k8s apps

Apps do not connect directly to jaguar's IP. Each app namespace has a headless
Service + Endpoints object that routes to jaguar:

```yaml
# Example: apps/prod/truthmetal/postgres-svc.yml
apiVersion: v1
kind: Service
metadata:
  name: truthmetal-postgres
  namespace: truthmetal
spec:
  ports:
    - port: 5432
---
apiVersion: v1
kind: Endpoints
metadata:
  name: truthmetal-postgres
  namespace: truthmetal
subsets:
  - addresses:
      - ip: 161.97.147.76
    ports:
      - port: 5432
```

The app's `DATABASE_URL` points to the Service name (e.g. `truthmetal-postgres:5432`),
not to jaguar's IP directly. This decouples apps from the infrastructure IP.

## Provisioning

The `paradedb` Ansible role creates databases and users on jaguar.
The `pdns-database` Ansible role creates the pdns database for PowerDNS.

Both are in `bootstrap/ansible/roles/` and run from `site.yml`.

Passwords live in `bootstrap/ansible/group_vars/vault.yml` (gitignored, ansible-vault encrypted).

## Access control

jaguar's `pg_hba.conf` is scoped per database: each app's user can only access
its own database. Cross-database access is not permitted.
Pantera (ns1) connects to jaguar port 5432 for the `pdns` database only.
Eagle (ns2) does NOT connect to jaguar — it uses the bind backend (AXFR only).

## Lesson from 2026-04-07 migration

During the pantera/eagle DNS split, a `truthmetal-postgres` StatefulSet was found
running on pantera with a local-path PVC. This StatefulSet was NOT in the kustomization
and was never managed by ArgoCD — it was an orphan from an earlier manual deployment.

The application was already connecting to jaguar via the External Service. The
StatefulSet was deleted and the PVC reclaimed as part of the pantera drain.

**Rule:** Never create database StatefulSets inside k3s. All persistent relational
storage goes to jaguar. The External Service pattern is the correct abstraction.
