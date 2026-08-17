#!/bin/sh
# Push the repo's Home Assistant config files to the HA host, if they changed.
#
# The dashboard is registered in HA as a YAML-mode dashboard, and the Open-Meteo
# sensors are included from configuration.yaml, so these files in the pyvisionic
# repo are the source of truth. Copy them over; no pasting into the UI.
#
# Compares checksums first and exits silently when everything matches, so this
# is cheap to run on a schedule -- the common case is one ssh round trip and no
# output at all.
#
# The dashboard hot-reloads on browser refresh. REST sensors do not: changing
# pyvisionic-weather.yaml needs an HA restart, which this script reports rather
# than performs, since it runs unattended from cron.
#
# Usage: tools/sync_ha_dashboard.sh [user@host]

set -eu

REMOTE="${1:-mhuot@themint-1.quagga-ide.ts.net}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER="home-assistant"

# file:needs_restart
FILES="pyvisionic-dashboard.yaml:no pyvisionic-weather.yaml:yes"

restart_needed=""

for entry in $FILES; do
    name="${entry%:*}"
    restarts="${entry#*:}"
    src="$REPO/homeassistant/$name"
    dest="/config/$name"

    # Fail before copying if the YAML is malformed -- a broken include stops HA
    # from starting, and a broken dashboard renders an error page.
    python3 -c "import sys,yaml; yaml.safe_load(open(sys.argv[1]))" "$src"

    local_sum=$(sha256sum "$src" | cut -d' ' -f1)
    remote_sum=$(ssh -o BatchMode=yes "$REMOTE" \
        "docker exec $CONTAINER sha256sum $dest 2>/dev/null | cut -d' ' -f1" || true)

    [ "$local_sum" = "$remote_sum" ] && continue

    scp -q "$src" "$REMOTE:/tmp/$name"
    ssh -o BatchMode=yes "$REMOTE" \
        "docker cp /tmp/$name $CONTAINER:$dest && rm /tmp/$name"

    echo "$(date '+%Y-%m-%d %H:%M') $name synced to $REMOTE (${local_sum%"${local_sum#?????????}"}…)"
    [ "$restarts" = "yes" ] && restart_needed="$restart_needed $name"
done

if [ -n "$restart_needed" ]; then
    echo "restart Home Assistant to pick up:$restart_needed"
    echo "  ssh $REMOTE 'docker restart $CONTAINER'"
fi
