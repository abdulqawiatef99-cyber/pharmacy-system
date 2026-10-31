#!/usr/bin/env bash
# Rebuild everything from the original supplier files. Run from data/supplier-catalog.
set -euo pipefail
cd "$(dirname "$0")/.."
(cd originals && sha256sum -c --quiet SHA256SUMS)
python3 -I scripts/extract_pdfs.py originals build
python3 -I scripts/build_catalog.py originals build
python3 -I scripts/build_outputs.py originals build output
python3 -m pytest -q tests
