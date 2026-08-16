#!/bin/sh
# Push the repo's Home Assistant dashboard to the HA host, if it changed.
#
# The dashboard is registered in HA as a YAML-mode dashboard reading
# /config/pyvisionic-dashboard.yaml, so this file in the repo is the source of
# truth. Copy it over and refresh the browser; no HA restart, no pasting.
#
# Compares checksums first and exits silently when they match, so this is
# cheap to run on a schedule -- the common case is one ssh round trip and no
# output at all.
#
# Usage: tools/sync_ha_dashboard.sh [user@host]

set -eu

REMOTE="${1:-mhuot@themint-1.quagga-ide.ts.net}"
SRC="$(cd "$(dirname "$0")/.." && pwd)/homeassistant/pyvisionic-dashboard.yaml"
CONTAINER="home-assistant"
DEST="/config/pyvisionic-dashboard.yaml"

# Fail before copying if the YAML is malformed -- a broken dashboard file
# makes HA render an error page for that dashboard.
python3 -c "import sys,yaml; yaml.safe_load(open(sys.argv[1]))" "$SRC"

local_sum=$(sha256sum "$SRC" | cut -d' ' -f1)
remote_sum=$(ssh -o BatchMode=yes "$REMOTE" \
    "docker exec $CONTAINER sha256sum $DEST 2>/dev/null | cut -d' ' -f1" || true)

if [ "$local_sum" = "$remote_sum" ]; then
    exit 0
fi

scp -q "$SRC" "$REMOTE:/tmp/pyvisionic-dashboard.yaml"
ssh -o BatchMode=yes "$REMOTE" \
    "docker cp /tmp/pyvisionic-dashboard.yaml $CONTAINER:$DEST && rm /tmp/pyvisionic-dashboard.yaml"

echo "$(date '+%Y-%m-%d %H:%M') dashboard synced to $REMOTE (${local_sum%"${local_sum#?????????}"}…)"
