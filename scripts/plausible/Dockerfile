# PostgreSQL 16.15 bookworm, linux/amd64. Promote the resulting GHCR sha-* image
# only after its workflow publishes it; this Dockerfile does not deploy it.
FROM docker.io/library/postgres@sha256:efdf07c2f9d4df592783dcc8ea5f6db02efbf5f6452b527225ff5e58364570e9 AS kubectl-download

# Direct package versions are reviewed pins. APT verifies repository signatures;
# the final image's immutable digest and vulnerability scan cover transitive OS
# dependencies. curl is used only in this stage and is not added to the runtime.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates=20250419~deb12u1 \
        curl=7.88.1-10+deb12u15 \
    && curl --fail --show-error --silent --location \
        --proto '=https' --tlsv1.2 --max-time 120 \
        https://dl.k8s.io/release/v1.36.5/bin/linux/amd64/kubectl \
        --output /usr/local/bin/kubectl \
    && echo '33bc88a24c3b09cf55bfd59e6ca977881b61fc3f25ab65bc80c65b430248648e  /usr/local/bin/kubectl' | sha256sum --check --strict \
    && chmod 0755 /usr/local/bin/kubectl \
    && rm -rf /var/lib/apt/lists/*

FROM docker.io/library/postgres@sha256:efdf07c2f9d4df592783dcc8ea5f6db02efbf5f6452b527225ff5e58364570e9

LABEL org.opencontainers.image.title="RBX Plausible preservation" \
      org.opencontainers.image.description="Encrypted database backup and bounded weekly analytics tools" \
      org.opencontainers.image.source="https://github.com/rbxrobotica/rbx-infra"

# Patch the base's Perl and PCRE2 packages. The backup process starts directly
# as UID 10001; PostgreSQL's root-switching gosu/entrypoint are not used.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        python3=3.11.2-1+b1 \
        gnupg=2.2.40-1.1+deb12u2 \
        ca-certificates=20250419~deb12u1 \
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

COPY --from=kubectl-download /usr/local/bin/kubectl /usr/local/bin/kubectl
COPY --chown=0:0 backup_runner.py s3_archive.py weekly_report.py collect_weekly.py /app/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app
USER 10001:10001
ENTRYPOINT ["python3", "/app/backup_runner.py"]
