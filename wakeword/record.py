#!/usr/bin/env python3
"""Make clips of your own voice saying "hey hermes", for training the wake word on it.

Two ways, both write 16 kHz mono WAV clips to --out (default wakeword/work/my_voice):

  record.py split memo.m4a [more files...]   one long recording, cut where you pause
  record.py mic --count 60                   record from this computer's microphone, one prompt at a time

The easy one: on your phone, start a voice memo and say "Hey Hermes" 60-100 times with a short pause
(about a second) between, the way you'd really say it: near and across the room, quietly and loudly,
mumbled and clear, while walking around. Send the file to this computer and run `split` on it. Then:

  pipeline.py prepare --my-voice wakeword/work/my_voice  (and features, train, evaluate)

Your recordings stay on this computer (work/ is git-ignored). Never commit them.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile

HERE = Path(__file__).resolve().parent
RATE = 16000


def load_16k(path: Path) -> np.ndarray:
    """Any audio file (m4a, mp3, wav...) to 16 kHz mono float32, through ffmpeg when needed."""
    try:
        data, sr = soundfile.read(path, dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        if sr == RATE:
            return mono
    except Exception:
        pass
    if not shutil.which("ffmpeg"):
        sys.exit(f"{path}: needs ffmpeg to decode (brew install ffmpeg / apt install ffmpeg)")
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-ac", "1", "-ar", str(RATE), str(wav)],
                       check=True)
        data, _ = soundfile.read(wav, dtype="float32")
    return data


def segments(audio: np.ndarray, aggressiveness: int = 2, min_s: float = 0.4, max_s: float = 2.5,
             gap_s: float = 0.35) -> list[tuple[int, int]]:
    """Speech stretches separated by at least `gap_s` of silence (webrtcvad, 30 ms frames)."""
    import webrtcvad

    vad = webrtcvad.Vad(aggressiveness)
    frame = int(RATE * 0.03)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    voiced = [vad.is_speech(pcm[i : i + frame].tobytes(), RATE) for i in range(0, len(pcm) - frame, frame)]
    out, start, quiet = [], None, 0
    gap = int(gap_s / 0.03)
    for i, v in enumerate(voiced + [False] * (gap + 1)):
        if v:
            start = i if start is None else start
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet > gap:
                end = i - quiet + 1
                if min_s <= (end - start) * 0.03 <= max_s:
                    out.append((start * frame, end * frame))
                start, quiet = None, 0
    return out


def save_clip(out: Path, name: str, audio: np.ndarray, pad_s: float = 0.25) -> None:
    pad = np.zeros(int(pad_s * RATE), dtype=np.float32)
    clip = np.concatenate([pad, audio, pad])
    peak = float(np.max(np.abs(clip))) or 1.0
    soundfile.write(out / f"{name}.wav", clip / peak * 0.9 if peak < 0.05 else clip, RATE, subtype="PCM_16")


def cmd_split(a: argparse.Namespace) -> None:
    a.out.mkdir(parents=True, exist_ok=True)
    total = 0
    for f in a.files:
        audio = load_16k(f)
        segs = segments(audio)
        pad = int(0.25 * RATE)
        for i, (s, e) in enumerate(segs):
            save_clip(a.out, f"{f.stem}_{i:03d}", audio[max(0, s - pad // 2) : e + pad // 2], 0.1)
        print(f"{f.name}: {len(audio) / RATE:.0f} s, {len(segs)} clips")
        total += len(segs)
    print(f"{total} clips in {a.out}. Listen to a few; delete any that are not 'hey hermes'.")
    if total < 30:
        print("Fewer than 30: record more (60-100 is good) or leave longer pauses between them.")


def cmd_mic(a: argparse.Namespace) -> None:
    try:
        import sounddevice
    except ImportError:
        sys.exit("needs sounddevice: .venv/bin/pip install sounddevice")
    a.out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    hints = ["normally", "quietly", "from across the room", "a bit mumbled", "loudly", "fast", "slowly"]
    print(f"{a.count} clips. Say 'Hey Hermes' each time it says NOW. Ctrl-C stops (kept clips stay).")
    for i in range(a.count):
        input(f"[{i + 1}/{a.count}] Enter, then say it {hints[i % len(hints)]}... ")
        print("   NOW")
        rec = sounddevice.rec(int(2.5 * RATE), samplerate=RATE, channels=1, dtype="float32")
        sounddevice.wait()
        audio = rec[:, 0]
        segs = segments(audio, min_s=0.3, max_s=2.4, gap_s=0.5)
        if not segs:
            print("   heard nothing, try again")
            continue
        s, e = segs[0][0], segs[-1][1]
        save_clip(a.out, f"mic_{stamp}_{i:03d}", audio[max(0, s - 2000) : e + 2000], 0.1)
    print(f"done: {a.out}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=HERE / "work" / "my_voice")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split", help="cut one or more long recordings into clips")
    s.add_argument("files", nargs="+", type=Path)
    m = sub.add_parser("mic", help="record from this computer's microphone")
    m.add_argument("--count", type=int, default=60)
    a = p.parse_args()
    {"split": cmd_split, "mic": cmd_mic}[a.cmd](a)


if __name__ == "__main__":
    main()
