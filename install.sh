#!/usr/bin/env bash
set -eu
app_source=$(dirname -- "$(readlink -f -- "$0")")
exec python3 "$app_source/packaging/install.py" "$@"
