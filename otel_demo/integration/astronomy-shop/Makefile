PYTHON ?= python3

.PHONY: help check test corpus corpus-check preflight live-preflight report dry-run adapter-help

help:
	@echo "Targets:"
	@echo "  check           Run all local checks"
	@echo "  test            Run the stdlib unittest suite"
	@echo "  corpus          Regenerate docs-corpus/corpus.jsonl and manifest.json"
	@echo "  corpus-check    Fail if the committed corpus is stale"
	@echo "  preflight       Local readiness checks (no Docker required)"
	@echo "  live-preflight  Compose config and stack probes (Docker + running stack)"
	@echo "  report          Markdown scenario coverage report"
	@echo "  dry-run         Print the T-02 scenario plan without requests"
	@echo "  adapter-help    Show adapter CLI help"

check: test corpus-check preflight

test:
	$(PYTHON) -m unittest discover -s tests

corpus:
	$(PYTHON) tools/build_corpus.py

corpus-check:
	$(PYTHON) tools/build_corpus.py --check

preflight:
	$(PYTHON) tools/preflight.py --local-only

live-preflight:
	$(PYTHON) tools/preflight.py --execute --probe

report:
	$(PYTHON) tools/scenario_report.py --format md

dry-run:
	$(PYTHON) tools/run_scenario.py T-02 --dry-run

adapter-help:
	$(PYTHON) -m adapter --help
