.PHONY: help install init run-bot test clear-archive \
	harness-bootstrap harness-up harness-run harness-inspect harness-validate harness-down harness \
	verify-session gate o11y-up o11y-down

PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

help:
	@echo "Available targets:"
	@echo "  install   Install dependencies (pip -e .)"
	@echo "  init      Initialize archive storage (squire init)"
	@echo "  run-bot   Run the Discord bot (optional: env=dev|test|prod log_level=DEBUG|INFO|WARNING|ERROR)"
	@echo "  test      Run test suite"
	@echo "  clear-archive  Delete archive contents (uses archive_root from config.yaml)"
	@echo "  harness-bootstrap  Create a new run directory and run.env"
	@echo "  harness-up         Start local Alloy/Loki/Tempo/Prometheus stack"
	@echo "  harness-run        Run deterministic harness checks"
	@echo "  harness-inspect    Query local observability APIs into run artifacts"
	@echo "  harness-validate   Evaluate assertions and write assertions.json"
	@echo "  harness-down       Stop local observability stack and remove volumes"
	@echo "  harness            Full lifecycle: bootstrap/up/run/inspect/validate/down"
	@echo "  verify-session     Required behavior-change gate with local attestation artifact"
	@echo "  gate               Alias of verify-session"
	@echo "  o11y-up            Alias of harness-up"
	@echo "  o11y-down          Alias of harness-down"

install:
	pip install -e .

init:
	$(PYTHON) -m squire_core.cli_init

run-bot:
	SQUIRE_ENV=$${SQUIRE_ENV:-$(if $(env),$(env),dev)} SQUIRE_LOG_LEVEL=$${SQUIRE_LOG_LEVEL:-$(if $(log_level),$(log_level),)} $(PYTHON) -m squire_core.discord_bot

test:
	$(PYTHON) -m pytest -q

harness-bootstrap:
	SQUIRE_RUN_ID=$${SQUIRE_RUN_ID:-$(RUN_ID)} SQUIRE_HARNESS_MODE=$${SQUIRE_HARNESS_MODE:-$(if $(mode),$(mode),deterministic)} SQUIRE_ENV=$${SQUIRE_ENV:-$(if $(env),$(env),dev)} SQUIRE_HARNESS_NOW=$${SQUIRE_HARNESS_NOW:-$(if $(fixed_now),$(fixed_now),2026-02-15T12:00:00+00:00)} $(PYTHON) tools/harness/run_harness.py bootstrap

harness-up:
	SQUIRE_RUN_ID=$${SQUIRE_RUN_ID:-$(RUN_ID)} $(PYTHON) tools/harness/run_harness.py up

harness-run:
	SQUIRE_RUN_ID=$${SQUIRE_RUN_ID:-$(RUN_ID)} SQUIRE_HARNESS_MODE=$${SQUIRE_HARNESS_MODE:-$(if $(mode),$(mode),deterministic)} $(PYTHON) tools/harness/run_harness.py run

harness-inspect:
	SQUIRE_RUN_ID=$${SQUIRE_RUN_ID:-$(RUN_ID)} $(PYTHON) tools/harness/run_harness.py inspect

harness-validate:
	SQUIRE_RUN_ID=$${SQUIRE_RUN_ID:-$(RUN_ID)} SQUIRE_HARNESS_MODE=$${SQUIRE_HARNESS_MODE:-$(if $(mode),$(mode),deterministic)} $(PYTHON) tools/harness/run_harness.py validate

harness-down:
	$(PYTHON) tools/harness/run_harness.py down

harness:
	SQUIRE_RUN_ID=$${SQUIRE_RUN_ID:-$(RUN_ID)} SQUIRE_HARNESS_MODE=$${SQUIRE_HARNESS_MODE:-$(if $(mode),$(mode),deterministic)} SQUIRE_ENV=$${SQUIRE_ENV:-$(if $(env),$(env),dev)} SQUIRE_HARNESS_NOW=$${SQUIRE_HARNESS_NOW:-$(if $(fixed_now),$(fixed_now),2026-02-15T12:00:00+00:00)} $(PYTHON) tools/harness/run_harness.py harness

verify-session:
	SQUIRE_RUN_ID=$${SQUIRE_RUN_ID:-$(RUN_ID)} SQUIRE_ENV=$${SQUIRE_ENV:-$(if $(env),$(env),dev)} SQUIRE_HARNESS_NOW=$${SQUIRE_HARNESS_NOW:-$(if $(fixed_now),$(fixed_now),2026-02-15T12:00:00+00:00)} $(PYTHON) tools/harness/run_harness.py verify-session

gate: verify-session

o11y-up: harness-up

o11y-down: harness-down

clear-archive:
	@archive_root=$$(grep -E '^archive_root:' config.yaml | head -n1 | sed 's/^[^:]*:[[:space:]]*//; s/[[:space:]]*#.*$$//; s/^"//; s/"$$//; s/^[\\x27]//; s/[\\x27]$$//'); \
	case "$$archive_root" in \
		"~") archive_root="$$HOME" ;; \
		"~/"*) archive_root="$$HOME/$${archive_root#~/}" ;; \
	esac; \
	archive_root=$$(printf '%s' "$$archive_root" | sed 's#/~/#/#'); \
	if [ -z "$$archive_root" ]; then \
		echo "archive_root not set in config.yaml"; \
		exit 1; \
	fi; \
	if [ ! -d "$$archive_root" ]; then \
		echo "archive_root does not exist: $$archive_root"; \
		exit 1; \
	fi; \
	echo "This action will clear all data from the storage archive and reset all system state."; \
	tries=0; \
	confirmed=0; \
	while [ $$tries -lt 3 ]; do \
		read -r -p "Type \"DELETE\" to confirm: " ans; \
		if [ "$$ans" = "DELETE" ]; then \
			confirmed=1; \
			break; \
		fi; \
		tries=$$((tries + 1)); \
	done; \
	if [ $$confirmed -eq 1 ]; then \
		find "$$archive_root" -mindepth 1 -maxdepth 1 ! -name ".git" -exec rm -rf {} +; \
		echo "Cleared $$archive_root (preserved .git if present)"; \
	else \
		echo "Aborted. Confirmation failed."; \
	fi
