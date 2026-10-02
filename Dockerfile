# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=24
# Python and Node stages share the same Debian release so the copied node binary finds its libs.
ARG DEBIAN_RELEASE=trixie

# --- Spectral CLI (Node) ---------------------------------------------------------------------
FROM node:${NODE_VERSION}-${DEBIAN_RELEASE}-slim AS spectral
# Keep in sync with the version documented in README.md and used in CI.
ARG SPECTRAL_VERSION=6.16.3
RUN npm install --prefix /opt/spectral --omit=dev --no-audit --no-fund \
      "@stoplight/spectral-cli@${SPECTRAL_VERSION}" \
 && npm cache clean --force

# --- Python dependencies -------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_RELEASE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# --- Runtime -------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_RELEASE}
COPY --from=spectral /usr/local/bin/node /usr/local/bin/node
COPY --from=spectral /opt/spectral /opt/spectral
RUN ln -s /opt/spectral/node_modules/.bin/spectral /usr/local/bin/spectral \
 && useradd --create-home --uid 10001 govagent
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY rulesets ./rulesets
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1
USER govagent
CMD ["govagent", "--help"]
