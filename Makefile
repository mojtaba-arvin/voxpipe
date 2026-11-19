PYTHON ?= python3

.PHONY: bootstrap check validate test

bootstrap:
	$(PYTHON) -m venv .venv

check: validate test

validate:
	$(PYTHON) scripts/validate_repo.py

test:
	$(PYTHON) -m unittest discover -s tests -v
