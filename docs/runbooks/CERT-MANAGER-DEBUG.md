# cert-manager Debug Runbook

**Audience:** engineer who can `kubectl describe` against the
production cluster.
**Goal:** diagnose and unstick a `Certificate` that is not
becoming `Ready: True`.

---

## The flow, briefly

When an `Ingress` declares a `cert-manager.io/cluster-issuer`
annotation, cert-manager creates:

```
Certificate  →  CertificateRequest  →  Order  →  Challenge
                                                   ↓
                                       cert-manager solver pod
                                                   ↓
                                       HTTP-01 from Let's Encrypt
                                                   ↓
                                       success → Order valid → Cert issued
```

`Ready: True` means the chain completed and the TLS Secret
referenced by the Ingress contains a real Let's Encrypt cert.

---

## Triage

```bash
KUBECONFIG=~/.kube/config-rbx kubectl get certificate -A | head
```

For any `Ready: False`:

```bash
ns=robson      # adjust
name=robson-frontend-v2-rbxsystems-ch-tls
kubectl describe certificate -n $ns $name | tail -30
kubectl get certificaterequest -n $ns | grep $name
kubectl get order -n $ns | head
kubectl get challenge -A
```

The most useful output is `kubectl describe challenge` — it
contains the exact reason for failure.

---

## Common failure modes

### A. DNS not yet propagated

**Signal:** `Challenge` stays in `pending` for several minutes;
`Status: Waiting for DNS-01 challenge propagation: NXDOMAIN
response from authoritative servers...` (or HTTP-01 equivalent).

**Cause:** the host has an A record on ns1/ns2 but Let's Encrypt's
HTTP solver pod cannot resolve it through public recursors yet,
either because TTL hasn't expired on a previous negative cache or
because the record was just created.

**Fix:** wait. Public negative cache TTL is typically 300s. If
the record was created in the last 15 minutes, do nothing.
cert-manager retries with exponential backoff.

To accelerate: nothing reliable. You can delete the `Challenge`
to force an immediate retry, but if DNS still hasn't propagated,
it just fails faster.

```bash
# Force a re-attempt by deleting the existing Order:
kubectl delete order -n $ns -l cert-manager.io/certificate-name=$name
# cert-manager creates a new Order within ~30s.
```

### B. Ingress not routing `/.well-known/acme-challenge/*`

**Signal:** `Challenge` reports HTTP `404` from the solver
endpoint.

**Cause:** the Ingress for the host blocks the well-known path,
typically because of an overly aggressive `pathType: Exact` rule
or a custom middleware that redirects everything to HTTPS *before*
cert-manager's HTTP-01 path is matched.

**Fix:** the Traefik ingress class as configured in this cluster
allows cert-manager's HTTP solver to inject its own Ingress for
the challenge path automatically. If that injection isn't
happening, check:

```bash
kubectl get ingress -n $ns | grep cm-acme-http-solver
```

If you see a `cm-acme-http-solver-*` Ingress: the solver tried,
but something is intercepting the path. Check Traefik middlewares
applied to your Ingress.

If you don't see one: cert-manager hasn't gotten that far yet;
revisit the Order/Challenge state.

### C. ClusterIssuer in error

**Signal:** `Certificate` describes
`Issuer not ready: signing CA is not yet ready`.

**Cause:** the `letsencrypt-prod` ClusterIssuer is misconfigured
or unreachable.

**Fix:**

```bash
kubectl describe clusterissuer letsencrypt-prod | tail -20
```

Look for ACME registration errors. Resolution is operator-level —
likely needs the operator to re-register or fix the issuer's
private-key Secret.

### D. Rate-limited by Let's Encrypt

**Signal:** `Challenge` reports
`urn:ietf:params:acme:error:rateLimited`.

**Cause:** too many cert requests for the same domain in a short
window. Common when an automated retry loop fires repeatedly.

**Fix:** stop the retry loop. Identify why repeated requests are
happening (usually a bad ingress or a paused namespace creating
many `Certificate` resources). Wait for the rate-limit window to
expire (typically 1 hour for failed validation, longer for
duplicate certs). Switch to staging issuer
(`letsencrypt-staging`) for further iteration if available.

### E. Stale Secret with rotten cert

**Signal:** `Ready: True`, but TLS handshake fails in the browser
because the cert has expired.

**Cause:** the Secret was created before the renewal window and
cert-manager isn't renewing it. Usually means the Certificate
resource was modified in a way that broke the renewal trigger.

**Fix:** delete the Secret; cert-manager re-issues a fresh one
within minutes.

```bash
kubectl delete secret -n $ns $name-tls   # name pattern
```

---

## Force a clean re-issue

When you've ruled out everything and just want to start fresh:

```bash
# 1. Delete the Secret (if it exists)
kubectl delete secret -n $ns ${name}-tls --ignore-not-found

# 2. Delete the Certificate
kubectl delete certificate -n $ns $name

# 3. Re-apply the Ingress (with the cert-manager annotation) — or
#    let ArgoCD sync it back.
kubectl rollout status -n $ns ingress/$name 2>/dev/null || true
```

Within 30s, cert-manager creates a fresh Certificate, Order,
Challenge cycle.

If your Ingress was deployed by ArgoCD, do not `kubectl apply`
manually — let ArgoCD recreate the resources.

---

## Verification after success

```bash
# Cert ready?
kubectl get certificate -n $ns $name -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'

# Real Let's Encrypt issuer?
kubectl get secret -n $ns ${name}-tls -o jsonpath='{.data.tls\.crt}' | \
  base64 -d | openssl x509 -noout -issuer
# Expected: issuer=C=US, O=Let's Encrypt, CN=R...

# HTTPS reachable?
curl -I -m 10 https://<host>/healthz
```

---

## See also

- `docs/INCIDENT-2026-03-28-ARGOCD-OUTOFSYNC.md` — case where an
  ArgoCD reconciliation conflict caused certs to never settle.
- `docs/runbooks/DNS-TROUBLESHOOTING.md` — when the cert is
  blocked at DNS resolution rather than ACME flow.
- cert-manager docs: <https://cert-manager.io/docs/troubleshooting/>
