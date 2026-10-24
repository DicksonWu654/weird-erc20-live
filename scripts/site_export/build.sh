#!/usr/bin/env sh
set -eu
root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
exec "$root/.venv/bin/python" "$root/scripts/site_export/export.py"
