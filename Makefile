.PHONY: help install init run-bot test clear-archive

help:
	@echo "Available targets:"
	@echo "  install   Install dependencies (pip -e .)"
	@echo "  init      Initialize archive storage (squire init)"
	@echo "  run-bot   Run the Discord bot"
	@echo "  test      Run test suite"
	@echo "  clear-archive  Delete archive contents (uses archive_root from config.yaml)"

install:
	pip install -e .

init:
	python -m squire_core.cli_init

run-bot:
	python -m squire_core.discord_bot

test:
	python3 -m pytest -q

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
