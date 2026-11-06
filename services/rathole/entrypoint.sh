#!/bin/sh
set -eu

if [ "$#" -gt 0 ]; then
    exec /usr/local/bin/rathole "$@"
fi

while [ ! -s "$RATHOLE_CONFIG_PATH" ]; do
    sleep 5
done

exec /usr/local/bin/rathole "$RATHOLE_CONFIG_PATH"
