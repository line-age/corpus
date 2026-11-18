# corpus chart

Deploys the retrieval API and, optionally, ingest as scheduled jobs. By default it also creates
two one-instance CloudNativePG clusters, so the CNPG operator must be installed:

- `<release>-api-db`: the API's database, on CNPG's standard image, which carries pgvector.
- `<release>-ingest-db`: ingest's database, on ParadeDB, which carries `pg_search` for the BM25
  index.

This chart carries generic defaults only. The deployment supplies everything specific to an
environment: `image.tag`, storage classes and sizes, the ingress host, ingest schedules and any
credentials.

```sh
helm template corpus deploy/chart --set image.tag=sha-0123abc
```

| Value | Meaning |
|---|---|
| `image.tag` | Required. One tag for the api and ingest images, e.g. `sha-<short>` from CI. |
| `api.env` | Extra settings, as name: value. See `.env.example`. The database settings come from the database secret. |
| `migrations.enabled` | `alembic upgrade head` on both databases before the API starts. |
| `ingest.jobs` | One CronJob per entry: `schedule`, `suspend`, `args` for `corpus-ingest`. None by default. |
| `ingest.rawStore` | Empty for `file:///raw` on the `rawStorage` volume, or `s3://bucket[/prefix]`. |
| `rawStorage.*` | The raw volume, ReadWriteMany. `existingClaim` to bring your own. |
| `database.<api\|ingest>.existingSecret` | Use an external database instead of a CNPG cluster. The secret needs the keys of a CNPG app secret: `host`, `port`, `dbname`, `username`, `password`, `uri`. |
| `database.sslmode` | Passed to libpq as `PGSSLMODE`. `require` by default. |

The API has **no authentication**. Anyone who can reach it can query it, so expose it only
where that is acceptable.

## How it fits together

- **Migrations** run in an init container of the API pod, against the API's database and then
  ingest's, from the one history in `migrations/`. While a database is not ready the pod stays
  in `Init` and retries. With more than one API replica, the first pods may race; the loser
  fails, restarts and finds the schema at head.
- **Extensions** are created when each cluster is initialised, by the superuser: `vector` in
  both, `pg_search` in ingest's, which also preloads it and puts `paradedb` on the search path.
  The migrations and the index stage then find them already there.
- **Ingest** jobs build `CORPUS_DATABASE_URL` from the secret's `uri` when they start, so the
  password never appears in a spec. A suspended job is a template. Run it by hand with
  `kubectl create job --from=cronjob/<release>-ingest-<name> <name>-1`. Stages are resumable,
  so a job that fails can be run again.
- **Probes**: readiness is `/healthz`, which answers 503 while the database is down; liveness
  is a TCP check, because restarting the API cannot fix the database.

Both database clusters and the raw volume are annotated so that neither Helm uninstall nor
Argo CD pruning or deletion removes them. Remove them by hand when you mean to.
