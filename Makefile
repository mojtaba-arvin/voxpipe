.PHONY: check validate

check: validate

validate:
	python3 scripts/validate_repo.py
