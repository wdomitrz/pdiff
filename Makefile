.PHONY: check fix fix_and_format format lint run_tests doctest smoke_tests

PYTHON ?= python3

check: lint run_tests

fix_and_format: fix format

run_tests: doctest smoke_tests

lint:
	ruff check .
	basedpyright --project pyproject.toml --level error .

doctest:
	$(PYTHON) -m doctest README.md $(wildcard *.py)

smoke_tests:
	$(PYTHON) test_data/smoke_tests.py

fix:
	ruff check --fix .

format:
	ruff check --select I --fix .
	ruff format .
