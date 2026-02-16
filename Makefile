.PHONY: install install-dev format lint security test coverage ci clean

PYTHON := python3
VENV := venv
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
BLACK := $(VENV)/bin/black
PYLINT := $(VENV)/bin/pylint
BANDIT := $(VENV)/bin/bandit

SRC_DIRS := src/ tools/ data_collector.py

## Install production dependencies
install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -r requirements.txt

## Install all dependencies including dev tools
install-dev:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -r requirements-dev.txt

## Format code with black
format:
	$(BLACK) $(SRC_DIRS) tests/

## Check formatting without modifying files
format-check:
	$(BLACK) --check $(SRC_DIRS) tests/

## Run pylint
lint:
	$(PYLINT) $(SRC_DIRS)

## Run bandit security checks
security:
	$(BANDIT) -r $(SRC_DIRS) -c pyproject.toml

## Run tests
test:
	$(PYTEST) tests/

## Run tests with coverage report
coverage:
	$(PYTEST) tests/ --cov=src --cov-report=term-missing --cov-report=html

## Run all CI checks (format, lint, security, test)
ci: format-check lint security test

## Remove generated files
clean:
	rm -rf .pytest_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
