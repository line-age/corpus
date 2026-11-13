COMPOSE ?= docker compose
API     := corpus-api

.DEFAULT_GOAL := help
.PHONY: help setup build up down test db-migrate lock

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
