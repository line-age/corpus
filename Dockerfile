# syntax=docker/dockerfile:1

# Targets:
#   dev      everything including test tools, installed editable; used by compose.yaml
#   ingest   the corpus-ingest CLI, without the API; used by compose.yaml
#   production  the shippable image: production dependencies only, non-root (default target;
#               keep it the last stage)

ARG PYTHON_VERSION=3.13
# What both targets pass to uvicorn. --factory: uvicorn calls create_app() when it starts,
# so importing corpus.api.main builds nothing. --no-access-log: RequestLoggerMiddleware
# logs each request, with its duration. The port isn't here: it comes from $API_PORT when
# the container starts, and sh doesn't expand a variable that is inside another one's value.
ARG UVICORN_ARGS="--factory corpus.api.main:create_app --host 0.0.0.0 --no-access-log"

FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

FROM python:${PYTHON_VERSION}-slim AS base
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH
WORKDIR /app

FROM base AS build
COPY pyproject.toml uv.lock ./
# uv needs every workspace member's pyproject.toml to resolve the lockfile.
COPY packages ./packages
COPY apps ./apps
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-workspace
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM base AS dev
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-workspace
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked
EXPOSE 9001
# CMD can't read a build arg, so copy it into the environment for sh to expand.
ARG UVICORN_ARGS
ENV UVICORN_ARGS="${UVICORN_ARGS}"
# sh expands UVICORN_ARGS (unquoted, so it splits into separate arguments) and API_PORT;
# exec replaces sh with uvicorn, so uvicorn is PID 1 and gets SIGTERM.
CMD ["sh", "-c", "exec uvicorn $UVICORN_ARGS --port \"${API_PORT:-9001}\" --reload --reload-dir src"]

FROM base AS ingest-build
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
# Non-editable: the ingest stage copies only the virtualenv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package corpus-ingest

FROM python:${PYTHON_VERSION}-slim AS ingest
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
# A new named volume mounted at /raw takes this ownership.
RUN groupadd --system corpus && useradd --system --gid corpus --no-create-home corpus \
    && mkdir /raw && chown corpus:corpus /raw
COPY --from=ingest-build /opt/venv /opt/venv
USER corpus
ENTRYPOINT ["corpus-ingest"]
CMD ["--help"]

FROM python:${PYTHON_VERSION}-slim AS production
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
RUN groupadd --system corpus && useradd --system --gid corpus --no-create-home corpus
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations ./migrations
USER corpus
EXPOSE 9001
ARG UVICORN_ARGS
ENV UVICORN_ARGS="${UVICORN_ARGS}"
# Listens on $API_PORT (default 9001); see the dev target for how the command expands.
CMD ["sh", "-c", "exec uvicorn $UVICORN_ARGS --port \"${API_PORT:-9001}\""]
