.PHONY: help install api orchestrator controller frontend test lint fmt clean

UV ?= uv
PY ?= python3

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Install all Python workspace members
	$(UV) sync --all-packages

api:  ## Run the control-plane API on :8000
	$(UV) run --package aiw-api uvicorn aiw_api.main:app --reload --port 8000

orchestrator:  ## Run the orchestrator worker loop
	$(UV) run --package aiw-orchestrator python -m aiw_orchestrator.main

controller:  ## Run the workspace controller CLI (provision a workspace)
	$(UV) run --package aiw-workspace-controller python -m aiw_workspace.cli --help

frontend:  ## Run the Vite dev server
	cd frontend && npm install && npm run dev

test:  ## Run the test suite
	$(UV) run pytest -q

lint:  ## Ruff check
	$(UV) run ruff check .

fmt:  ## Ruff format
	$(UV) run ruff format .

clean:  ## Remove caches and local DB
	rm -rf .pytest_cache .ruff_cache **/__pycache__ aiw.db
