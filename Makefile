PYTHON ?= python3
TOOL := memory_tool/memory_tool.py
INGEST := memory_tool/ingest.py
VIEWER := memory_tool/viewer.py
PROFILE ?= codex

.PHONY: help init-codex init-claude init-shared stats clean-preview vacuum \
	search list viewer ingest-file ingest-stdin

help:
	@echo "Targets:"
	@echo "  init-codex      Initialize codex profile DB"
	@echo "  init-claude     Initialize claude profile DB"
	@echo "  init-shared     Initialize shared profile DB"
	@echo "  stats           Show manage stats (PROFILE=codex|claude|shared)"
	@echo "  clean-preview   Dry-run cleanup (DAYS=90, PROFILE=codex)"
	@echo "  vacuum          VACUUM DB for profile"
	@echo "  search          Search (Q='query', PROFILE=codex)"
	@echo "  list            List latest (LIMIT=20, PROFILE=codex)"
	@echo "  viewer          Start viewer (PROFILE=codex, PORT=37777)"
	@echo "  ingest-file     Ingest file (FILE=notes.txt, PROFILE=codex)"
	@echo "  ingest-stdin    Ingest from stdin: cat file | make ingest-stdin"

init-codex:
	$(PYTHON) $(TOOL) --profile codex init

init-claude:
	$(PYTHON) $(TOOL) --profile claude init

init-shared:
	$(PYTHON) $(TOOL) --profile shared init

stats:
	$(PYTHON) $(TOOL) --profile $(PROFILE) manage stats

clean-preview:
	$(PYTHON) $(TOOL) --profile $(PROFILE) clean --older-than-days $${DAYS:-90} --dry-run

vacuum:
	$(PYTHON) $(TOOL) --profile $(PROFILE) manage vacuum

search:
	$(PYTHON) $(TOOL) --profile $(PROFILE) search "$${Q:-memory}" --limit $${LIMIT:-20}

list:
	$(PYTHON) $(TOOL) --profile $(PROFILE) list --limit $${LIMIT:-20}

viewer:
	$(PYTHON) $(VIEWER) --profile $(PROFILE) --port $${PORT:-37777}

ingest-file:
	@if [ -z "$${FILE}" ]; then echo "FILE is required"; exit 1; fi
	$(PYTHON) $(INGEST) --profile $(PROFILE) --raw-file "$${FILE}" --auto-tags

ingest-stdin:
	$(PYTHON) $(INGEST) --profile $(PROFILE) --auto-tags
