#!/usr/bin/env bash
# Host: run the production CICC collector (cicc_report_collector incr).
# Same flags as scripts/vps/cicc-dispatch.py MODE_ARGS["incr"].
# Lab-only. Never print cookie contents.
set -euo pipefail

CACHE_ROOT="${CACHE_ROOT:-/data/vpush-ima-cache}"
SRC_ROOT="${VPUSH_SRC_ROOT:-/opt/vpush-ima-lab/src}"
PYTHON="${VPUSH_PYTHON:-/opt/vpush-ima-lab/venv/bin/python}"
SCRIPT="${CICC_COLLECTOR_PY:-$SRC_ROOT/scripts/cicc_report_collector.py}"
COOKIE="${VPUSH_CICC_COOKIE_FILE:-/opt/vpush-ima-lab/secrets/cicc-cookies.txt}"
LOG_DIR="${CACHE_ROOT}/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="${LOG_DIR}/cicc-host-sync-${STAMP}.log"
DAYS="${CICC_INCR_DAYS:-3}"
SETTINGS="${CACHE_ROOT}/ops-lab-settings.json"
if [[ -f "$SETTINGS" ]]; then
  parsed="$("$PYTHON" -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); print(int(d.get("cicc_incr_days") or 3))' "$SETTINGS" 2>/dev/null || true)"
  if [[ "$parsed" =~ ^[1-9][0-9]*$ ]]; then
    DAYS="$parsed"
  fi
fi

umask 077
mkdir -p "${LOG_DIR}"

# Create the log up front and grant read to the vpush-rust container user
# (ro mount /app/cicc-lab-logs). Non-fatal; set CICC_LOG_READER_UID= to skip.
CICC_LOG_READER_UID="${CICC_LOG_READER_UID:-10001}"
: >>"${LOG}"
if [[ -n "${CICC_LOG_READER_UID}" ]] && command -v setfacl >/dev/null 2>&1; then
  setfacl -m "u:${CICC_LOG_READER_UID}:r,m::r" "${LOG}" 2>/dev/null || true
fi

if [[ ! -f "$SCRIPT" ]]; then
  echo "cicc_report_collector.py missing at $SCRIPT" >&2
  exit 2
fi
if [[ ! -f "$COOKIE" ]]; then
  echo "CICC cookie file missing; set VPUSH_CICC_COOKIE_FILE" >&2
  exit 1
fi

export CACHE_ROOT
export VPUSH_CICC_COOKIE_FILE="$COOKIE"
export VPUSH_ARM_STAGING_ROOT="${VPUSH_ARM_STAGING_ROOT:-$CACHE_ROOT/staging}"
export PYTHONPATH="${PYTHONPATH:-$SRC_ROOT}"

args=("--arm-middleware" "--days" "$DAYS" "--cookie-file" "$COOKIE")
if [[ "${DRY_RUN:-1}" == "1" ]]; then
  args+=(--dry-run)
fi

{
  echo "start $(date -Iseconds) days=${DAYS} dry_run=${DRY_RUN:-1}"
  set +e
  "$PYTHON" "$SCRIPT" "${args[@]}"
  rc=$?
  set -e
  echo "done rc=${rc} $(date -Iseconds)"
  exit "${rc}"
} >>"${LOG}" 2>&1
