.DEFAULT_GOAL := help

.PHONY: help test build verify lint typecheck

help: ## Print available targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

test: ## Run unit + lint (no container needed)
	uv run pytest tests/ --ignore=tests/fixtures --ignore=tests/test_compat_realistic.py -q

build: ## Build hermes-station:local and hermes-station:test images
	$(if $(shell command -v container 2>/dev/null),container,docker) build -t hermes-station:local .
	$(if $(shell command -v container 2>/dev/null),container,docker) build --target test -t hermes-station:test .

verify: ## Run scripts/dx-verify.sh (full lint + build + health + in-container suite)
	bash scripts/dx-verify.sh

lint: ## Lint and check formatting with ruff
	uv run ruff check . && uv run ruff format --check .

typecheck: ## Run mypy against hermes_station/
	uv run mypy hermes_station/
