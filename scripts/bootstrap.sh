#!/usr/bin/env bash
# One-time local setup: creates .env files from the committed examples if
# they don't already exist. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

copy_if_missing() {
  local example="$1"
  local target="$2"
  if [ -f "$target" ]; then
    echo "skip:   $target already exists"
  else
    cp "$example" "$target"
    echo "created: $target"
  fi
}

copy_if_missing ".env.example" ".env"
copy_if_missing "frontend/.env.example" "frontend/.env"

echo
echo "Next steps:"
echo "  docker compose up --build      # starts Postgres/PostGIS + the API"
echo "  cd frontend && npm ci && npm run dev"
echo
echo "Change POSTGRES_PASSWORD in .env before sharing or deploying anything."
