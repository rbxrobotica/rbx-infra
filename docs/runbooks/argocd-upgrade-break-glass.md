# ArgoCD self-upgrade: break-glass procedure

ArgoCD manages itself through `platform/argocd/application.yml`. A version bump
is therefore applied by the version being replaced. The
`argocd-application-controller` is a **single-replica StatefulSet**: Kubernetes
terminates the old pod before the new one is ready.

If the new version fails to pull, crash-loops, or cannot start against the new
CRDs or config, **there is no controller left to consume a revert committed to
Git**. Rolling back through GitOps is circular in exactly the scenario where a
rollback is needed. Everything below exists to break that circle.

Do not merge an ArgoCD version bump without completing "Before the merge".

---

## Before the merge

**1. Prove you have cluster access independent of ArgoCD.**

```bash
kubectl auth can-i patch statefulset -n argocd
kubectl auth can-i patch deployment -n argocd
```

Both must answer `yes`. If they do not, stop: the recovery path does not exist.

**2. Save the live workloads to a file outside the cluster.**

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p ~/argocd-break-glass
kubectl get statefulset,deployment,configmap -n argocd -o yaml \
  > ~/argocd-break-glass/argocd-${STAMP}.yaml
wc -l ~/argocd-break-glass/argocd-${STAMP}.yaml
```

This file is the rollback. It is worthless if it lives only in the cluster you
are about to change.

**3. Prove the target image exists and is pullable before pointing production
at it.**

```bash
TAG=v3.4.7
docker manifest inspect quay.io/argoproj/argocd:${TAG} >/dev/null \
  && echo "image ok" || { echo "IMAGE MISSING, stop here"; exit 1; }
```

**4. Record the current revision to roll back to.**

```bash
kubectl get application -n argocd argocd \
  -o jsonpath='{.spec.source.targetRevision}{"\n"}'
```

---

## Recovery, if the new controller does not come up

The order matters: restore the workload first, diagnose afterwards.

```bash
# a. Fastest path, if only the pod template changed
kubectl rollout undo statefulset/argocd-application-controller -n argocd
kubectl rollout status statefulset/argocd-application-controller -n argocd

# b. If (a) is not enough, reapply the saved manifests
kubectl apply -f ~/argocd-break-glass/argocd-<STAMP>.yaml

# c. If the controller starts but reconciles wrongly, stop it reconciling
#    before it acts on a bad desired state
kubectl scale statefulset/argocd-application-controller -n argocd --replicas=0
```

Option (c) is the emergency brake. A stopped controller changes nothing, which
is the safe state. Running workloads, including `robsond`, keep running: they do
not depend on ArgoCD at runtime.

After the workload is healthy again, revert `targetRevision` in Git so the
restored controller does not immediately reapply the failed upgrade.

---

## What is NOT at risk

`robsond` does not talk to ArgoCD. A stopped or broken ArgoCD means no deploys
and no self-heal; it does not stop trading, and it does not touch the exchange
insurance stop that bounds loss on an open position (ADR-0039).

Postgres is external to the cluster on jaguar, reached through the hand-written
`Endpoints` objects in `apps/*/`. Those are unaffected by an ArgoCD outage, and
the upgrade explicitly overrides the chart's default `resource.exclusions` so
they stay managed afterwards.

---

## Why a single replica

The chart supports a replicated controller, but sharding a controller is a
larger change than a version bump and has its own failure modes. Running the
upgrade with one replica and a tested break-glass path is the smaller risk.
Revisit if ArgoCD upgrades become routine.
