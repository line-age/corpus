COMPOSE ?= docker compose
API     := corpus-api
INGEST  := corpus-ingest

.DEFAULT_GOAL := help
.PHONY: help setup build up down test db-migrate lock corpus-ingest

# Update this list when a target is added, renamed or removed.
help:
	@echo 'help - Show this help'
	@echo 'setup - Build the images and start the stack'
	@echo 'build - Build the Docker images'
	@echo 'up - Start the stack in the background'
	@echo 'down - Stop the stack (the database volume is kept)'
	@echo 'test - Run the test suite in a container (pass pytest options with ARGS="...")'
	@echo 'db-migrate - Apply database migrations'
	@echo 'lock - Update uv.lock after changing dependencies in pyproject.toml'
	@echo 'corpus-ingest - Run the apps/ingest pipeline CLI (ARGS="run -c <collection> ...", ARGS="status"; no ARGS shows its help)'

# Created from the example on first use; an existing .env is never overwritten.
.env:
	cp .env.example .env

setup: build up

build: .env
	$(COMPOSE) build

up: .env
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

test: build
	$(COMPOSE) run --rm $(API) pytest $(ARGS)

db-migrate: build
	$(COMPOSE) run --rm $(API) alembic upgrade head

lock: build
	$(COMPOSE) run --rm --no-deps $(API) uv lock

# The apps/ingest pipeline. Its service is in a compose profile, so `build` skips it, and its
# image installs the code rather than mounting it, so it is rebuilt (from cache) on every run
# to pick up changes. fixtures/ is mounted read-only, for ARGS="... --ids /fixtures/<file>".
# Run `make db-migrate` first on a fresh ingest database.
corpus-ingest: .env
	$(COMPOSE) build $(INGEST)
	$(COMPOSE) run --rm -v "$(CURDIR)/fixtures:/fixtures:ro" $(INGEST) $(ARGS)
