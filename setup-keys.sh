#!/bin/sh
# EXPERIMENTAL / UNTESTED: Official platform support is Windows 10/11 only.
# Optional: add free API keys (FRED, BLS, EIA, Congress.gov, FMP, ...). The terminal works without any.
# Opens local/api_keys.env in $EDITOR (or nano), then applies the keys to ~/.openbb_platform/user_settings.json.
#   sh setup-keys.sh --no-edit    only apply what is already in local/api_keys.env
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv-v5/bin/python ]; then echo "Not installed yet - run sh install.sh first."; exit 1; fi
exec .venv-v5/bin/python scripts/launcher.py keys "$@"
