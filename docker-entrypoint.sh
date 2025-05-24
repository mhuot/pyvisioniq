#!/bin/bash
set -e

# If password file is provided, read it
if [ -n "$PYVISIONIQ_MASTER_PASSWORD_FILE" ] && [ -f "$PYVISIONIQ_MASTER_PASSWORD_FILE" ]; then
    export PYVISIONIQ_MASTER_PASSWORD=$(cat "$PYVISIONIQ_MASTER_PASSWORD_FILE")
fi

# Execute the main command
exec "$@"