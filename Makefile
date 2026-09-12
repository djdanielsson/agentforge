.PHONY: help install api orchestrator cli workspaces web test lint fmt clean

UV ?= uv
PY ?= python3

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Install all Python workspace members
	$(UV) sync --all-packages

api:  ## Run the control-plane API on :8000
	$(UV) run --package agentforge-api uvicorn agentforge_api.main:app --reload --port 8000

orchestrator:  ## Run the orchestrator worker loop
	$(UV) run --package agentforge-orchestrator python -m agentforge_orchestrator.main

cli:  ## Run the CLI (pass ARGS="fleet")
	$(UV) run --package agentforge-cli agentforge $(ARGS)

workspaces:  ## Workspace provider CLI (provision/status/exec/teardown)
	$(UV) run --package agentforge-workspaces python -m agentforge_workspaces.cli --help

web:  ## Run the Vite dev server
	cd apps/web && npm install && npm run dev

test:  ## Run the test suite
	$(UV) run pytest -q

lint:  ## Ruff check
	$(UV) run ruff check .

fmt:  ## Ruff format
	$(UV) run ruff format .

clean:  ## Remove caches and local DB
	rm -rf .pytest_cache .ruff_cache **/__pycache__ agentforge.db
