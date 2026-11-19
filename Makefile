PYTHON ?= python3

.PHONY: bootstrap check validate test go-check

bootstrap:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install --disable-pip-version-check -r requirements-dev.txt

check: validate test

validate:
	$(PYTHON) scripts/validate_repo.py

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

go-check:
	gofmt -w $(find cmd internal -name '*.go' -type f)
	go mod tidy
	go test ./...
