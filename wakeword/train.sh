#!/usr/bin/env bash
# One command: create a venv, install pinned deps, run every pipeline stage, write model/hey_hermes.{tflite,json}.
# Downloads about 33 GB of permissively licensed audio; needs about 90 GB free under wakeword/work.
# Usage: wakeword/train.sh [extra pipeline.py args, e.g. --my-voice wakeword/work/my_voice --steps 10000]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
"$PY" -c 'import sys, lzma; assert (3,10) <= sys.version_info[:2] <= (3,12), "need Python 3.10-3.12"' \
  || { echo "Need Python 3.10-3.12 built with lzma (e.g. 'uv python install 3.11', then PYTHON=\$(uv python find 3.11))"; exit 1; }
mkdir -p "$HERE/work/tmp"
export HF_HOME="${HF_HOME:-$HERE/work/hf-cache}" TMPDIR="${TMPDIR:-$HERE/work/tmp}"   # keep caches next to the data
VENV="$HERE/.venv"
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$HERE/work/pip-cache}"
"$VENV/bin/pip" install -q --upgrade pip
REQS=(-r "$HERE/requirements.txt")
if [ "$(uname -s)" = "Linux" ] && command -v nvidia-smi >/dev/null; then
  "$VENV/bin/pip" install -q "tensorflow[and-cuda]==2.21.*"   # GPU TensorFlow on Linux + NVIDIA (same minor as the macOS lock)
fi
"$VENV/bin/pip" install -q "${REQS[@]}"
MWW_SHA=4665173cd35f1cff9a61e06fc427f124766c488e
MWW_SRC="$HERE/work/src/microWakeWord-$MWW_SHA"
if [ ! -d "$MWW_SRC" ]; then
  mkdir -p "$HERE/work/src"
  curl -fsSL "https://github.com/kahrendt/microWakeWord/archive/$MWW_SHA.tar.gz" | tar -xz -C "$HERE/work/src"
fi
"$VENV/bin/pip" install -q -e "$MWW_SRC"   # editable: setup.py's find_packages() misses microwakeword/audio
"$VENV/bin/pip" freeze --exclude-editable > "$HERE/requirements.lock.$(uname -s | tr A-Z a-z)-$(uname -m).txt"
BATCH=50; command -v nvidia-smi >/dev/null && BATCH=100
"$VENV/bin/python" "$HERE/pipeline.py" all --batch "$BATCH" "$@"
echo "Done: $HERE/model/hey_hermes.tflite and hey_hermes.json"
