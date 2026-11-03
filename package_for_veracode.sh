#!/usr/bin/env bash
# Builds a source-only zip for a Veracode static scan.
# Output lands in veracode/xbrl-agent-veracode-<date>.zip
# Run ./start.sh at least once first so the venv exists.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x venv/bin/python ]; then
    echo "ERROR: venv not found. Run ./start.sh once first." >&2
    exit 1
fi
command -v git >/dev/null || { echo "ERROR: git not found." >&2; exit 1; }

venv/bin/python scripts/package_for_veracode.py "$@"
echo
echo "Done. Upload the zip above in the Veracode web portal."
