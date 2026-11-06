#!/usr/bin/env bash
# Run tests, ruff and mypy inside the pinned Home Assistant test image.
# Usage: tests/run.sh [pytest args]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="hermes-voice-test:2026.10.4"
# The skill carries its own copy of the integration and wake word model (tools/sync_skill_assets.sh).
ASSETS="$ROOT/skill/hermes-voice-setup/assets"
if ! diff -rq -x __pycache__ "$ROOT/custom_components/hermes_voice" "$ASSETS/hermes_voice" >/dev/null ||
   ! cmp -s "$ROOT/wakeword/model/hey_hermes.tflite" "$ASSETS/wakeword/hey_hermes.tflite" ||
   ! cmp -s "$ROOT/wakeword/model/hey_hermes.json" "$ASSETS/wakeword/hey_hermes.json" ||
   ! cmp -s "$ROOT/wakeword/model/MODEL_NOTICE.md" "$ASSETS/wakeword/MODEL_NOTICE.md"; then
  echo "skill assets are out of date: run tools/sync_skill_assets.sh" >&2
  exit 1
fi
# The screen page's script: a syntax error there blanks the screen and no Python test would notice.
if command -v node >/dev/null; then
  node --check "$ROOT/custom_components/hermes_voice/screen/app.js"
else
  echo "note: node not found, screen/app.js syntax not checked" >&2
fi
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" "$ROOT/tests/docker"
docker run --rm -v "$ROOT:/repo" -w /repo -e PYTHONDONTWRITEBYTECODE=1 -e MYPY_CACHE_DIR=/tmp/mypy -e RUFF_CACHE_DIR=/tmp/ruff "$IMAGE" sh -c "
  python -m pytest -p no:cacheprovider -q --timeout 60 ${*:-} &&
  ruff check custom_components tests &&
  ruff format --check custom_components tests &&
  mypy custom_components/hermes_voice"
