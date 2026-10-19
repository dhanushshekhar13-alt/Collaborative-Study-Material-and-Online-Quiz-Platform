#!/usr/bin/env sh
# Deploy the current main branch: fetch it, rebuild the image, run migrations and restart.
# Usage (on the host that runs Docker):  ./scripts/deploy.sh
set -eu

cd "$(dirname "$0")/.."
git fetch origin main
git checkout main
git pull --ff-only origin main
docker compose up -d --build
docker compose ps
