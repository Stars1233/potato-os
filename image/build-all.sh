#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required. Install from https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

exec uv run --script "${SCRIPT_DIR}/build_all.py" "$@"
