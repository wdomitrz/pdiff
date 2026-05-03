.PHONY: all lint fix test

all: fix lint test

lint:
	ruff check .
	basedpyright --project pyproject.toml --level error .

fix:
	ruff check --fix .
	ruff check --select I --fix .
	ruff format .

test:
	python3 -m doctest README.md $(wildcard *.py)
	python3 test_data/test.py
