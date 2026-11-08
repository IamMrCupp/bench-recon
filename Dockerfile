# Target is the rpi-k8s cluster (Raspberry Pi 4), so this image must build for
# linux/arm64. The CI workflow builds multi-arch; see .github/workflows/image.yml.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

LABEL org.opencontainers.image.title="bench-recon" \
      org.opencontainers.image.description="Self-hosted engagement/target system of record for hardware/automotive/IoT pentesting." \
      org.opencontainers.image.source="https://github.com/IamMrCupp/bench-recon" \
      org.opencontainers.image.licenses="PolyForm-Small-Business-1.0.0"

# UV_COMPILE_BYTECODE trades a little build time for faster cold starts, which
# matters more than usual on a Pi. UV_LINK_MODE=copy avoids hardlink warnings
# when the cache and the venv sit on different layers.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, in their own layer: they change far less often than the
# source, so an app-only edit reuses this layer instead of re-resolving.
# LICENSE and NOTICES ride along with the project metadata: the PolyForm terms
# require that anyone who gets a copy, an image pull included, gets the license
# and its Required Notice line. They land at /app/LICENSE and /app/NOTICES.
COPY pyproject.toml uv.lock README.md LICENSE NOTICES ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
RUN uv sync --frozen --no-dev

# Non-root. The data PVC is mounted with fsGroup 1000 on the cluster, matching
# the pattern apps/recipe-box and benchhud-intake use.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin recon \
    && chown -R recon:recon /app \
    && mkdir /data && chown recon:recon /data
USER recon

# The data folder: the database and artifact blobs live here. The cluster and
# compose both mount their volume at /data. Without these the app would write
# into /app inside the container and lose everything on the next recreate.
# BENCHRECON_DATA_DIR puts settings.env there too, so
# `bench-recon set-password` / `new-token` run in the container write to the volume.
ENV BENCHRECON_DATA_DIR=/data \
    BENCHRECON_DB_PATH=/data/bench-recon.db \
    BENCHRECON_ARTIFACTS_PATH=/data/artifacts

EXPOSE 8000

CMD ["bench-recon", "serve", "--host", "0.0.0.0", "--port", "8000"]
