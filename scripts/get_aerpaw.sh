#!/usr/bin/env bash
# Download the AERPAW AADM dataset (7.7 GB) from Dryad into data/aerpaw/raw/ (ignored by git).
# If the API download fails, download AADM2025Dryad.zip manually from
# https://datadryad.org/dataset/doi:10.5061/dryad.7d7wm3898 and place it in data/aerpaw/raw/.
set -euo pipefail

DEST="$(cd "$(dirname "$0")/.." && pwd)/data/aerpaw/raw"
ZIP="$DEST/AADM2025Dryad.zip"
URL="https://datadryad.org/api/v2/datasets/doi%3A10.5061%2Fdryad.7d7wm3898/download"

mkdir -p "$DEST"

if [ ! -f "$ZIP" ]; then
  echo "Downloading AADM dataset (~7.7 GB) to $DEST ..."
  curl -L --fail --retry 3 -C - -o "$ZIP" "$URL"
fi

echo "Unzipping ..."
unzip -n -q "$ZIP" -d "$DEST"
echo "Done. Radar logs: radar_data_N.json; ground truth: vehicleOut.txt per flight."
