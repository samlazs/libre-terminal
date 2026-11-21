#!/bin/sh
# EXPERIMENTAL / UNTESTED: Official platform support is Windows 10/11 only.
# Install (or repair / update) the terminal on macOS or Linux. Safe to re-run.
#   sh install.sh             main V5 backend (default) from config/requirements-v5.lock.txt
#   sh install.sh --with-v4   also install the optional legacy V4 backend (FMP, Finviz, EconDB, Intrinio)
#   sh install.sh --upgrade   latest compatible releases, then rewrites the lock files
# (--skip-v4 is accepted as a backward-compatible alias; V5-only is the default.)
# The logic lives in scripts/launcher.py (shared with Windows Install.cmd).
set -eu
cd "$(dirname "$0")"

find_uv() {
  if command -v uv >/dev/null 2>&1; then command -v uv; return 0; fi
  for p in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$p" ]; then echo "$p"; return 0; fi
  done
  return 1
}

if ! UV=$(find_uv); then
  echo "uv is not installed. uv is a small, free tool (by Astral) that downloads Python and this project's packages."
  echo "Installing it for your user only from https://astral.sh/uv ..."
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    echo "Neither curl nor wget is available. Install uv from https://docs.astral.sh/uv/getting-started/installation/ and run this again."
    exit 1
  fi
  if ! UV=$(find_uv); then
    echo "Could not find uv after installing it. Open a new terminal and run ./install.sh again."
    exit 1
  fi
fi

# uv finds (or downloads) Python 3.12 just to run the installer; the environments themselves are created by the launcher.
exec "$UV" run --no-project --python 3.12 scripts/launcher.py install "$@"
