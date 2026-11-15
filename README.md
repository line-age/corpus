# corpus

Lineage trade-press ingest and authority-ranked RAG search.

Part of [Lineage](https://github.com/line-age/controlplane). What this component takes in,
what it puts out, and what it depends on are described in the controlplane's
[components.md](https://github.com/line-age/controlplane/blob/main/components.md#corpus);
this README will say how it is built once it is.

Work is tracked in the [Corpus](https://github.com/orgs/line-age/projects/2) project. The
team lead is [@FlorenceEgwu](https://github.com/FlorenceEgwu). If that changes, the
[controlplane's table](https://github.com/line-age/controlplane#projects-and-leads) is updated first.

## Status

Ingest is being built; search is not started. The plan is [docs/ingestion-plan.md](docs/ingestion-plan.md)
and the design is [docs/architecture.md](docs/architecture.md).

## Layout

```text
packages/corpus_core/   index format: schema and migrations, collections, lanes, hOCR parsing,
                        normalisation, passages, Internet Archive client, raw store
apps/ingest/            the corpus-ingest CLI: one command per stage
tests/                  unit tests, plus an end-to-end test that needs Postgres
fixtures/               identifier lists for real runs
```

## Running ingest locally

Needs Python 3.12, [uv](https://docs.astral.sh/uv/), Docker, and outbound HTTPS to
`archive.org` and `*.us.archive.org`.

```sh
uv sync
cp .env.example .env
docker compose up -d postgres          # Postgres with pg_search
uv run corpus-ingest migrate
uv run corpus-ingest run -c pub_pipeline-gas-journal --ids fixtures/pgj-sample-15.txt
uv run corpus-ingest status
uv run corpus-ingest export --sample 20 -o review.csv
```

Each stage is also its own command (`enumerate`, `classify`, `lane`, `mirror`, `pages`,
`normalise`, `passages`, `index`). Every one is safe to re-run: items already done are skipped,
failed items are retried up to three times, and finishing a stage for an item resets the stages
after it. `--force` redoes items regardless.

## Tests

```sh
uv run pytest                                   # unit tests
CORPUS_TEST_DATABASE_URL=postgresql+psycopg://corpus:corpus@localhost:5432/corpus_test \
  uv run pytest                                 # plus the end-to-end test
```

