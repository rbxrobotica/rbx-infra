# PostgreSQL 16.15 amd64. Build from the repository root; publish only after scans.
FROM docker.io/library/postgres@sha256:efdf07c2f9d4df592783dcc8ea5f6db02efbf5f6452b527225ff5e58364570e9

LABEL org.opencontainers.image.title="RBX Comms preservation" \
      org.opencontainers.image.description="Encrypted logical backup of the Comms PostgreSQL database" \
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

COPY --chown=0:0 scripts/plausible/backup_runner.py scripts/plausible/s3_archive.py /app/plausible/
COPY --chown=0:0 scripts/comms/backup_runner.py scripts/comms/s3_archive.py /app/comms/

# No kubectl or Kubernetes token is required for this PostgreSQL-only job.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app
USER 10001:10001
ENTRYPOINT ["python3", "/app/comms/backup_runner.py"]
