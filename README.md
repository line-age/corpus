# corpus

Lineage trade-press ingest and authority-ranked RAG search.

Part of [Lineage](https://github.com/line-age/controlplane). What this component takes in,
what it puts out, and what it depends on are described in the controlplane's
[components.md](https://github.com/line-age/controlplane/blob/main/components.md#corpus);
this README will say how it is built once it is.

Work is tracked in the [Corpus](https://github.com/orgs/line-age/projects/2) project. The
team lead is [@FlorenceEgwu](https://github.com/FlorenceEgwu). If that changes, the
[controlplane's table](https://github.com/line-age/controlplane#projects-and-leads) is updated first.

## Setup
- Docker (https://docker.com)
- Make

## Running it

```sh
make setup        # build the images; start the API, its Postgres (pgvector) and the ingest database
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

## Ingest

Ingest is being built; search is not started. The plan is [docs/ingestion-plan.md](docs/ingestion-plan.md)
and the design is [docs/architecture.md](docs/architecture.md).

```text
src/corpus/             the API
packages/corpus_core/   index format: schema and migrations, collections, lanes, hOCR parsing,
                        normalisation, passages, Internet Archive client, raw store
apps/ingest/            the corpus-ingest CLI: one command per stage
tests/                  API and ingest tests; the ingest end-to-end test needs Postgres
fixtures/               identifier lists for real runs
```

Ingest has its own database, `corpus-ingest-db` (ParadeDB, for `pg_search`), which `make up`
starts alongside the API's. Both share one migration history in `migrations/`, and
`make db-migrate` applies it to both. Ingest needs outbound HTTPS to `archive.org` and
`*.us.archive.org`. Run it in compose (raw files go to the `corpus-raw-data` volume):

```sh
make setup && make db-migrate
docker compose run --rm corpus-ingest run -c pub_pipeline-gas-journal --ids fixtures/pgj-sample-15.txt
docker compose run --rm corpus-ingest status
```

or on the host with Python 3.13 and [uv](https://docs.astral.sh/uv/). `CORPUS_DATABASE_URL` in
`.env` already points at `corpus-ingest-db` on `localhost:5433`:

```sh
uv sync
uv run corpus-ingest run -c pub_pipeline-gas-journal --ids fixtures/pgj-sample-15.txt
uv run corpus-ingest export --sample 20 -o review.csv
```

Each stage is also its own command (`enumerate`, `classify`, `lane`, `mirror`, `pages`,
`normalise`, `passages`, `index`). Every one is safe to re-run: items already done are skipped,
failed items are retried up to three times, and finishing a stage for an item resets the stages
after it. `--force` redoes items regardless. Set `CORPUS_TEST_DATABASE_URL` to run the ingest
end-to-end test; without it, that test is skipped.

## Service Status Endpoints

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

