#!/bin/sh
# Push the repo's Home Assistant dashboard to the HA host.
#
# The dashboard is registered in HA as a YAML-mode dashboard reading
# /config/pyvisionic-dashboard.yaml, so this file in the repo is the source of
# truth. Copy it over and refresh the browser; no HA restart, no pasting.
#
# Usage: tools/sync_ha_dashboard.sh [user@host]

set -eu

REMOTE="${1:-mhuot@themint-1.quagga-ide.ts.net}"
SRC="$(dirname "$0")/../homeassistant/pyvisionic-dashboard.yaml"
CONTAINER="home-assistant"

# Fail before copying if the YAML is malformed -- a broken dashboard file
# makes HA render an error page for that dashboard.
python3 -c "import sys,yaml; yaml.safe_load(open(sys.argv[1]))" "$SRC"

scp -q "$SRC" "$REMOTE:/tmp/pyvisionic-dashboard.yaml"
ssh "$REMOTE" "docker cp /tmp/pyvisionic-dashboard.yaml $CONTAINER:/config/pyvisionic-dashboard.yaml && rm /tmp/pyvisionic-dashboard.yaml"

echo "dashboard synced to $REMOTE — refresh the browser to see it"
