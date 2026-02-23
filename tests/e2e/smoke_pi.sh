#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_PASSWORD="${PI_PASSWORD:-raspberry}"
PI_HOST_PRIMARY="${PI_HOST_PRIMARY:-192.168.1.132}"
PI_HOST_FALLBACK="${PI_HOST_FALLBACK:-192.168.1.131}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXPECT_BACKEND="${EXPECT_BACKEND:-llama}"
LLAMA_BUNDLE_ROOT="${LLAMA_BUNDLE_ROOT:-${PROJECT_ROOT}/references/old_reference_design/llama_cpp_binary}"
LLAMA_BUNDLE_SRC="${LLAMA_BUNDLE_SRC:-}"
WAIT_ATTEMPTS="${WAIT_ATTEMPTS:-120}"
WAIT_SECONDS="${WAIT_SECONDS:-5}"
PI_SCHEME="${PI_SCHEME:-http}"
PI_PORT="${PI_PORT:-80}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing command: $1" >&2
    exit 1
  fi
}

require_cmd sshpass
require_cmd rsync
require_cmd curl
require_cmd jq

pick_host() {
  if ping -c 1 -W 1 "${PI_HOST_PRIMARY}" >/dev/null 2>&1; then
    echo "${PI_HOST_PRIMARY}"
    return
  fi
  if ping -c 1 -W 1 "${PI_HOST_FALLBACK}" >/dev/null 2>&1; then
    echo "${PI_HOST_FALLBACK}"
    return
  fi
  echo "" 
}

resolve_bundle_src() {
  if [ -n "${LLAMA_BUNDLE_SRC}" ]; then
    echo "${LLAMA_BUNDLE_SRC}"
    return
  fi
  if [ ! -d "${LLAMA_BUNDLE_ROOT}" ]; then
    echo ""
    return
  fi
  find "${LLAMA_BUNDLE_ROOT}" -mindepth 1 -maxdepth 1 -type d -name 'llama_server_bundle_*' | sort | tail -n 1
}

PI_HOST="$(pick_host)"
if [ -z "${PI_HOST}" ]; then
  echo "No reachable Pi host found (${PI_HOST_PRIMARY}, ${PI_HOST_FALLBACK})." >&2
  exit 1
fi

echo "Using Pi host: ${PI_HOST}"
BASE_URL="${PI_SCHEME}://${PI_HOST}"
if [ -n "${PI_PORT}" ]; then
  BASE_URL="${BASE_URL}:${PI_PORT}"
fi

bundle_src="$(resolve_bundle_src)"
if [ -z "${bundle_src}" ] || [ ! -x "${bundle_src}/bin/llama-server" ] || [ ! -d "${bundle_src}/lib" ]; then
  echo "Missing llama bundle source. Set LLAMA_BUNDLE_SRC or ensure ${LLAMA_BUNDLE_ROOT}/llama_server_bundle_* exists." >&2
  exit 1
fi

SSHPASS="${PI_PASSWORD}" sshpass -e rsync -az --delete \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude 'references/' \
  "${PROJECT_ROOT}/" "${PI_USER}@${PI_HOST}:/tmp/potato-os/"

SSHPASS="${PI_PASSWORD}" sshpass -e rsync -az --delete \
  "${bundle_src}/" "${PI_USER}@${PI_HOST}:/tmp/potato-os/.llama_bundle/"

SSHPASS="${PI_PASSWORD}" sshpass -e ssh -o StrictHostKeyChecking=accept-new "${PI_USER}@${PI_HOST}" \
  "cd /tmp/potato-os && PI_PASSWORD='${PI_PASSWORD}' POTATO_LLAMA_BUNDLE_SRC='/tmp/potato-os/.llama_bundle' ./bin/install_dev.sh"

status_json=""
for _ in $(seq 1 "${WAIT_ATTEMPTS}"); do
  status_json="$(curl -sS --max-time 5 "${BASE_URL}/status" || true)"
  if [ -z "${status_json}" ]; then
    sleep "${WAIT_SECONDS}"
    continue
  fi
  if ! printf '%s' "${status_json}" | jq -e . >/dev/null 2>&1; then
    sleep "${WAIT_SECONDS}"
    continue
  fi
  active_backend="$(printf '%s' "${status_json}" | jq -r '.backend.active // empty')"
  llama_healthy="$(printf '%s' "${status_json}" | jq -r '.llama_server.healthy // false')"
  if [ -n "${EXPECT_BACKEND}" ] && [ "${active_backend}" = "${EXPECT_BACKEND}" ]; then
    if [ "${EXPECT_BACKEND}" != "llama" ] || [ "${llama_healthy}" = "true" ]; then
      break
    fi
  fi
  sleep "${WAIT_SECONDS}"
done

if [ -z "${status_json}" ]; then
  echo "Unable to reach status endpoint on ${PI_HOST}" >&2
  exit 1
fi

echo "${status_json}"

active_backend="$(printf '%s' "${status_json}" | jq -r '.backend.active // empty')"
if [ -n "${EXPECT_BACKEND}" ] && [ "${active_backend}" != "${EXPECT_BACKEND}" ]; then
  echo "Expected backend '${EXPECT_BACKEND}', got '${active_backend}'" >&2
  exit 1
fi

if [ "${EXPECT_BACKEND}" = "llama" ]; then
  llama_healthy="$(printf '%s' "${status_json}" | jq -r '.llama_server.healthy // false')"
  if [ "${llama_healthy}" != "true" ]; then
    echo "llama backend expected but not healthy" >&2
    exit 1
  fi
fi

curl --fail --retry 20 --retry-delay 2 --retry-connrefused --retry-all-errors -X POST "${BASE_URL}/v1/chat/completions" \
  -H 'content-type: application/json' \
  -d '{"model":"qwen-local","stream":false,"max_tokens":32,"messages":[{"role":"user","content":"ping"}]}'

echo "Smoke checks completed for ${PI_HOST}"
