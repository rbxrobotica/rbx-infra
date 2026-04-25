# Add a New Application to the Cluster

**Audience:** RBX engineers deploying a new service.
**Prerequisites:** kubectl access, rbx-infra repo clone, ArgoCD
access.

This runbook walks through deploying a brand-new application from
scratch using the GitOps pattern. Every change goes through a PR to
`rbxrobotica/rbx-infra` `main` — never `kubectl apply` directly.

---

## Overview

```
1. Namespace          → core/namespaces/
2. Manifests          → apps/prod/{app}/
3. ArgoCD Application → gitops/app-of-apps/
4. PR + merge         → ArgoCD auto-syncs
```

---

## Step 1: Create the namespace

Add a file in `core/namespaces/` (or add to an existing file if the
namespace belongs to an existing project):

```yaml
# core/namespaces/{app}.yml
apiVersion: v1
kind: Namespace
metadata:
  name: {app}
  labels:
    environment: production
```

---

## Step 2: Create deployment manifests

Create `apps/prod/{app}/` with at minimum these files:

### kustomization.yml

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - namespace.yml
  - {app}-deploy.yml
  - {app}-svc.yml
  - {app}-ingress.yml

labels:
  - pairs:
      app.kubernetes.io/part-of: {app}
      environment: production
    includeSelectors: false
```

### {app}-deploy.yml

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {app}
  namespace: {app}
  labels:
    app.kubernetes.io/name: {app}
    app.kubernetes.io/component: backend
    app.kubernetes.io/part-of: {app}
spec:
  replicas: 2
  selector:
    matchLabels:
      app.kubernetes.io/name: {app}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: {app}
        app.kubernetes.io/component: backend
    spec:
      containers:
      - name: {app}
        image: ghcr.io/rbxrobotica/{app}:sha-XXXXXXXX
        ports:
        - containerPort: 8080
          name: http
        resources:
          requests:
            cpu: "10m"
            memory: "32Mi"
          limits:
            cpu: "200m"
            memory: "128Mi"
        securityContext:
          allowPrivilegeEscalation: false
          runAsNonRoot: true
          capabilities:
            drop:
              - ALL
        livenessProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 2
          periodSeconds: 5
```

### {app}-svc.yml

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {app}
  namespace: {app}
  labels:
    app.kubernetes.io/name: {app}
spec:
  selector:
    app.kubernetes.io/name: {app}
  ports:
    - port: 80
      targetPort: http
      name: http
```

### {app}-ingress.yml

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {app}
  namespace: {app}
  labels:
    app.kubernetes.io/name: {app}
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    traefik.ingress.kubernetes.io/router.middlewares: {app}-redirect-https@kubernetescrd
spec:
  ingressClassName: traefik
  tls:
    - hosts:
        - {app}.rbx.ia.br
      secretName: {app}-tls
  rules:
    - host: {app}.rbx.ia.br
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {app}
                port:
                  number: 80
```

If HTTPS redirect middleware is needed, also create
`{app}-middleware-https.yml` in the same directory.

---

## Step 3: Register with ArgoCD

Create `gitops/app-of-apps/{app}.yml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: {app}
  namespace: argocd
spec:
  project: rbx-applications
  source:
    repoURL: https://github.com/rbxrobotica/rbx-infra
    targetRevision: main
    path: apps/prod/{app}
  destination:
    server: https://kubernetes.default.svc
    namespace: {app}
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      # NEVER add ServerSideApply=true
```

**Important:** See `docs/ARGOCD-BEST-PRACTICES.md` for why
ServerSideApply is forbidden.

---

## Step 4: Add DNS (if external access needed)

In `infra/terraform/dns/`, add a record in the appropriate zone file:

```hcl
resource "powerdns_record" "{app}_rbx_ia_br" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "{app}.rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}
```

Apply via the standard DNS workflow (see `docs/infra/DNS.md`).

---

## Step 5: PR checklist

Before submitting the PR, verify:

- [ ] All files use consistent `app.kubernetes.io/name` labels
- [ ] Image uses SHA tag (`sha-XXXXXXXX`), not `latest`
- [ ] `securityContext` blocks privilege escalation
- [ ] Resource requests and limits are set
- [ ] Health probes point to a valid endpoint
- [ ] No secrets in manifests (use Kubernetes Secrets or external-secrets)
- [ ] ArgoCD Application does NOT use `ServerSideApply=true`
- [ ] Namespace file exists in `core/namespaces/`
- [ ] `kustomization.yml` lists all resources

---

## Step 6: Merge and observe

```bash
# After PR merge, watch ArgoCD sync:
kubectl get application {app} -n argocd -w

# Check pods:
kubectl get pods -n {app} -w

# Check TLS certificate:
kubectl get certificate -n {app}
```

ArgoCD auto-syncs within ~3 minutes. If it doesn't, check the
ArgoCD UI at `argocd.rbx.ia.br` or run:

```bash
kubectl describe application {app} -n argocd
```
