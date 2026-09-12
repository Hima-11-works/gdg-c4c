#!/usr/bin/env bash
# Regenerates backend/requirements.lock: exact runtime dependency versions for
# the Docker image (Linux, Python 3.12). Run after editing backend/pyproject.toml.
# Requires uv (https://docs.astral.sh/uv/); installing from the lock only needs pip.
set -euo pipefail
cd "$(dirname "$0")/../backend"

uv pip compile pyproject.toml \
  --python-version 3.12 \
  --python-platform x86_64-unknown-linux-gnu \
  --no-header \
  --output-file requirements.lock
