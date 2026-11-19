#!/bin/sh
set -eu
PUID="${PUID:-99}"; PGID="${PGID:-100}"
case "$PUID" in *[!0-9]*|'') echo "PUID must be numeric" >&2; exit 1;; esac
case "$PGID" in *[!0-9]*|'') echo "PGID must be numeric" >&2; exit 1;; esac
ZILCHO_PASSWORD="${ZILCHO_PASSWORD:-}"
if [ "${#ZILCHO_PASSWORD}" -lt 12 ]; then echo "Set ZILCHO_PASSWORD to at least 12 characters" >&2; exit 1; fi
umask "${UMASK:-002}"
# Never chmod/chown a media library. Only create/chown this app's own data directories.
if [ "$(id -u)" = 0 ]; then
  mkdir -p /config/models /config/cache /config/work /config/transcripts /config/packages /config/logs /config/data /config/settings /config/inputs /config/exports
  chown "${PUID:-99}:${PGID:-100}" /config /config/models /config/cache /config/work /config/transcripts /config/packages /config/logs /config/data /config/settings /config/inputs /config/exports
  exec gosu "${PUID:-99}:${PGID:-100}" "$@"
fi
exec "$@"
