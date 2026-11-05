#!/usr/bin/env python3
"""Reproducible "hey hermes" microWakeWord training pipeline.

Follows the upstream microWakeWord basic training notebook (kahrendt/microWakeWord @ 4665173,
notebooks/basic_training_notebook.ipynb) as a script with resumable stages. Every stage skips work
that is already done, so the whole thing can be re-run after an interruption.

Stages (run all with `pipeline.py all`, or one at a time):
  generate    synthetic positive clips with piper-sample-generator (LibriTTS-R generator)
  augdata     download augmentation audio: MIT RIRs, one AudioSet part, FMA xsmall (16 kHz wav)
  negatives   download pre-computed negative spectrogram features (kahrendt/microwakeword on HF)
  features    augment positives and write train/validation/test spectrogram mmaps
  train       train + quantize + stream-convert (python -m microwakeword.model_train_eval)
  export      copy the tflite and write the ESPHome/micro_wake_word v2 JSON manifest
  probe       tiny end-to-end timing run (few samples, few steps) to estimate full training time

All data lives under --work (default ./work). Licensing: the augmentation/negative data has mixed
licenses; upstream says models trained with it are for non-commercial personal use only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PSG_SHA = "2971426a55072f7d22fec416ca7800df8bd23207"  # rhasspy/piper-sample-generator (v3.2.0 + piper_train/)
PSG_TARBALL = f"https://github.com/rhasspy/piper-sample-generator/archive/{PSG_SHA}.tar.gz"
GENERATOR_URL = "https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/en_US-libritts_r-medium.pt"
NEG_ROOT = "https://huggingface.co/datasets/kahrendt/microwakeword/resolve/0da95f94302ca2f4aae3b18fc6560fa6d2bba3d1/"
NEG_FILES = ["dinner_party.zip", "dinner_party_eval.zip", "no_speech.zip", "speech.zip"]
# AudioSet moved from tar parts (the notebook's bal_train09.tar, now 404) to parquet shards; one shard, pinned revision.
AUDIOSET_REV = "0c609e8302cf139307f639c57652032af0a88041"
AUDIOSET_URL = f"https://huggingface.co/datasets/agkphysics/AudioSet/resolve/{AUDIOSET_REV}/data/bal_train/09.parquet"
FMA_URL = "https://huggingface.co/datasets/mchl914/fma_xsmall/resolve/bfef24fd1e1334e4631b917c98e0dbc3dd653ab2/fma_xs.zip"
MIT_RIR = ("davidscripka/MIT_environmental_impulse_responses", "b824a1ef2821f112fda0b9cb26e4278c62b425bb")

# Phonetic spellings help Piper say a new word naturally; several variants add diversity.
DEFAULT_PHRASES = ["hey hermes", "hey her mees", "hey hermiss"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log(f"download {url} -> {dest}")
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    tmp.rename(dest)
    return dest


def run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    log("$ " + " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=cwd, env=env)


def psg_source(work: Path) -> Path:
    """Source of piper-sample-generator at PSG_SHA. The PyPI wheel omits piper_train/, which the
    LibriTTS-R generator (.pt) needs, so the pinned source tree goes on PYTHONPATH."""
    src = work / "src" / f"piper-sample-generator-{PSG_SHA}"
    if not (src / "piper_train").exists():
        tgz = download(PSG_TARBALL, work / "src" / f"psg-{PSG_SHA[:8]}.tar.gz")
        run(["tar", "-xzf", str(tgz), "-C", str(tgz.parent)])
    return src


def stage_generate(work: Path, phrases: list[str], samples: int, batch: int) -> None:
    out = work / "generated_samples"
    have = len(list(out.glob("*.wav"))) if out.exists() else 0
    if have >= samples:
        log(f"generate: {have} samples already present")
        return
    src = psg_source(work)
    # The generator's .pt.json config ships in the source tree's models/; keep the .pt next to it.
    model = download(GENERATOR_URL, src / "models" / "en_US-libritts_r-medium.pt")
    env = {**os.environ, "PYTHONPATH": str(src) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    per = max(1, samples // len(phrases))
    for i, phrase in enumerate(phrases):
        sub = work / "generated_parts" / f"p{i}"
        if len(list(sub.glob("*.wav"))) >= per:
            continue
        run([sys.executable, "-m", "piper_sample_generator", phrase, "--model", str(model),
             "--max-samples", str(per), "--batch-size", str(batch), "--output-dir", str(sub)], env=env)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for sub in sorted((work / "generated_parts").glob("p*")):
        for wav in sorted(sub.glob("*.wav")):
            shutil.copy2(wav, out / f"{sub.name}_{wav.name}")
            n += 1
    log(f"generate: {n} positive clips in {out}")


def _to_16k_wavs(files: list[Path], out_dir: Path) -> None:
    import datasets
    import numpy as np
    import scipy.io.wavfile

    out_dir.mkdir(parents=True, exist_ok=True)
    ds = datasets.Dataset.from_dict({"audio": [str(p) for p in files]}).cast_column(
        "audio", datasets.Audio(sampling_rate=16000))
    for row in ds:
        name = Path(row["audio"]["path"]).stem + ".wav"
        scipy.io.wavfile.write(out_dir / name, 16000, (row["audio"]["array"] * 32767).astype(np.int16))


def _parquet_audio_to_16k(shard: Path, out_dir: Path) -> None:
    """Decode an HF audio parquet shard (struct{bytes,path}) to 16 kHz mono int16 wavs.
    Read with pyarrow directly: the shard's metadata uses feature types newer `datasets` versions write."""
    import io
    from math import gcd

    import numpy as np
    import pyarrow.parquet as pq
    import scipy.io.wavfile
    import scipy.signal
    import soundfile

    pf = pq.ParquetFile(shard)
    n = 0
    for batch in pf.iter_batches(batch_size=64, columns=["video_id", "audio"]):
        for vid, audio in zip(batch.column("video_id").to_pylist(), batch.column("audio").to_pylist()):
            try:
                data, sr = soundfile.read(io.BytesIO(audio["bytes"]), dtype="float32", always_2d=True)
            except Exception:  # undecodable clip: skip
                continue
            mono = data.mean(axis=1)
            if sr != 16000:
                g = gcd(int(sr), 16000)
                mono = scipy.signal.resample_poly(mono, 16000 // g, int(sr) // g)
            scipy.io.wavfile.write(out_dir / f"{vid}.wav", 16000, (np.clip(mono, -1, 1) * 32767).astype(np.int16))
            n += 1
    log(f"augdata: {n} AudioSet clips")


def stage_augdata(work: Path) -> None:
    import datasets
    import numpy as np
    import scipy.io.wavfile

    rir = work / "mit_rirs"
    if not rir.exists() or not any(rir.iterdir()):
        rir.mkdir(parents=True, exist_ok=True)
        log("augdata: MIT environmental impulse responses")
        for row in datasets.load_dataset(MIT_RIR[0], revision=MIT_RIR[1], split="train", streaming=True):
            name = row["audio"]["path"].split("/")[-1]
            scipy.io.wavfile.write(rir / name, 16000, (row["audio"]["array"] * 32767).astype(np.int16))
    a16 = work / "audioset_16k"
    if not a16.exists() or not any(a16.iterdir()):
        shard = download(AUDIOSET_URL, work / "audioset" / "bal_train_09.parquet")
        log("augdata: AudioSet shard -> 16 kHz wav")
        a16.mkdir(parents=True, exist_ok=True)
        _parquet_audio_to_16k(shard, a16)
    f16 = work / "fma_16k"
    if not f16.exists() or not any(f16.iterdir()):
        z = download(FMA_URL, work / "fma" / "fma_xs.zip")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(z.parent)
        _to_16k_wavs(sorted((work / "fma" / "fma_small").glob("**/*.mp3")), f16)
    log("augdata: ready")


def stage_negatives(work: Path, only: list[str] | None = None) -> None:
    neg = work / "negative_datasets"
    for fname in only or NEG_FILES:
        target = neg / fname.removesuffix(".zip")
        if target.exists():
            continue
        z = download(NEG_ROOT + fname, neg / fname)
        log(f"negatives: unzip {fname}")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(neg)
        z.unlink()
    log("negatives: ready")


def stage_features(work: Path, background: list[str]) -> None:
    from mmap_ninja.ragged import RaggedMmap
    from microwakeword.audio.augmentation import Augmentation
    from microwakeword.audio.clips import Clips
    from microwakeword.audio.spectrograms import SpectrogramGeneration

    out = work / "generated_augmented_features"
    if (out / "testing" / "wakeword_mmap").exists():
        log("features: already generated")
        return
    clips = Clips(input_directory=str(work / "generated_samples"), file_pattern="*.wav",
                  max_clip_duration_s=None, remove_silence=False, random_split_seed=10, split_count=0.1)
    augmenter = Augmentation(
        augmentation_duration_s=3.2,
        augmentation_probabilities={"SevenBandParametricEQ": 0.1, "TanhDistortion": 0.1, "PitchShift": 0.1,
                                    "BandStopFilter": 0.1, "AddColorNoise": 0.1, "AddBackgroundNoise": 0.75,
                                    "Gain": 1.0, "RIR": 0.5},
        impulse_paths=[str(work / "mit_rirs")],
        background_paths=[str(work / b) for b in background],
        background_min_snr_db=-5, background_max_snr_db=10, min_jitter_s=0.195, max_jitter_s=0.205)
    for split, split_name, repeat, slide in (("training", "train", 2, 10), ("validation", "validation", 1, 10),
                                             ("testing", "test", 1, 1)):
        d = out / split
        d.mkdir(parents=True, exist_ok=True)
        spec = SpectrogramGeneration(clips=clips, augmenter=augmenter, slide_frames=slide, step_ms=10)
        RaggedMmap.from_generator(out_dir=str(d / "wakeword_mmap"),
                                  sample_generator=spec.spectrogram_generator(split=split_name, repeat=repeat),
                                  batch_size=100, verbose=True)
    log("features: done")


def training_config(work: Path, steps: int, negatives: list[str]) -> Path:
    import yaml

    weights = {"speech": 10.0, "dinner_party": 10.0, "no_speech": 5.0}
    feats = [{"features_dir": str(work / "generated_augmented_features"), "sampling_weight": 2.0,
              "penalty_weight": 1.0, "truth": True, "truncation_strategy": "truncate_start", "type": "mmap"}]
    for n in negatives:
        if n == "dinner_party_eval":
            feats.append({"features_dir": str(work / "negative_datasets" / n), "sampling_weight": 0.0,
                          "penalty_weight": 1.0, "truth": False, "truncation_strategy": "split", "type": "mmap"})
        else:
            feats.append({"features_dir": str(work / "negative_datasets" / n), "sampling_weight": weights[n],
                          "penalty_weight": 1.0, "truth": False, "truncation_strategy": "random", "type": "mmap"})
    cfg = {"window_step_ms": 10, "train_dir": str(work / "trained_models" / "hey_hermes"), "features": feats,
           "training_steps": [steps], "positive_class_weight": [1], "negative_class_weight": [20],
           "learning_rates": [0.001], "batch_size": 128, "time_mask_max_size": [0], "time_mask_count": [0],
           "freq_mask_max_size": [0], "freq_mask_count": [0], "eval_step_interval": min(500, max(10, steps // 4)),
           "clip_duration_ms": 1500, "target_minimization": 0.9, "minimization_metric": None,
           "maximization_metric": "average_viable_recall"}
    path = work / "training_parameters.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


def stage_train(work: Path, steps: int, negatives: list[str]) -> None:
    cfg = training_config(work, steps, negatives)
    run([sys.executable, "-m", "microwakeword.model_train_eval", f"--training_config={cfg}",
         "--train", "1", "--restore_checkpoint", "1", "--test_tf_nonstreaming", "0",
         "--test_tflite_nonstreaming", "0", "--test_tflite_nonstreaming_quantized", "0",
         "--test_tflite_streaming", "0", "--test_tflite_streaming_quantized", "1", "--use_weights", "best_weights",
         "mixednet", "--pointwise_filters", "64,64,64,64", "--repeat_in_block", "1, 1, 1, 1",
         "--mixconv_kernel_sizes", "[5], [7,11], [9,15], [23]", "--residual_connection", "0,0,0,0",
         "--first_conv_filters", "32", "--first_conv_kernel_size", "5", "--stride", "3"])


def stage_export(work: Path, out: Path, cutoff: float, arena: int) -> None:
    src = work / "trained_models" / "hey_hermes" / "tflite_stream_state_internal_quant" / "stream_state_internal_quant.tflite"
    if not src.exists():
        sys.exit(f"export: no trained model at {src}")
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, out / "hey_hermes.tflite")
    manifest = {"type": "micro", "wake_word": "Hey Hermes", "author": "hermes-voice contributors",
                "website": "https://github.com/majorminorlabs/hermes-voice", "model": "hey_hermes.tflite",
                "trained_languages": ["en"], "version": 2,
                "micro": {"probability_cutoff": cutoff, "feature_step_size": 10, "sliding_window_size": 5,
                          "tensor_arena_size": arena, "minimum_esphome_version": "2024.7.0"}}
    (out / "hey_hermes.json").write_text(json.dumps(manifest, indent=2) + "\n")
    log(f"export: {out / 'hey_hermes.tflite'} + hey_hermes.json")


def stage_probe(work: Path) -> None:
    """Small timing run: 40 clips, eval negatives only, 40 training steps."""
    steps = [
        ("generate_40_clips_s", lambda: stage_generate(work, DEFAULT_PHRASES[:1], 40, 8)),
        ("augdata_light_s", lambda: stage_augdata_light(work)),
        ("negatives_eval_only_s", lambda: stage_negatives(work, ["dinner_party_eval.zip"])),
        ("features_40_clips_s", lambda: stage_features(work, ["fma_16k"])),
        ("train_40_steps_s", lambda: stage_train(work, 40, ["dinner_party_eval"])),
    ]
    t: dict[str, float] = {}
    for name, fn in steps:
        t0 = time.time()
        fn()
        t[name] = round(time.time() - t0, 1)
    (work / "probe_timings.json").write_text(json.dumps(t, indent=2))
    log(f"probe timings: {json.dumps(t)}")


def stage_augdata_light(work: Path) -> None:
    """Probe-only augmentation data: MIT RIRs + FMA xsmall (skips the AudioSet part)."""
    import datasets
    import numpy as np
    import scipy.io.wavfile

    rir = work / "mit_rirs"
    if not rir.exists() or not any(rir.iterdir()):
        rir.mkdir(parents=True, exist_ok=True)
        for row in datasets.load_dataset(MIT_RIR[0], revision=MIT_RIR[1], split="train", streaming=True):
            scipy.io.wavfile.write(rir / row["audio"]["path"].split("/")[-1], 16000,
                                   (row["audio"]["array"] * 32767).astype(np.int16))
    f16 = work / "fma_16k"
    if not f16.exists() or not any(f16.iterdir()):
        z = download(FMA_URL, work / "fma" / "fma_xs.zip")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(z.parent)
        _to_16k_wavs(sorted((work / "fma" / "fma_small").glob("**/*.mp3")), f16)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", choices=["all", "generate", "augdata", "negatives", "features", "train", "export", "probe"])
    p.add_argument("--work", type=Path, default=HERE / "work")
    p.add_argument("--out", type=Path, default=HERE / "model")
    p.add_argument("--samples", type=int, default=3000, help="positive clips (upstream notebook: 1000)")
    p.add_argument("--batch", type=int, default=50, help="piper generation batch size (GPU: 100)")
    p.add_argument("--steps", type=int, default=10000)
    p.add_argument("--phrase", action="append", help="phrase(s) to synthesize; default: hey hermes + phonetic variants")
    p.add_argument("--cutoff", type=float, default=0.97, help="probability_cutoff written to the manifest")
    p.add_argument("--arena", type=int, default=30000,
                   help="tensor_arena_size for the manifest (hey_jarvis, same architecture, uses 22860; headroom added)")
    a = p.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    phrases = a.phrase or DEFAULT_PHRASES
    negs = ["speech", "dinner_party", "no_speech", "dinner_party_eval"]
    if a.stage in ("generate", "all"):
        stage_generate(a.work, phrases, a.samples, a.batch)
    if a.stage in ("augdata", "all"):
        stage_augdata(a.work)
    if a.stage in ("negatives", "all"):
        stage_negatives(a.work)
    if a.stage in ("features", "all"):
        stage_features(a.work, ["fma_16k", "audioset_16k"])
    if a.stage in ("train", "all"):
        stage_train(a.work, a.steps, negs)
    if a.stage in ("export", "all"):
        stage_export(a.work, a.out, a.cutoff, a.arena)
    if a.stage == "probe":
        stage_probe(a.work)


if __name__ == "__main__":
    main()
