#!/usr/bin/env bash
# sprinkler-ai bootstrap installer.
#
# Creates a venv, installs the package, and (optionally) runs the interactive
# config setup. Idempotent — safe to re-run.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${REPO_DIR}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

c_red()    { printf "\033[31m%s\033[0m" "$*"; }
c_green()  { printf "\033[32m%s\033[0m" "$*"; }
c_yellow() { printf "\033[33m%s\033[0m" "$*"; }
c_dim()    { printf "\033[2m%s\033[0m" "$*"; }

echo
echo "  $(c_green 'sprinkler-ai') · installer"
echo "  ──────────────────────────"

# ── Python check ─────────────────────────────────────────────────────────────
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "  $(c_red '✗') ${PYTHON_BIN} not found. Install Python 3.11+ and re-run."
  exit 1
fi
PY_VER=$("${PYTHON_BIN}" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
echo "  $(c_green '✓') ${PYTHON_BIN} (${PY_VER})"

# Soft version check.
PY_MAJ=$(echo "${PY_VER}" | cut -d. -f1)
PY_MIN=$(echo "${PY_VER}" | cut -d. -f2)
if [ "${PY_MAJ}" -lt 3 ] || { [ "${PY_MAJ}" -eq 3 ] && [ "${PY_MIN}" -lt 11 ]; }; then
  echo "  $(c_red '✗') Python 3.11+ required, found ${PY_VER}."
  exit 1
fi

# ── Optional CLI sanity checks (warn only) ───────────────────────────────────
if ! command -v claude >/dev/null 2>&1; then
  echo "  $(c_yellow '!') 'claude' CLI not on PATH. Install from https://claude.ai/code"
  echo "    (you can still continue — set CLAUDE_BIN in .env to its absolute path)"
fi
if ! command -v gemini >/dev/null 2>&1; then
  echo "  $(c_dim "  (optional)  'gemini' CLI not found — fallback unavailable.")"
fi

# ── venv ─────────────────────────────────────────────────────────────────────
if [ ! -d "${VENV_DIR}" ]; then
  echo "  $(c_green '✓') creating venv at ${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
else
  echo "  $(c_green '✓') venv already exists at ${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip >/dev/null

# ── install package ──────────────────────────────────────────────────────────
echo "  $(c_green '✓') installing dependencies (this takes a minute)"
EXTRAS=""
if [ "${INSTALL_VISION:-1}" = "1" ]; then
  EXTRAS="[vision]"
  echo "    $(c_dim '— including [vision] extras (Nest WebRTC); set INSTALL_VISION=0 to skip)')"
fi
pip install -e ".${EXTRAS}" >/dev/null

# ── interactive config ───────────────────────────────────────────────────────
echo
if [ -f "${REPO_DIR}/config.yaml" ] && [ -f "${REPO_DIR}/.env" ]; then
  echo "  $(c_green '✓') config.yaml and .env already present — skipping setup"
  echo "    (run \`sprinkler-ai-init --force\` if you want to redo the wizard)"
else
  echo "  Launching interactive setup..."
  echo
  "${VENV_DIR}/bin/sprinkler-ai-init" || {
    echo
    echo "  $(c_yellow '!') Setup cancelled. You can re-run any time with:"
    echo "      ${VENV_DIR}/bin/sprinkler-ai-init"
    exit 0
  }
fi

# ── done ─────────────────────────────────────────────────────────────────────
echo
echo "  $(c_green 'Done.')  Try:"
echo "    ${VENV_DIR}/bin/sprinkler-ai --dry-run"
echo
echo "  To install systemd timers (4am irrigation + mid-morning vision):"
echo "    see contrib/ in this repo and the README."
echo
