PYTHON ?= python3
CLI := $(PYTHON) -m memory_tool
INGEST := $(PYTHON) -m memory_tool.ingest
VIEWER := $(PYTHON) -m memory_tool.viewer
PROFILE ?= codex

.PHONY: help init-codex init-claude init-shared stats clean-preview vacuum \
	search list viewer ingest-file ingest-stdin smoke-contract

help:
	@echo "Targets:"
	@echo "  init-codex      Initialize codex profile DB"
	@echo "  init-claude     Initialize claude profile DB"
	@echo "  init-shared     Initialize shared profile DB"
	@echo "  stats           Show admin manage stats (PROFILE=codex|claude|shared)"
	@echo "  clean-preview   Dry-run cleanup (DAYS=90, PROFILE=codex)"
	@echo "  vacuum          VACUUM DB for profile"
	@echo "  search          Memory search (Q='query', PROFILE=codex)"
	@echo "  list            List latest (LIMIT=20, PROFILE=codex)"
	@echo "  viewer          Start viewer (PROFILE=codex, PORT=37777)"
	@echo "  ingest-file     Ingest file (FILE=notes.txt, PROFILE=codex)"
	@echo "  ingest-stdin    Ingest from stdin: cat file | make ingest-stdin"
	@echo "  smoke-contract  Run the stable adapter smoke contract locally"

init-codex:
	$(CLI) --profile codex init

init-claude:
	$(CLI) --profile claude init

init-shared:
	$(CLI) --profile shared init

stats:
	$(CLI) --profile $(PROFILE) admin manage stats

clean-preview:
	$(CLI) --profile $(PROFILE) memory clean --older-than-days $${DAYS:-90} --dry-run

vacuum:
	$(CLI) --profile $(PROFILE) admin manage vacuum

search:
	$(CLI) --profile $(PROFILE) memory search "$${Q:-memory}" --limit $${LIMIT:-20}

list:
	$(CLI) --profile $(PROFILE) memory list --limit $${LIMIT:-20}

viewer:
	$(VIEWER) --profile $(PROFILE) --port $${PORT:-37777}

ingest-file:
	@if [ -z "$${FILE}" ]; then echo "FILE is required"; exit 1; fi
	$(INGEST) --profile $(PROFILE) --raw-file "$${FILE}" --auto-tags

ingest-stdin:
	$(INGEST) --profile $(PROFILE) --auto-tags

smoke-contract:
	$(PYTHON) scripts/verify_los_memory_adapter.py
