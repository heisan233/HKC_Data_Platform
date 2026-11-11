#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$SCRIPT_DIR/config.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$SCRIPT_DIR/config.env"
  set +a
fi
exec python3 "$SCRIPT_DIR/clear_upload_markers.py" "$@"
