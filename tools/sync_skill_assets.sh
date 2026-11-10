#!/usr/bin/env bash
# Copy the Home Assistant integration, the wake word model, Hermes Eye (the camera app's source and its
# MCP tool) and Hermes Visual Memory (its code, not its tests) into the skill, so the skill works when it
# is installed on its own (`hermes skills install` copies only the skill folder).
# tests/run.sh fails if the copies are out of date.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$ROOT/skill/hermes-voice-setup/assets"
rm -rf "$ASSETS/hermes_voice" "$ASSETS/wakeword" "$ASSETS/eye" "$ASSETS/visual-memory"
mkdir -p "$ASSETS/wakeword" "$ASSETS/eye" "$ASSETS/visual-memory"
rsync -a --exclude __pycache__ "$ROOT/custom_components/hermes_voice/" "$ASSETS/hermes_voice/"
cp "$ROOT/wakeword/model/"{hey_hermes.tflite,hey_hermes.json,MODEL_NOTICE.md} "$ASSETS/wakeword/"
rsync -a --exclude build --exclude .gitignore "$ROOT/devices/echo/eye-app/" "$ASSETS/eye/app/"
cp "$ROOT/extras/eye/eye_mcp.py" "$ASSETS/eye/"
rsync -a --exclude __pycache__ "$ROOT/extras/visual-memory/vismem" "$ASSETS/visual-memory/"
cp "$ROOT/extras/visual-memory/"{pyproject.toml,config.example.toml,README.md} "$ASSETS/visual-memory/"
echo "synced $ASSETS"
