.PHONY: help install init run-bot test clear-archive

help:
	@echo "Available targets:"
	@echo "  install   Install dependencies (pip -e .)"
	@echo "  init      Initialize archive storage (squire init)"
	@echo "  run-bot   Run the Discord bot"
	@echo "  test      Placeholder for tests"
	@echo "  clear-archive  Delete archive contents (uses archive_root from config.yaml)"

install:
	pip install -e .

init:
	python -m squire_core.cli_init

run-bot:
	python -m squire_core.discord_bot

test:
	@echo "No tests configured yet."

clear-archive:
	@archive_root=$$(grep -E '^archive_root:' config.yaml | head -n1 | sed 's/^[^:]*:[[:space:]]*//; s/^"//; s/"$$//; s/^[\\x27]//; s/[\\x27]$$//'); \
	if [ "$$archive_root" != "" ] && [ "$$archive_root" != "$${archive_root#\~\\/}" ]; then \
		archive_root="$$HOME/$${archive_root#\\~/}"; \
	fi; \
	if [ -z "$$archive_root" ]; then \
		echo "archive_root not set in config.yaml"; \
		exit 1; \
	fi; \
	read -r -p "Type DELETE to clear all data from the storage archive. This will reset all system state. " ans; \
	if [ "$$ans" = "DELETE" ]; then \
		find "$$archive_root" -mindepth 1 -maxdepth 1 ! -name ".git" -exec rm -rf {} +; \
		echo "Cleared $$archive_root (preserved .git if present)"; \
	else \
		echo "Aborted."; \
	fi
