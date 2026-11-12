# corpus

Lineage trade-press ingest and authority-ranked RAG search.

Part of [Lineage](https://github.com/line-age/controlplane). What this component takes in,
what it puts out, and what it depends on are described in the controlplane's
[components.md](https://github.com/line-age/controlplane/blob/main/components.md#corpus);
this README will say how it is built once it is.

Work is tracked in the [Corpus](https://github.com/orgs/line-age/projects/2) project. The
team lead is [@FlorenceEgwu](https://github.com/FlorenceEgwu). If that changes, the
[controlplane's table](https://github.com/line-age/controlplane#projects-and-leads) is updated first.

## Running it

Everything runs in Docker; the only host requirements are Docker (with Compose) and `make`.

```sh
make setup        # build the images and start the API and Postgres (pgvector)
make db-migrate   # apply migrations, including enabling the vector extension
curl localhost:9001/healthz
```

The first `make` creates `.env` from `.env.example`. With `DOCS_ENABLED=true` (the example
default), interactive docs are at <http://localhost:9001/docs>.

| Target | What it does |
| --- | --- |
| `make help` | List the targets |
| `make setup` | `build`, then `up` |
| `make build` | Build the Docker images |
| `make up` | Start the stack in the background (`docker compose up -d`) |
| `make down` | Stop the stack; the database volume is kept |
| `make test` | Run pytest in a container; pass options with `ARGS="-k health"` |
| `make db-migrate` | Apply Alembic migrations (`alembic upgrade head`) |
| `make lock` | Update `uv.lock` after changing dependencies in `pyproject.toml` |

Tests use their own databases (`<POSTGRES_DB>_test` and `<POSTGRES_DB>_test_bare`), recreated
on every run, so they never touch development data.

## Endpoints

- `GET /` names the service and its version, and links the docs when they are enabled.
- `GET /healthz` reports each dependency (the database, and the pgvector extension in it).
  It answers 200 when all are healthy and 503 when any is not, so it works as a readiness
  probe. Failure details are kept short; the full error goes to the logs.

## Logs

The API writes JSON lines to stdout, one object per record, including uvicorn's own
startup and shutdown messages. Every line has `time` (UTC), `level`, `logger` and
`message`; request lines add `method`, `path`, `status` and `duration_ms`:

```json
{"time": "2026-11-12T15:23:42.861Z", "level": "INFO", "logger": "corpus", "message": "GET /healthz 200 16.5ms", "method": "GET", "path": "/healthz", "status": 200, "duration_ms": 16.5}
```

Log through `corpus.lib.logger.logger`; anything passed as `extra={...}` becomes a key.
Query strings are not logged.

## Configuration

All configuration comes from environment variables, read by `corpus.config.get_settings()`
(once per process, then cached).
Locally they come from `.env`, which is gitignored; elsewhere, set them however the
deployment supplies environment. Every variable has a default.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DOCS_ENABLED` | `false` | Serve `/docs`, `/redoc` and `/openapi.json` |
| `HEALTHZ_TIMEOUT_SECONDS` | `3` | Time limit for each `/healthz` check |
| `LOG_LEVEL` | `INFO` | Lowest level logged: `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `POSTGRES_HOST` | `localhost` | Database host (`corpus-db` under compose) |
| `POSTGRES_PORT` | `5432` | Database port |
| `POSTGRES_USER` | `corpus` | Database user |
| `POSTGRES_PASSWORD` | `corpus` | Database password |
| `POSTGRES_DB` | `corpus` | Database name |
| `POSTGRES_CONNECT_TIMEOUT` | `5` | Seconds to wait for a new connection |
| `POSTGRES_POOL_SIZE` | `5` | Connections each API process keeps open |
| `POSTGRES_MAX_CONNECTIONS` | `10` | Most connections each API process opens; at least `POSTGRES_POOL_SIZE` |
| `API_PORT` | `9001` | Port uvicorn listens on inside the container (read by compose and the image, not the app) |
| `FORWARD_API_PORT` | `9001` | Host port compose publishes the API on, forwarding to `API_PORT` (compose only) |

The Postgres container reads the same `POSTGRES_*` variables, so the API and the database
always agree.

## Layout

```
src/corpus/config.py   settings
src/corpus/db.py       engine, sessions (get_session) and ORM base, shared by search and ingest
src/corpus/lib/        shared helpers; logger.py is the one corpus logger
src/corpus/api/        the search API (FastAPI)
  modules/             one package per feature (health, search), each split into
                       router, service, repository, dto and exceptions
  middlewares/         request logging (method, path, status, duration in ms)
migrations/            Alembic migrations
tests/                 pytest
```

The Dockerfile has two targets: `dev`, which compose uses (test tools included, source
mounted for live reload), and `production`, the default, which is the image to ship: production
dependencies only, running as a non-root user.

Compose puts both services on a bridge network named `lineage`. Other Lineage stacks can
join that network, which is why the services are named `corpus-api` and `corpus-db`
rather than `api` and `db`.
