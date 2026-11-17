#!/usr/bin/env bash
# Download ONLY the AOT ground-truth metadata (no images) into data/aot/raw/ (ignored by git).
# E0 needs range + bounding boxes only, so the ~13 TB of images are not required.
# Needs the AWS CLI; the bucket is public (--no-sign-request).
set -euo pipefail
DEST="$(cd "$(dirname "$0")/.." && pwd)/data/aot/raw"
BUCKET="s3://airborne-obj-detection-challenge-training"
for part in part1 part2 part3; do
  mkdir -p "$DEST/$part/ImageSets"
  for f in groundtruth.json valid_encounters_maxRange700_maxGap3_minEncLen30.json; do
    aws s3 cp --no-sign-request "$BUCKET/$part/ImageSets/$f" "$DEST/$part/ImageSets/$f"
  done
done
echo "Done. Next:"
echo "  python3 scripts/aot_audit.py --gt data/aot/raw/part*/ImageSets/groundtruth.json"
echo "  python3 scripts/aot_audit_checks.py"
