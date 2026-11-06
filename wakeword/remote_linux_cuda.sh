#!/usr/bin/env bash
# Train on a Linux + NVIDIA machine over SSH and copy the model back.
# Usage: wakeword/remote_linux_cuda.sh user@host [remote_dir] [extra pipeline.py args]
# Needs on the remote: python3.10-3.12 with venv, NVIDIA driver (nvidia-smi), ~75 GB free disk, rsync.
set -euo pipefail
HOST="${1:?usage: remote_linux_cuda.sh user@host [remote_dir] [args]}"; shift
RDIR="${1:-hermes-voice-wakeword}"; [ $# -gt 0 ] && shift
HERE="$(cd "$(dirname "$0")" && pwd)"
rsync -a --exclude work --exclude .venv --exclude model "$HERE/" "$HOST:$RDIR/"
ssh -t "$HOST" "cd $RDIR && nvidia-smi -L && bash train.sh $*"
mkdir -p "$HERE/model"
rsync -a "$HOST:$RDIR/model/" "$HERE/model/"
rsync -a "$HOST:$RDIR/requirements.lock."* "$HERE/" || true
echo "Copied: $HERE/model/hey_hermes.tflite + hey_hermes.json"
