#!/usr/bin/env bash
# One-shot setup: virtualenv, Python deps, dashboard build, capability report.
#
#   bash scripts/setup.sh            # core only (works headless, no API key needed)
#   bash scripts/setup.sh desktop    # + real mouse/keyboard/screen control
#   bash scripts/setup.sh browser    # + Playwright web agent
#   bash scripts/setup.sh voice      # + local microphone and offline Whisper STT
#   bash scripts/setup.sh all        # everything
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
EXTRAS="${1:-core}"

echo "==> JARVIS setup in $ROOT"

# --- python ---------------------------------------------------------------
PYTHON="${PYTHON:-python3}"
if [ ! -d .venv ]; then
  echo "==> creating .venv"
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -V
pip install --upgrade pip >/dev/null

echo "==> installing core dependencies"
pip install -r requirements.txt

case "$EXTRAS" in
  desktop) pip install -e ".[desktop]" ;;
  browser) pip install -e ".[browser]" && playwright install chromium ;;
  voice)   pip install -e ".[voice]" ;;
  vision)  pip install -e ".[vision]" ;;
  all)     pip install -e ".[desktop,browser,voice,vision]" && playwright install chromium ;;
  core|"") ;;
  *) echo "unknown extra '$EXTRAS' (use: core | desktop | browser | voice | vision | all)"; exit 2 ;;
esac

# --- config ---------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> created .env from .env.example (add OPENAI_API_KEY there for the real brain)"
fi

# --- dashboard ------------------------------------------------------------
if command -v npm >/dev/null 2>&1; then
  echo "==> building the dashboard"
  ( cd frontend && npm install --no-audit --no-fund && npm run build )
else
  echo "==> npm not found — skipping the dashboard build (the API still works)"
fi

# --- report ---------------------------------------------------------------
echo
python main.py doctor

cat <<'EOF'

==> done. next:
      python main.py demo          # the MVP command list, end to end
      python main.py serve         # API + dashboard on http://localhost:8000
      bash scripts/dev.sh          # API + hot-reloading dashboard
EOF
