#!/usr/bin/env bash
# Copy the Home Assistant integration and the wake word model into the skill, so the skill works when it
# is installed on its own (`hermes skills install` copies only the skill folder).
# tests/run.sh fails if the copies are out of date.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$ROOT/skill/hermes-voice-setup/assets"
rm -rf "$ASSETS/hermes_voice" "$ASSETS/wakeword"
mkdir -p "$ASSETS/wakeword"
rsync -a --exclude __pycache__ "$ROOT/custom_components/hermes_voice/" "$ASSETS/hermes_voice/"
cp "$ROOT/wakeword/model/hey_hermes.tflite" "$ROOT/wakeword/model/hey_hermes.json" "$ASSETS/wakeword/"
echo "synced $ASSETS"
