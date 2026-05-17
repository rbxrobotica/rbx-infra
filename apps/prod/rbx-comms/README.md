# rbx-comms Kubernetes Manifests

## Secrets

The `ghcr-pull-secret` Docker registry secret is created manually:

```bash
kubectl create secret docker-registry ghcr-pull-secret \
  --namespace=rbx-comms \
  --docker-server=ghcr.io \
  --docker-username=<github-username> \
  --docker-password=<github-token>
```

This secret is NOT committed to Git for security reasons.
