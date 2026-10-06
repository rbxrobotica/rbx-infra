# rbx-comms Kubernetes Manifests

## Secrets

The API Deployment references the existing `rbx-comms/ghcr-pull-secret`
Secret of type `kubernetes.io/dockerconfigjson` to pull the private GHCR image.
Preserve this reference when promoting an image. The image must be verified with
the existing registry credential and pinned by digest before promotion.

Credentials are managed outside Git. Do not recreate or rotate this Secret as
part of an image promotion, copy it from another namespace, or place credential
values in command arguments, logs, or manifests. An authorized image check may
use a private temporary authentication file, which must be deleted afterward;
retain only the image reference, digest, revision and verification result.
