PYTHON ?= python3

.PHONY: bootstrap check validate test go-check mcp-docs-check gold-check

bootstrap:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install --disable-pip-version-check -r requirements-dev.txt

check: validate mcp-docs-check gold-check test

validate:
	$(PYTHON) scripts/validate_repo.py

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

mcp-docs-check:
	PYTHONPATH=src $(PYTHON) scripts/generate_mcp_docs.py --check

go-check:
	go mod tidy -diff
	@test -z "$(gofmt -l ./cmd ./internal)"
	go vet ./...
	go test -race ./...

gold-check:
	$(PYTHON) scripts/evaluate_persian_gold.py validate
