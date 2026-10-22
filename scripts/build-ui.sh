#!/usr/bin/env bash
# Build the React UI into src/dendrite/ui/dist (what CI runs before `uv build`).
set -euo pipefail
cd "$(dirname "$0")/../ui"
npm ci
npm run build
