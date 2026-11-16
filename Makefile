# Targets adapted from the engine's Makefile (MITSustainableDesignLab/globi), so the run stack
# works from this repo alone. Image publishing (push-worker, docker-login) stays in the engine.

AWS_ENV ?= local.host
HATCHET_ENV ?= local.host
# Worker image = the engine commit uv.lock pins
export GLOBI_REF := $(shell uv run --quiet python -m globi_toolkit.engine_ref 2>/dev/null)

COMPOSE := docker compose -f docker-compose.yml -f docker-compose.hatchet.yml -f docker-compose.aws.yml

##################### Installation #####################
.PHONY: install
install: ## Install the environment and generate the component-library client
	@uv sync
	@uv run epi prisma generate

##################### Code Quality and Testing #####################
.PHONY: check
check: ## Lint, format check and type check
	@uv lock --locked
	@uv run ruff check .
	@uv run ruff format --check .
	@uv run pyright

.PHONY: test
test: ## Run the tests
	@uv run pytest

.PHONY: test-ui
test-ui: ## Run sbem-ui's tests
	@$(MAKE) -C apps/sbem-ui test

##################### Docker Execution #####################
.PHONY: copy-env
copy-env: ## Copy `.env.example` to `.env` if `.env` does not exist
	@bash scripts/copy_envexample_to_env.sh

.PHONY: hatchet-lite
hatchet-lite: ## Run hatchet lite
	@make copy-env
	@docker compose -f docker-compose.yml -f docker-compose.hatchet.yml up hatchet-lite -d

.PHONY: hatchet-token
hatchet-token: ## Get the hatchet token and write it to .env.local.hatchet and .env.local.host.hatchet
	@make copy-env
	@bash scripts/copy_hatchet_token.sh
	@make hatchet-lite
	@MSYS_NO_PATHCONV=1 docker compose -f docker-compose.yml -f docker-compose.hatchet.yml exec -T hatchet-lite /hatchet-admin token create --config /config --tenant-id 707d0855-80ab-4e1f-a156-f1c4546cbf52 | bash scripts/write_hatchet_token.sh

.PHONY: build-engine
build-engine: ## Build the worker image from the engine commit in uv.lock
	@make copy-env
	@$(COMPOSE) build simulations

.PHONY: engine
engine: ## Run the engine: hatchet, local S3 and workers
	@make copy-env
	@$(COMPOSE) build simulations
	@$(COMPOSE) up -d

.PHONY: cli
cli: ## Run the engine CLI inside a worker container
	@make copy-env
	@$(COMPOSE) run --rm simulations uv run globi $(filter-out $@,$(MAKECMDGOALS))

.PHONY: interactive
interactive: ## Open a shell in the fanouts worker
	@make copy-env
	@$(COMPOSE) exec -it fanouts /bin/bash

.PHONY: viz
viz: ## Run the results dashboard in Docker
	@make copy-env
	@$(COMPOSE) -f docker-compose.st.yml up visualizer --build -d

.PHONY: ps
ps: ## List the containers of the local stack
	@$(COMPOSE) ps

.PHONY: logs
logs: ## Follow a service's logs (SERVICE=simulations by default)
	@$(COMPOSE) logs -f $(or $(SERVICE),simulations)

.PHONY: down
down: ## Stop the docker containers
	@make copy-env
	@$(COMPOSE) -f docker-compose.st.yml down

.PHONY: reset
reset: ## Stop the containers and delete all local runs and the hatchet token
	@$(COMPOSE) -f docker-compose.st.yml down -v

##################### Native Execution #####################
.PHONY: simulations-native
simulations-native: ## Run a simulations worker on this machine (needs EnergyPlus installed)
	@uv run --env-file .env.$(AWS_ENV).aws --env-file .env.$(HATCHET_ENV).hatchet --env-file .env.scythe.storage --env-file .env.scythe.simulations worker

.PHONY: fanouts-native
fanouts-native: ## Run a fanouts worker on this machine
	@uv run --env-file .env.$(AWS_ENV).aws --env-file .env.$(HATCHET_ENV).hatchet --env-file .env.scythe.storage --env-file .env.scythe.fanouts worker

.PHONY: viz-native
viz-native: ## Run the results dashboard on this machine
	@uv run --env-file .env.$(AWS_ENV).aws --env-file .env.scythe.storage globi-toolkit visualize

.PHONY: results-native
results-native: ## Download or export run results, e.g. make results-native fetch -- "Demo/Baseline"
	@uv run --env-file .env.$(AWS_ENV).aws --env-file .env.scythe.storage globi-toolkit results $(filter-out $@,$(MAKECMDGOALS))

.PHONY: cli-native
cli-native: ## Run the engine CLI on this machine against the local stack
	@uv run \
	--env-file .env.$(AWS_ENV).aws \
	--env-file .env.$(HATCHET_ENV).hatchet \
	--env-file .env.scythe.fanouts \
	--env-file .env.scythe.storage \
	globi $(filter-out $@,$(MAKECMDGOALS))

##################### Authoring app (sbem-ui) #####################
.PHONY: ui-setup
ui-setup: ## Install sbem-ui's frontend (needs Node 18+; Python deps come with `make install`)
	@cd apps/sbem-ui/web && npm install

.PHONY: ui
ui: ## Run sbem-ui: backend on :8000, frontend on http://localhost:5173
	@$(MAKE) -C apps/sbem-ui dev

##################### Documentation #####################
.PHONY: docs
docs: ## Build and serve the documentation
	@uv run --group docs mkdocs serve

.PHONY: docs-test
docs-test: ## Build the documentation, failing on warnings
	@uv run --group docs mkdocs build -s

##################### MISC #####################
.PHONY: engine-ref
engine-ref: ## Print the engine commit the worker image is built from
	@echo $(GLOBI_REF)

.PHONY: help
help:
	@uv run python -c "import re; \
	[[print(f'\033[36m{m[0]:<20}\033[0m {m[1]}') for m in re.findall(r'^([a-zA-Z_-]+):.*?## (.*)$$', open(makefile).read(), re.M)] for makefile in ('$(MAKEFILE_LIST)').strip().split()]"

.DEFAULT_GOAL := help

%:
	@:
