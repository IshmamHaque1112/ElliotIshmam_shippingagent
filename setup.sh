#!/usr/bin/env bash
# macOS/Linux: create .venv, install dependencies, run the offline tests.
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
pytest -q
echo "Done. Put your key in .env, then: source .venv/bin/activate && python eval.py"
