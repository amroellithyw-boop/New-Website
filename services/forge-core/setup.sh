#!/usr/bin/env bash
# ForgeOS one-time installer for Mac and Linux. Run:  bash setup.sh
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Install Python 3.11+ from https://www.python.org/downloads/ then run this again."; exit 1; }
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]"
echo; echo "Checking the install..."; forge bench
echo; forge setup
echo; echo "All set. Each time: cd $(pwd) && source .venv/bin/activate"
