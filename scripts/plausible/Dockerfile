# PostgreSQL 16.15 bookworm, linux/amd64. Promote the resulting GHCR sha-* image
# only after its workflow publishes it; this Dockerfile does not deploy it.
FROM docker.io/library/golang@sha256:5e8afd493a7124a8586e134d9b532731161d4b9becf9dee5a5956b107426f93d AS kubectl-build

# Upstream v1.36.6 entry point with locked security dependency updates.
# See kubectl/README.md for provenance; do not disable the final image scan.
ENV GOTOOLCHAIN=local CGO_ENABLED=0 GOOS=linux GOARCH=amd64
WORKDIR /src
COPY kubectl/go.mod kubectl/go.sum ./
RUN go mod download
COPY kubectl/main.go ./
RUN go build -mod=readonly -trimpath \
    -ldflags='-s -w -X k8s.io/component-base/version.gitVersion=v1.36.6-rbx.1 -X k8s.io/component-base/version.gitMajor=1 -X k8s.io/component-base/version.gitMinor=36 -X k8s.io/client-go/pkg/version.gitVersion=v1.36.6-rbx.1' \
    -o /out/kubectl .

FROM docker.io/library/postgres@sha256:efdf07c2f9d4df592783dcc8ea5f6db02efbf5f6452b527225ff5e58364570e9

LABEL org.opencontainers.image.title="RBX Plausible preservation" \
      org.opencontainers.image.description="Encrypted database backup and bounded weekly analytics tools" \
      org.opencontainers.image.source="https://github.com/rbxrobotica/rbx-infra"

# Patch the base's APT, OpenSSL, Perl and PCRE2 packages. The backup process starts directly
# as UID 10001; PostgreSQL's root-switching gosu/entrypoint are not used.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        python3=3.11.2-1+b1 \
        gnupg=2.2.40-1.1+deb12u2 \
        ca-certificates=20250419~deb12u2 \
        apt=2.6.2 \
        libapt-pkg6.0=2.6.2 \
        libssl3=3.0.22-1~deb12u2 \
        openssl=3.0.22-1~deb12u2 \
        perl=5.36.0-7+deb12u4 \
        perl-base=5.36.0-7+deb12u4 \
        perl-modules-5.36=5.36.0-7+deb12u4 \
        libperl5.36=5.36.0-7+deb12u4 \
        libpcre2-8-0=10.42-1+deb12u2 \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /usr/local/bin/gosu /usr/local/bin/docker-entrypoint.sh \
    && groupadd --gid 10001 backup-runner \
    && useradd --uid 10001 --gid 10001 --no-log-init --create-home backup-runner \
    && install -d -m 0755 /app

COPY --from=kubectl-build /out/kubectl /usr/local/bin/kubectl
COPY kubectl/LICENSE /usr/share/licenses/kubectl/LICENSE
COPY --chown=0:0 backup_runner.py s3_archive.py weekly_report.py collect_weekly.py /app/

# The current Kubernetes exec path reset/truncated a WebSocket stream in a
# verified transfer. Use SPDY while retaining TLS and the same RBAC. The runner
# separately verifies native source size/SHA-256; transport exit code is not proof.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KUBECTL_REMOTE_COMMAND_WEBSOCKETS=false \
    HOME=/tmp

WORKDIR /app
USER 10001:10001
ENTRYPOINT ["python3", "/app/backup_runner.py"]
