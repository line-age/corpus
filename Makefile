COMPOSE ?= docker compose
API     := corpus-api

.DEFAULT_GOAL := help
.PHONY: help setup build up down test db-migrate lock

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# Created from the example on first use; an existing .env is never overwritten.
.env:
	cp .env.example .env

setup: build up ## Build the images and start the stack

build: .env ## Build the Docker images
	$(COMPOSE) build

up: .env ## Start the stack in the background
	$(COMPOSE) up -d

down: ## Stop the stack (the database volume is kept)
	$(COMPOSE) down

test: .env ## Run the test suite in a container (pass pytest options with ARGS="...")
	$(COMPOSE) run --rm $(API) pytest $(ARGS)

db-migrate: .env ## Apply database migrations
	$(COMPOSE) run --rm $(API) alembic upgrade head

lock: .env ## Update uv.lock after changing dependencies in pyproject.toml
	$(COMPOSE) run --rm --no-deps $(API) uv lock
