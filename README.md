# corpus

Lineage trade-press ingest and authority-ranked RAG search.

Part of [Lineage](https://github.com/line-age/controlplane). What this component takes in,
what it puts out, and what it depends on are described in the controlplane's
[components.md](https://github.com/line-age/controlplane/blob/main/components.md#corpus);
this README will say how it is built once it is.

Work is tracked in the [Corpus](https://github.com/orgs/line-age/projects/2) project. The
team lead is [@FlorenceEgwu](https://github.com/FlorenceEgwu). If that changes, the
[controlplane's table](https://github.com/line-age/controlplane#projects-and-leads) is updated first.

## Architecture

![Corpus architecture: publications from Archive.org flow through ingest, the raw data lake and ETL into a searchable data warehouse, served by a retrieval API to Cite, Legend and other apps](docs/corpus_sys_diagram.png)

The source is [docs/corpus_sys_diagram.drawio](docs/corpus_sys_diagram.drawio); edit it in
[draw.io](https://app.diagrams.net) and re-export the PNG when the design changes.

## Setup
- Docker (https://docker.com)
- Make

## Running it

```sh
make setup        # build the images; start the API and its database (ParadeDB)
make db-migrate   # apply migrations, including pg_search and the BM25 search index
curl localhost:9001/healthz
make corpus-ingest ARGS="run -c pub_pipeline-gas-journal --ids /fixtures/pgj-sample-15.txt"
curl 'localhost:9001/api/v1/search?q=natural%20gas'
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
| `make corpus-ingest ARGS="<command> ..."` | Run the `apps/ingest` pipeline CLI in compose, rebuilding its image first; no `ARGS` shows its help |

Tests use their own databases (`<POSTGRES_DB>_test` and `<POSTGRES_DB>_test_bare`), recreated
on every run, so they never touch development data.

## Ingestion Pipelikne

The Ingestion pipeline is being built. The plan is [docs/ingestion-plan.md](docs/ingestion-plan.md).

Only issues dated 1920-1961 are mirrored and indexed; the rest are listed but stop after the
lane stage. The plan explains the window.

```text
src/corpus/             the API
packages/corpus_core/   index format: schema and migrations, collections, lanes, hOCR parsing,
                        normalisation, passages, Internet Archive client, raw store
apps/ingest/            the corpus-ingest CLI: one command per stage
tests/                  API and ingest tests; the ingest end-to-end test needs ParadeDB
fixtures/               identifier lists for real runs
```

Ingest and the API share one database, `corpus-ingest-db` (ParadeDB, for `pg_search`): ingest
writes it and search reads it. Its schema is in `packages/corpus_core` and its migrations in
`migrations/`; `make db-migrate` applies them. Ingest needs outbound HTTPS to `archive.org` and
`*.us.archive.org`. Run it in compose with `make corpus-ingest`, which rebuilds the ingest
image so it has your latest changes and mounts `fixtures/` at `/fixtures` (raw files go to the
`corpus-raw-data` volume):

```sh
make setup && make db-migrate
make corpus-ingest ARGS="run -c pub_pipeline-gas-journal --ids /fixtures/pgj-sample-15.txt"
make corpus-ingest ARGS="status"
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

## Search

`GET /api/v1/search?q=<words>` returns the passages of OCR text that contain a word or
phrase, such as a place name, most relevant first, with a citation for each: the document,
the page (printed number, scan position and a link to that page of the scan) and the
statement that contains the phrase.

- `q` is required, 2 to 200 characters. `limit` (1 to 100, default 20) and `offset`
  (default 0) page through the results; `total` counts every matching passage.
- Search runs in the database on [pg_search](https://docs.paradedb.com). The `passage_bm25`
  index matches `q` as a phrase: the words in order, ignoring case and the punctuation
  between them, and whole words only, so `Martin` does not match `Martinez`. Results are
  ranked by each passage's BM25 `score`, highest first: passages that use the words more
  often, passages whose words are rarer across the corpus, and shorter passages come first.
- One result per passage. A passage is a window of about 250 words of a page (see
  [docs/ingestion-plan.md](docs/ingestion-plan.md)), and passages overlap by 50 words.
- `snippet` is the statement around the first match in the passage, from its normalised
  text (whitespace collapsed, words hyphenated across a line break rejoined).
  `match_start` and `match_end` locate the match within `snippet`, for highlighting.

| Setting | Default | Meaning |
| --- | --- | --- |
| `ARCHIVE_ORG_BASE_URL` | `https://archive.org` | The base of the document and page links in results |

```json
{
  "query": "natural gas", "total": 12, "limit": 20, "offset": 0,
  "results": [{
    "document": {"identifier": "sim_pipeline-gas-journal_1926-10-30_125_22", "title": "American Gas Journal 1926-10-30: Vol 125 Iss 22", "publication_date": "1926-10-30", "volume": "125", "issue": "22", "source_url": "https://archive.org/details/sim_pipeline-gas-journal_1926-10-30_125_22"},
    "page": {"leaf_number": 19, "page_number": "550", "url": "https://archive.org/details/sim_pipeline-gas-journal_1926-10-30_125_22/page/n19"},
    "snippet": "The plant that is employed for the removal of gasoline from natural gas by absorption in charcoal consists of a number of absorption cylinders of horizontal type, which contain the activated charcoal and through it the casing-head gas is led.",
    "match_start": 60, "match_end": 71, "score": 4.5199
  }]
}
```

Known limits: a phrase that falls in the overlap between two passages is returned once for
each; abbreviations such as "Co." end a snippet early; and there is no stemming, so `well`
does not match `wells`.

## Service Status Endpoints

- `GET /` names the service and its version, and links the docs when they are enabled.
- `GET /healthz` reports each dependency (the database, and pg_search with the search index
  in it).
  It answers 200 when all are healthy and 503 when any is not, so it works as a readiness
  probe. Failure details are kept short; the full error goes to the logs.

## Logs

The API writes JSON lines to stdout, one object per record, including uvicorn's own
startup and shutdown messages. Every line has `time` (UTC), `level`, `logger` and
`message`; request lines add `method`, `path`, `status` and `duration_ms`:

```json
{"time": "2026-11-12T15:23:42.861Z", "level": "INFO", "logger": "corpus", "message": "GET /healthz 200 16.5ms", "method": "GET", "path": "/healthz", "status": 200, "duration_ms": 16.5}
```

