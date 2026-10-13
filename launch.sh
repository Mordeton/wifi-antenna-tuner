#!/usr/bin/env bash
set -eu
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
if [[ -x .venv/bin/python ]]; then
    exec .venv/bin/python -m antenna_tuner "$@"
fi
exec /usr/bin/python3 -m antenna_tuner "$@"
