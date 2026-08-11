#!/bin/sh
# Cron wrapper for the trip readiness check.
# Usage: trip_readiness_cron.sh <never|on-problem|always>
# Runs from the repo with its venv and appends to logs/trip_readiness.log.
# A non-zero exit from the checker just means "not on track", so it is logged
# rather than propagated -- otherwise cron would treat every warning as a
# job failure and mail about it.

set -u

REPO=/home/ubuntu/new-pyvisionic
NOTIFY=${1:-on-problem}
LOG="$REPO/logs/trip_readiness.log"

mkdir -p "$REPO/logs"
cd "$REPO" || exit 0

{
    echo "--- $(date '+%Y-%m-%d %H:%M:%S %Z')  notify=$NOTIFY ---"
    "$REPO/venv/bin/python" tools/trip_readiness_check.py --notify "$NOTIFY" 2>&1
    echo "(status=$?)"
} >>"$LOG" 2>&1

exit 0
