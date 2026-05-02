.PHONY: check fix fix_and_format format lint run_tests doctest

PYTHON ?= python

check: lint run_tests

fix_and_format: fix format

run_tests: doctest

lint:
	ruff check .
	basedpyright --project pyproject.toml --level error .

doctest:
	$(PYTHON) -m doctest README.md $(wildcard *.py)

fix:
	ruff check --fix .

format:
	ruff check --select I --fix .
	ruff format .
