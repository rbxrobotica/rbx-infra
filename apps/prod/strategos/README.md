# Strategos production

Strategos UI is exposed through a dual-host Ingress:

- `strategos.rbx.ia.br`
- `strategos.rbxsystems.ch`

`strategos.rbxsystems.ch` is managed in `infra/terraform/dns/rbxsystems_ch.tf`.
The `rbx.ia.br` zone is currently documented as externally managed, so
`strategos.rbx.ia.br` must be created where the existing `robson.rbx.ia.br`
record is managed and pointed at the k3s ingress IP (`158.220.116.31`).

The UI image is published by the `ldamasio/strategos-ui` GitHub Actions workflow
as `ghcr.io/ldamasio/strategos-ui:sha-<short-sha>`.

The container package is private because the source repository is private. The
namespace needs an out-of-band `ghcr-ldamasio` `kubernetes.io/dockerconfigjson`
secret with `read:packages` access. Do not commit that token to Git.
