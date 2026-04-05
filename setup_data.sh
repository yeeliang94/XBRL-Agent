#!/usr/bin/env bash
# Copies test PDFs and Excel templates into data/ from a source location.
# Source defaults to the sibling ream/ repo; override with DATA_SOURCE env var.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"

# Default source: the ream/ repo that originally held these files
DEFAULT_SOURCE="$SCRIPT_DIR/../ream"
SOURCE="${DATA_SOURCE:-$DEFAULT_SOURCE}"

if [ ! -d "$SOURCE" ]; then
    echo "ERROR: Source directory not found: $SOURCE"
    echo "Set DATA_SOURCE env var to point at the source location."
    exit 1
fi

echo "Copying data files from: $SOURCE"
mkdir -p "$DATA_DIR"

# Each required file with its source path relative to $SOURCE
declare -a FILES=(
    "sample_data/FINCO-Audited-Financial-Statement-2021.pdf"
    "sample_data/Oriental.pdf"
    "sample_data/ground_truth_sofp_sopl.xlsx"
    "MBRS_test.xlsx"
)

MISSING=0
for rel_path in "${FILES[@]}"; do
    src="$SOURCE/$rel_path"
    dst="$DATA_DIR/$(basename "$rel_path")"
    if [ -f "$src" ]; then
        cp "$src" "$dst"
        echo "  OK  $(basename "$rel_path")"
    else
        echo "  MISS  $rel_path (not found at $src)"
        MISSING=$((MISSING + 1))
    fi
done

if [ "$MISSING" -gt 0 ]; then
    echo ""
    echo "WARNING: $MISSING file(s) missing. Agent may not run correctly without them."
    exit 1
fi

echo ""
echo "Done. Files in $DATA_DIR:"
ls -lh "$DATA_DIR"
