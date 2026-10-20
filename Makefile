# JARVIS — local development tasks.
#
#   make setup        create the venv, install deps, build the dashboard, run doctor
#   make dev          API on :8000 + hot-reloading dashboard on :5173
#   make serve        API + built dashboard on :8000
#   make test         the whole suite
#   make demo         the MVP command list, end to end
#
# Extras:  make setup-desktop | setup-browser | setup-voice | setup-all

VENV      ?= .venv
BIN       := $(VENV)/bin
PY        := $(BIN)/python
PIP       := $(BIN)/pip
HOST      ?= 0.0.0.0
PORT      ?= 8000
WEB_PORT  ?= 5173

.DEFAULT_GOAL := help

help: ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

$(PY):
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

setup: $(PY) ## core setup: venv + deps + dashboard build + doctor
	$(PIP) install -r requirements.txt
	[ -f .env ] || cp .env.example .env
	cd frontend && npm install --no-audit --no-fund && npm run build
	$(PY) main.py doctor

setup-desktop: $(PY) ## add real mouse/keyboard/screen control (Phase 2)
	$(PIP) install -e ".[desktop]"
	$(PY) main.py doctor

setup-browser: $(PY) ## add the Playwright web agent (Phase 5)
	$(PIP) install -e ".[browser]" && $(BIN)/playwright install chromium

setup-voice: $(PY) ## add the local microphone + offline Whisper STT
	$(PIP) install -e ".[voice]"

setup-vision: $(PY) ## add OCR for screen reading (Phase 6)
	$(PIP) install -e ".[vision]"

setup-all: $(PY) ## every optional capability
	$(PIP) install -e ".[desktop,browser,voice,vision,dev]"
	$(BIN)/playwright install chromium

serve: $(PY) ## run the API + built dashboard
	$(PY) main.py serve --host $(HOST) --port $(PORT)

dev: ## run the API and the hot-reloading dashboard together
	bash scripts/dev.sh

repl: $(PY) ## interactive console
	$(PY) main.py repl

listen: $(PY) ## microphone + wake-word loop
	$(PY) main.py listen

doctor: $(PY) ## what will run on this machine
	$(PY) main.py doctor

demo: $(PY) ## the MVP command list, end to end
	$(PY) main.py demo

safety: $(PY) ## demonstrate the three permission levels
	$(PY) main.py safety

tools: $(PY) ## list the tool catalogue
	$(PY) main.py tools

plan: $(PY) ## plan a multi-step goal (G="prepare my workspace for coding")
	$(PY) main.py plan "$(or $(G),prepare my workspace for coding)"

ask: $(PY) ## one-shot command (Q="open vscode")
	$(PY) main.py ask "$(or $(Q),what can you do)"

test: $(PY) ## run the test suite
	$(PY) -m pytest -q

watch: $(PY) ## run the tests that relate to what you just changed
	$(PY) -m pytest -q --lf -x

lint: $(PY) ## ruff check + fix
	$(BIN)/ruff check jarvis tests --fix

types: ## type-check the dashboard
	cd frontend && npx tsc --noEmit

build: ## build the dashboard into frontend/dist
	cd frontend && npm run build

docs-tools: $(PY) ## regenerate docs/TOOLS.md from the live registry
	$(PY) scripts/export_tools.py

clean: ## remove build artefacts and caches
	rm -rf frontend/dist .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -not -path "./.venv/*" -prune -exec rm -rf {} +

nuke: clean ## also delete the JARVIS database, screenshots and logs
	rm -rf .jarvis

.PHONY: help setup setup-desktop setup-browser setup-voice setup-vision setup-all \
        serve dev repl listen doctor demo safety tools plan ask test watch lint \
        types build docs-tools clean nuke
