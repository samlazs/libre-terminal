#!/bin/sh
# EXPERIMENTAL / UNTESTED: Official platform support is Windows 10/11 only.
# Start the V5 data backend and the terminal UI in the background, rebuild the dashboards, open the browser.
#   sh start.sh --no-browser
#   sh start.sh --no-wait                                     (open browser right away without waiting for warm-up)
#   sh start.sh --with-v4                                     (also start optional legacy V4 backend)
#   sh start.sh --ui-port 7950 --v5-port 7900                 (other ports; remembered for next time)
# Logs: local/logs/. The logic lives in scripts/launcher.py.
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv-v5/bin/python ]; then echo "Not installed yet - run sh install.sh first."; exit 1; fi
exec .venv-v5/bin/python scripts/launcher.py start "$@"
