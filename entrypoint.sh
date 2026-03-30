#!/bin/sh
set -e

python3 /upload_to_confluence.py \
  --root-page-id "${INPUT_ROOT_PAGE_ID}" \
  --root-dir "${INPUT_ROOT_DIR}" \
  --space "${INPUT_SPACE}"