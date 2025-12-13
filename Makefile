# Makefile

POETRY=poetry

.PHONY: install run test fmt format lint

install:
	$(POETRY) install

run:
	$(POETRY) run uvicorn main:app --reload


test:
	$(POETRY) run pytest

fmt:
	$(POETRY) run isort .
	$(POETRY) run black .

# alias
format: fmt
