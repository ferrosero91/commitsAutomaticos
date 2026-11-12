.PHONY: help test lint run clean

help:
	@echo "make test  - ejecuta la suite de pruebas (unittest)"
	@echo "make lint  - analiza el código con ruff"
	@echo "make run   - abre el flujo interactivo"
	@echo "make clean - elimina artefactos de Python"

test:
	python3 -m unittest discover -s tests -v

lint:
	ruff check .
	ruff format --check .

run:
	python3 -m autocommit

clean:
	rm -rf __pycache__ autocommit/__pycache__ tests/__pycache__
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
