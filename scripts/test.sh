#!/usr/bin/env bash
set -e
source "$(dirname "$0")/../venv/bin/activate"
echo "=== Unit tests (no DB) ==="
pytest app/tests/ -v --tb=short

echo ""
echo "=== Full suite (with DB) ==="
RUN_DB_TESTS=true pytest app/tests/ -v --tb=short
