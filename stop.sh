#!/bin/sh
# EXPERIMENTAL / UNTESTED: Official platform support is Windows 10/11 only.
# Stop the data backend(s) and the terminal UI.
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv-v5/bin/python ]; then echo "Not installed - nothing to stop."; exit 0; fi
exec .venv-v5/bin/python scripts/launcher.py stop "$@"
