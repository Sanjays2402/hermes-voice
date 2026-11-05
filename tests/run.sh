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
   ! cmp -s "$ROOT/wakeword/model/hey_hermes.json" "$ASSETS/wakeword/hey_hermes.json"; then
  echo "skill assets are out of date: run tools/sync_skill_assets.sh" >&2
  exit 1
fi
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" "$ROOT/tests/docker"
docker run --rm -v "$ROOT:/repo" -w /repo -e PYTHONDONTWRITEBYTECODE=1 "$IMAGE" sh -c "
  python -m pytest -p no:cacheprovider -q ${*:-} &&
  ruff check custom_components tests &&
  ruff format --check custom_components tests &&
  mypy custom_components/hermes_voice"
