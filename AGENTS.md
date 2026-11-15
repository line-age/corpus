# AGENTS.md

How to work on this codebase: where code goes and the patterns to follow. What corpus is
for, and what it takes in and puts out, is in the controlplane's
[components.md](https://github.com/line-age/controlplane/blob/main/components.md#corpus).
How to run it is in [README.md](README.md). Don't copy either here; link to them.

## Ground rules

- **Everything runs in Docker.** Use the `make` targets. There is no virtualenv on the host,
  so editor warnings like "package not installed" are expected. Run any other Python
  command in the API container: `docker compose run --rm corpus-api <command>`.
- **This repo is public, and it is software, not deployment configuration** (controlplane
  decisions L-0006 and L-0010). No secrets, hostnames, cluster names or
  environment-specific values in code, defaults or docs.
- **Configuration is environment variables only**, and every setting has a sensible default.
- **Writing style:** no em-dashes or en-dashes as separators. Use a spaced hyphen, a
  semicolon or a new sentence.

## Commands

| Task | Command |
| --- | --- |
| Build and start everything | `make setup` (then `make db-migrate` on a fresh database) |
| Start / stop | `make up` / `make down` (the database volume is kept) |
| Run tests | `make test`, or `make test ARGS="-k health -x"` |
| Apply migrations | `make db-migrate` |
| New migration | `docker compose run --rm corpus-api alembic revision --autogenerate -m "add passages"` |
| Update the lockfile | `make lock` |
| Follow API logs | `docker compose logs -f corpus-api` |

The API is published on `localhost:${FORWARD_API_PORT}` (9001 by default).

## Layout

```
src/corpus/
  config.py              Settings and get_settings()
  db.py                  engine and session factories, get_session, ORM Base
  lib/logger.py          the one corpus logger (JSON lines)
  api/
    main.py              create_app(): settings, logging, lifespan, middleware, routers
    middlewares/         request logging
    modules/<feature>/   one package per feature: router, service, repository, dto, exceptions
migrations/versions/     Alembic revisions, named YYYY_MM_DD_<rev>_<slug>.py
tests/                   pytest; fixtures in conftest.py
```

`lib/` and `db.py` are shared with ingest, which is still to come; keep them free of HTTP concerns, except
`get_session`, which is the FastAPI dependency.

## Feature modules

Every feature is a package under `src/corpus/api/modules/`, split into five layers. Copy
the structure of `modules/health/`, which is complete, or `modules/search/`, which is
wired but empty.

| File | Holds | Rules |
| --- | --- | --- |
| `router.py` | `APIRouter`, endpoints, and dependency providers such as `get_search_service` | The only layer that imports FastAPI. Turns results and errors into HTTP responses and status codes. |
| `service.py` | Business logic, as a plain class that takes its repository in `__init__` | No FastAPI, no SQL. Unit-testable with a mocked repository. |
| `repository.py` | Database queries, as a class that takes an `AsyncSession` | Queries only, no business rules. |
| `dto.py` | Pydantic request and response models | Endpoints declare their return type with these. |
| `exceptions.py` | The feature's exceptions | A message that reaches a client must be safe to show (no hostnames or driver errors). |

The providers in `router.py` connect the layers, and FastAPI injects the result:

```python
def get_search_service(session: Annotated[AsyncSession, Depends(get_session)]) -> SearchService:
    return SearchService(SearchRepository(session))

@router.get("")
async def search(service: Annotated[SearchService, Depends(get_search_service)]) -> SearchResponse: ...
```

Mount a new router in `create_app()` in `api/main.py` with `app.include_router(...)`.

A repository uses the engine instead of a session only when its queries must run
concurrently, because an `AsyncSession` must not be shared between tasks. The health
checks are the one case; each method opens its own connection.

## Settings

- Read settings with `get_settings()` from `corpus.config`. It reads the environment once
  per process and caches the result. Don't keep settings in module-level globals or on
  `app.state`, and don't pass them into `create_app()`.
- `make_engine(settings)` takes settings as an argument because it is a factory, and unit
  tests may build `Settings(...)` directly. Otherwise, call `get_settings()`.
- To add a setting:
  1. Add a field to `Settings` with a default, a docstring and constraints (`Field(ge=...)`,
     validators).
  2. Add it to `.env.example` **and** to the local `.env` in the same change. Keep any
     values already set in `.env`.
- `API_PORT` and `FORWARD_API_PORT` are read by compose and the Dockerfile, not by the app,
  so they are not `Settings` fields. They are only ever set in `.env`.

## Database

- The engine and sessionmaker are created in `create_app()`'s lifespan, live on
  `app.state`, and are disposed at shutdown. Never create an engine at import time.
- Endpoints get a session through `Depends(get_session)`, usually by way of the service
  provider. Commit explicitly; anything uncommitted is rolled back when the request ends.
- Models subclass `corpus.db.Base`. Alembic autogenerate only sees models whose modules
  have been imported, so import new model modules in `migrations/env.py`.
- Always read an autogenerated migration before applying it. Autogenerate misses
  extensions and some index types, which matters for pgvector. Never edit a migration that
  has already been applied; add a new one.
- `POSTGRES_POOL_SIZE` and `POSTGRES_MAX_CONNECTIONS` apply per process. Total connections
  are that maximum times the number of workers and replicas.

## Logging

- Log through `from corpus.lib.logger import logger`. Never use `logging.getLogger(...)` or
  `print`.
- Output is JSON lines on stdout. Keep the message readable, and put values in
  `extra={...}` so each becomes its own field:
  `logger.warning("health check %r failed", name, exc_info=True, extra={"check": name})`.
- Never log secrets, credentials or query strings. Put the full error detail in the log,
  and keep the client response brief, as `/healthz` does.
- `RequestLoggerMiddleware` already logs every request with its duration, and uvicorn's
  access log is turned off. Don't add request logging in individual endpoints.

## Health checks

To check a new dependency, add a method to `HealthRepository` and an entry to the
`checks` dict in `HealthService.check()`. Raise `CheckFailed` for a failure message that
is safe to show a client; any other exception is reported by its type name only.
`/healthz` returns 503 if any check fails.

## Tests

- Tests run with `make test` inside the API container. They never touch the development
  database: the fixtures create `<POSTGRES_DB>_test` (migrated) and
  `<POSTGRES_DB>_test_bare` (empty) fresh for each run.
- Fixtures in `tests/conftest.py`:
  - `configure(**fields)` sets settings for one test through environment variables and
    rejects unknown field names.
  - `make_client()` starts an app built from the current settings. Call `configure()`
    before it, because apps read settings when they are created.
  - `migrated_db` and `bare_db` point settings at the two test databases.
- Patterns to copy:
  - Test a service with a mocked repository, `create_autospec(<Repository>, instance=True)`: `test_health_service.py`.
  - Test an endpoint with `make_client()`: `test_health.py`.
  - Test a dependency by mounting a temporary route: `test_db.py`.
- Cover failure paths as well as the happy path. Tests must not depend on order; the
  suite passes when run in reverse.

## Dependencies and images

- Put runtime dependencies in `[project].dependencies` in `pyproject.toml` and test tools
  in `[dependency-groups].dev`. Then run `make lock` and `make build`. Builds use
  `uv sync --locked`, so a stale `uv.lock` fails the build.
- The `Dockerfile` has two targets:
  - `dev` is what compose uses. Source is mounted and uvicorn reloads on changes.
  - `production` is the default and the image that ships: production dependencies only,
    running as a non-root user.
- Both start uvicorn with `sh -c "exec uvicorn ..."` on `$API_PORT` and `--no-access-log`.
  Keep the `exec`; without it uvicorn is not PID 1 and never receives SIGTERM.
- The arguments both targets share are in the `UVICORN_ARGS` build arg at the top of the
  `Dockerfile`. They include `--factory corpus.api.main:create_app`, so uvicorn builds the
  app when it starts. Don't add a module-level `app = create_app()`: every import of
  `main.py`, including the tests', would build one from the ambient environment.

## Before you finish

- `make test` passes.
- For changes to the API, run `make up`, call the endpoint on `localhost:9001`, and check
  `docker compose logs corpus-api`.
- Any new setting is in `Settings`, `.env.example`, `.env` and the README.
- The README is updated if commands, settings or the layout changed.
- PR descriptions follow `.github/pull_request_template.md`. In Claude Code, the
  `write-pr-description` skill fills it in from the branch.
