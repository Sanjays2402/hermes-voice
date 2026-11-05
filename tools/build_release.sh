#!/usr/bin/env bash
# Build the release files from the committed tree (git archive), so a release holds exactly what is in git.
#   tools/build_release.sh [OUT_DIR]     (default: dist/ in the repo, which git ignores)
#   hermes_voice-<v>.zip          Home Assistant integration: unzip into <config>/custom_components/
#   hermes-voice-setup-<v>.zip    Hermes skill (scripts, integration and wake word model included)
#   hey_hermes-wakeword-<v>.zip   "Hey Hermes" microWakeWord model for echo-dot-assist
#   SHA256SUMS
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -n "$(git status --porcelain)" ]; then
  echo "working tree has uncommitted changes; commit first (the release is built from HEAD)" >&2
  exit 1
fi

V=$(python3 -c "import json;print(json.load(open('custom_components/hermes_voice/manifest.json'))['version'])")
for f in pyproject.toml skill/hermes-voice-setup/SKILL.md; do
  grep -qE "^version *[=:] *\"?$V\"?" "$f" || { echo "version in $f does not match manifest ($V)" >&2; exit 1; }
done
ASSETS=skill/hermes-voice-setup/assets
diff -rq -x __pycache__ custom_components/hermes_voice "$ASSETS/hermes_voice" >/dev/null &&
  cmp -s wakeword/model/hey_hermes.tflite "$ASSETS/wakeword/hey_hermes.tflite" &&
  cmp -s wakeword/model/hey_hermes.json "$ASSETS/wakeword/hey_hermes.json" &&
  cmp -s wakeword/model/MODEL_NOTICE.md "$ASSETS/wakeword/MODEL_NOTICE.md" ||
  { echo "skill assets are out of date: run tools/sync_skill_assets.sh and commit" >&2; exit 1; }
tools/check_clean.sh || { echo "fix the findings above before releasing" >&2; exit 1; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git archive HEAD | tar -x -C "$TMP"
DIST="$(mkdir -p "${1:-$ROOT/dist}" && cd "${1:-$ROOT/dist}" && pwd)"
rm -f "$DIST"/hermes_voice-*.zip "$DIST"/hermes-voice-setup-*.zip "$DIST"/hey_hermes-wakeword-*.zip "$DIST/SHA256SUMS"
(cd "$TMP/custom_components" && zip -qrX "$DIST/hermes_voice-$V.zip" hermes_voice)
(cd "$TMP/skill" && zip -qrX "$DIST/hermes-voice-setup-$V.zip" hermes-voice-setup)
(cd "$TMP/wakeword/model" && zip -qrX "$DIST/hey_hermes-wakeword-$V.zip" hey_hermes.tflite hey_hermes.json MODEL_NOTICE.md TRAINING_RESULTS.md)
(cd "$DIST" && shasum -a 256 ./*.zip > SHA256SUMS)
echo "built $V from $(git rev-parse --short HEAD):"
ls -l "$DIST"
