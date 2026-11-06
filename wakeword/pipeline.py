#!/usr/bin/env python3
"""Reproducible "hey hermes" microWakeWord training pipeline, on permissively licensed data only.

Follows the upstream microWakeWord basic training notebook (kahrendt/microWakeWord @ 4665173,
notebooks/basic_training_notebook.ipynb) as a script with resumable stages, but builds every negative
and augmentation set itself from data whose license allows any use (see DATA below and
model/MODEL_NOTICE.md). Every stage skips work that is already done, so it can be re-run after an
interruption.

Stages (run all with `pipeline.py all`, or one at a time):
  download    the datasets in DATA, from their publishers (about 33 GB)
  generate    synthetic "hey hermes" clips and near-miss phrases (piper-sample-generator, LibriTTS-R)
  prepare     unpack and sort the audio into sets (licenses checked per file where they differ)
  features    augment and write spectrogram features for every set
  train       train + quantize + stream-convert (python -m microwakeword.model_train_eval)
  evaluate    the streaming model on real recordings: your held-out clips and party audio it never heard
  export      copy the tflite and write the ESPHome/micro_wake_word v2 JSON manifest

Your own voice (optional, recommended): put clips of you saying "hey hermes" in --my-voice (record.py
makes them); 20 % are held out to test the model on a real voice.

All data lives under --work (default ./work/permissive).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PSG_SHA = "2971426a55072f7d22fec416ca7800df8bd23207"  # rhasspy/piper-sample-generator (MIT)
PSG_TARBALL = f"https://github.com/rhasspy/piper-sample-generator/archive/{PSG_SHA}.tar.gz"
# Exported from rhasspy/piper-checkpoints en/en_US/libritts_r/medium (MIT; trained from scratch on
# LibriTTS-R, CC BY 4.0). Not the piper-voices model of the same name, which was fine-tuned from lessac.
GENERATOR_URL = "https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/en_US-libritts_r-medium.pt"

OPENSLR = "https://openslr.elda.org/resources/"  # the EU mirror: about 25x faster than the others in a test
# name: (url, md5 or None, license)
DATA = {
    "musan.tar.gz": (OPENSLR + "17/musan.tar.gz", None, "CC BY 4.0 (per-file licenses checked)"),
    "rirs_noises.zip": (OPENSLR + "28/rirs_noises.zip", None, "Apache 2.0 (simulated RIRs only)"),
    "train-clean-100.tar.gz": (OPENSLR + "12/train-clean-100.tar.gz", None, "CC BY 4.0"),
    "dev-other.tar.gz": (OPENSLR + "12/dev-other.tar.gz", None, "CC BY 4.0"),
    "DipCo.tgz": ("https://zenodo.org/api/records/8122551/files/DipCo.tgz/content",
                  "2297eb9334f3b90e02b54b708e501b24", "CDLA-Permissive-1.0"),
}

# Phonetic spellings help Piper say a new word naturally; several variants add diversity.
DEFAULT_PHRASES = ["hey hermes", "hey her mees", "hey hermiss"]
# Near misses, trained as "not the wake word" (upstream does the same with adversarial phrases).
NEAR_MISSES = [
    "hey hermit", "hey herman", "hey her mess", "hey germs", "hey hershey", "hey mercy", "hey there",
    "hermes", "hey her", "hey herbs", "hey hammers", "hey firms", "hey terms", "hey humans", "hey homies",
    "hey thermos", "hey heroes", "a hermes bag", "hey hare miss", "hey her nieces",
]
# MUSAN's LICENSE files are free-text blocks (file ids, then the license) separated by "=====" lines.
# Kept: CC BY, CC0, public domain, US government. Left out: NonCommercial, NoDerivatives and ShareAlike
# (ShareAlike could carry over to the model), and any block whose license is not clear.
_MUSAN_ID = re.compile(r"\b((?:music|noise)-[a-z0-9-]+-\d{4})\b")
_LIC_BAD = re.compile(r"by-nc|noncommercial|non-commercial|\bnc\b|by-nd|noderiv|no deriv|\bnd\b|by-sa|sharealike|"
                      r"share-alike|share alike|\bsa\b", re.IGNORECASE)
_LIC_GOOD = re.compile(r"\bcc0\b|public domain|\bcc[- ]by\b|attribution \d|attribution license|"
                       r"creative commons attribution|u\.?s\.? government", re.IGNORECASE)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def download(url: str, dest: Path, md5: str | None = None) -> Path:
    if not (dest.exists() and dest.stat().st_size > 0):
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        log(f"download {url} -> {dest}")
        run(["curl", "-fL", "--retry", "10", "-C", "-", "-o", str(tmp), url])
        tmp.rename(dest)
    if md5:
        h = hashlib.md5()
        with open(dest, "rb") as f:
            for block in iter(lambda: f.read(1 << 22), b""):
                h.update(block)
        if h.hexdigest() != md5:
            sys.exit(f"{dest}: md5 {h.hexdigest()} != {md5}; delete it and download again")
    return dest


def run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    log("$ " + " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=cwd, env=env)


def write_wav(path: Path, audio, rate: int = 16000) -> None:
    import numpy as np
    import scipy.io.wavfile

    scipy.io.wavfile.write(path, rate, (np.clip(audio, -1, 1) * 32767).astype(np.int16))


# ------------------------------------------------------------------------------------------ download --


def stage_download(work: Path) -> None:
    for name, (url, md5, lic) in DATA.items():
        log(f"{name}: {lic}")
        download(url, work / "downloads" / name, md5)


# ------------------------------------------------------------------------------------------ generate --


def psg_source(work: Path) -> Path:
    """Source of piper-sample-generator at PSG_SHA. The PyPI wheel omits piper_train/, which the
    LibriTTS-R generator (.pt) needs, so the pinned source tree goes on PYTHONPATH."""
    src = work / "src" / f"piper-sample-generator-{PSG_SHA}"
    if not (src / "piper_train").exists():
        tgz = download(PSG_TARBALL, work / "src" / f"psg-{PSG_SHA[:8]}.tar.gz")
        run(["tar", "-xzf", str(tgz), "-C", str(tgz.parent)])
    return src


def _synthesize(work: Path, phrases: list[str], total: int, batch: int, out: Path, tag: str) -> None:
    have = len(list(out.glob("*.wav"))) if out.exists() else 0
    if have >= total:
        log(f"generate: {have} {tag} clips already present")
        return
    src = psg_source(work)
    model = download(GENERATOR_URL, src / "models" / "en_US-libritts_r-medium.pt")
    env = {**os.environ, "PYTHONPATH": str(src) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    per = max(1, total // len(phrases))
    parts = work / "generated_parts" / tag
    for i, phrase in enumerate(phrases):
        sub = parts / f"p{i}"
        if len(list(sub.glob("*.wav"))) >= per:
            continue
        run([sys.executable, "-m", "piper_sample_generator", phrase, "--model", str(model),
             "--max-samples", str(per), "--batch-size", str(batch), "--output-dir", str(sub)], env=env)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for sub in sorted(parts.glob("p*")):
        for wav in sorted(sub.glob("*.wav")):
            shutil.copy2(wav, out / f"{sub.name}_{wav.name}")
            n += 1
    log(f"generate: {n} {tag} clips in {out}")


def stage_generate(work: Path, phrases: list[str], samples: int, batch: int, near: int) -> None:
    _synthesize(work, phrases, samples, batch, work / "sets" / "positive", "positive")
    _synthesize(work, NEAR_MISSES, near, batch, work / "sets" / "near_miss", "near_miss")


# ------------------------------------------------------------------------------------------- prepare --


def _musan_allowed(root: Path, audit: Path) -> list[Path]:
    """MUSAN noise and music files whose own license allows any use (see _LIC_*). Writes every decision
    to `audit` (file, kept/left out, the license text) so the selection can be checked by hand."""
    keep: list[Path] = []
    rows, counts = [], {"kept": 0, "left out": 0}
    for lic_file in sorted(root.glob("musan/*/*/LICENSE")):
        text = lic_file.read_text(errors="replace")
        blocks = re.split(r"^=+\s*$", text, flags=re.MULTILINE)
        # A block with a license and no file ids is the folder's license (rfm: "CC BY 3.0" at the top;
        # free-sound: one sentence, "marked as in the Public Domain"). It covers files listed without one.
        folder = " ".join(b.strip() for b in blocks if not _MUSAN_ID.search(b) and (_LIC_GOOD.search(b) or _LIC_BAD.search(b)))
        listed: set[str] = set()
        entries: list[tuple[list[str], str]] = []
        for block in blocks:
            ids = _MUSAN_ID.findall(block)
            if not ids:
                continue
            own = _LIC_GOOD.search(block) or _LIC_BAD.search(block)
            entries.append((ids, block if own else folder))
            listed.update(ids)
        if not entries and folder:  # no file list at all: the statement covers the whole folder
            entries.append(([w.stem for w in sorted(lic_file.parent.glob("*.wav"))], folder))
        for ids, lic_text in entries:
            lic = " ".join(ln.strip() for ln in lic_text.splitlines() if ln.strip() and not _MUSAN_ID.fullmatch(ln.strip()))
            ok = bool(_LIC_GOOD.search(lic_text)) and not _LIC_BAD.search(lic_text)
            for i in ids:
                wav = lic_file.parent / f"{i}.wav"
                if not wav.exists():
                    continue
                verdict = "kept" if ok else "left out"
                counts[verdict] += 1
                rows.append(f"{lic_file.parent.relative_to(root)}/{i}.wav\t{verdict}\t{lic[:200]}")
                if ok:
                    keep.append(wav)
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text("file\tdecision\tlicense text\n" + "\n".join(rows) + "\n")
    unlisted = [w for w in root.glob("musan/*/*/*.wav") if w.parts[-3] != "speech" and w not in set(keep)]
    log(f"prepare: MUSAN {counts['kept']} kept, {counts['left out']} left out by license, "
        f"{len(unlisted) - counts['left out']} without a license entry (left out); audit: {audit}")
    if not keep:
        sys.exit("prepare: no MUSAN file passed the license check; look at musan/*/*/LICENSE")
    return keep


def _chunks(src: Path, out_dir: Path, seconds: float, prefix: str) -> int:
    """Cut a long 16 kHz recording into chunks (augmentation loads whole files)."""
    import soundfile

    data, sr = soundfile.read(src, dtype="float32", always_2d=True)
    mono = data[:, 0]
    if sr != 16000:
        sys.exit(f"{src}: expected 16 kHz, got {sr}")
    step = int(seconds * 16000)
    n = 0
    for i in range(0, len(mono) - step // 2, step):
        write_wav(out_dir / f"{prefix}_{i // step:04d}.wav", mono[i : i + step])
        n += 1
    return n


def _link_all(files: list[Path], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        dst = out_dir / f"{f.parent.name}_{f.name}"
        if not dst.exists():
            dst.symlink_to(f)


def stage_prepare(work: Path, limit: int | None, my_voice: Path | None) -> None:
    raw, sets, dl = work / "raw", work / "sets", work / "downloads"
    done = sets / ".prepared"
    if done.exists() and not my_voice:
        log("prepare: already done")
        return
    rnd = random.Random(10)
    raw.mkdir(parents=True, exist_ok=True)
    # Unpack (skips what is there).
    if not (raw / "musan").exists():
        run(["tar", "-xzf", str(dl / "musan.tar.gz"), "-C", str(raw), "musan/noise", "musan/music"])
    if not (raw / "RIRS_NOISES" / "simulated_rirs").exists():
        with zipfile.ZipFile(dl / "rirs_noises.zip") as zf:  # the recorded RIRs come from other databases: not used
            zf.extractall(raw, [m for m in zf.namelist() if "/simulated_rirs/" in m or m.endswith("LICENSE")])
    for name in ("train-clean-100", "dev-other"):
        if not (raw / "LibriSpeech" / name).exists():
            run(["tar", "-xzf", str(dl / f"{name}.tar.gz"), "-C", str(raw)])
    if not (raw / "Dipco").exists() and not (raw / "DipCo").exists():
        run(["tar", "-xzf", str(dl / "DipCo.tgz"), "-C", str(raw)])
    dipco = next(p for p in (raw / "Dipco", raw / "DipCo") if p.exists())

    # Room impulse responses: a fixed sample of the simulated rooms.
    rirs = sorted((raw / "RIRS_NOISES" / "simulated_rirs").glob("**/*.wav"))
    _link_all(rnd.sample(rirs, min(3000, len(rirs))), sets / "rirs")

    # Noise and music: 90 % for training and augmentation, 10 % held out.
    musan = _musan_allowed(raw, sets / "musan_licenses.tsv")
    rnd.shuffle(musan)
    cut = len(musan) // 10
    _link_all(musan[cut:][:limit], sets / "noise_train")
    _link_all(musan[:cut][:limit], sets / "noise_test")

    # Speech: LibriSpeech, whole utterances.
    speech = sorted((raw / "LibriSpeech").glob("*/*/*/*.flac"))
    rnd.shuffle(speech)
    _link_all(speech[: limit or 8000], sets / "speech")  # about 27 hours

    # Party: DiPCo far-field, one microphone per array. Dev sessions train (arrays U02-U05) and validate
    # (U01); eval sessions are only ever used to test.
    audio = dipco / "audio"
    party = sets / "party_train"
    party.mkdir(parents=True, exist_ok=True)
    if not any(party.iterdir()):
        for f in sorted((audio / "dev").glob("*_U0[2-5].CH1.wav"))[: limit or None]:
            _chunks(f, party, 30, f.stem)
    for split, src_split, unit in (("ambient_validation", "dev", "U01"), ("ambient_test", "eval", "U01")):
        d = sets / split
        d.mkdir(parents=True, exist_ok=True)
        if not any(d.iterdir()):
            for f in sorted((audio / src_split).glob(f"*_{unit}.CH1.wav")):
                _chunks(f, d, 600, f.stem)  # 10-minute pieces: counted as hours of audio

    # Your voice: 80 % train, 20 % test (never trained on).
    if my_voice:
        clips = sorted(p for p in my_voice.glob("*.wav"))
        if len(clips) < 10:
            sys.exit(f"--my-voice {my_voice}: need at least 10 clips, found {len(clips)}")
        rnd.shuffle(clips)
        cut = max(2, len(clips) // 5)
        for d, files in (("my_voice_test", clips[:cut]), ("my_voice_train", clips[cut:])):
            shutil.rmtree(sets / d, ignore_errors=True)
            (sets / d).mkdir(parents=True)
            for f in files:
                shutil.copy2(f, sets / d / f.name)
        log(f"prepare: your voice: {len(clips) - cut} clips to train, {cut} to test")
    done.write_text(time.strftime("%Y-%m-%d %H:%M:%S\n"))
    log("prepare: done")


# ------------------------------------------------------------------------------------------ features --


def _augmenter(work: Path, duration: float | None, background: float):
    from microwakeword.audio.augmentation import Augmentation

    return Augmentation(
        augmentation_duration_s=duration,
        augmentation_probabilities={"SevenBandParametricEQ": 0.1, "TanhDistortion": 0.1, "PitchShift": 0.1,
                                    "BandStopFilter": 0.1, "AddColorNoise": 0.1, "AddBackgroundNoise": background,
                                    "Gain": 1.0, "RIR": 0.5},
        impulse_paths=[str(work / "sets" / "rirs")],
        background_paths=[str(work / "sets" / "noise_train"), str(work / "sets" / "party_train")],
        background_min_snr_db=-5, background_max_snr_db=10, min_jitter_s=0.195, max_jitter_s=0.205)


def _mmap(out: Path, generator) -> None:
    from mmap_ninja.ragged import RaggedMmap

    out.parent.mkdir(parents=True, exist_ok=True)
    RaggedMmap.from_generator(out_dir=str(out), sample_generator=generator, batch_size=100, verbose=True)


def _clip_features(work: Path, name: str, src: Path, splits: tuple[tuple[str, str, int, int], ...]) -> None:
    """Short clips (wake word, near misses, your voice): fixed 3.2 s, augmented, slid like the notebook."""
    from microwakeword.audio.clips import Clips
    from microwakeword.audio.spectrograms import SpectrogramGeneration

    out = work / "features" / name
    if (out / ".done").exists():
        log(f"features: {name} already done")
        return
    clips = Clips(input_directory=str(src), file_pattern="*.wav", max_clip_duration_s=None, remove_silence=False,
                  random_split_seed=10, split_count=0.1)
    aug = _augmenter(work, 3.2, 0.75)
    for folder, split, repeat, slide in splits:
        spec = SpectrogramGeneration(clips=clips, augmenter=aug, slide_frames=slide, step_ms=10)
        _mmap(out / folder / f"{name}_mmap", spec.spectrogram_generator(split=split, repeat=repeat))
    (out / ".done").write_text("")
    log(f"features: {name} done")


def _long_features(work: Path, name: str, src: Path, pattern: str, folder: str, augment: bool,
                   shard: tuple[int, int] | None = None) -> None:
    """Long audio (speech, noise, party): augmented as a whole, cut into 3 s spectrograms; the ambient
    sets stay whole and unaugmented (the trainer splits them and counts false accepts per hour)."""
    from microwakeword.audio.clips import Clips
    from microwakeword.audio.spectrograms import SpectrogramGeneration

    out = work / "features" / name
    tag = f"{name}_{shard[0]}" if shard else name
    done = out / (f".done_{shard[0]}" if shard else ".done")
    if done.exists():
        log(f"features: {tag} already done")
        return
    if shard:  # every n-th file, in its own folder of links, so shards can run as separate processes
        k, n = shard
        files = sorted(src.glob(pattern))[k::n]
        src = work / "sets" / f"{name}_shard{k}"
        shutil.rmtree(src, ignore_errors=True)
        src.mkdir(parents=True)
        for f in files:
            (src / f.name).symlink_to(f.resolve())
    clips = Clips(input_directory=str(src), file_pattern=pattern, max_clip_duration_s=None, remove_silence=False)
    if augment:
        spec = SpectrogramGeneration(clips=clips, augmenter=_augmenter(work, None, 0.3),
                                     split_spectrogram_duration_s=3.0, step_ms=10)
    else:
        spec = SpectrogramGeneration(clips=clips, augmenter=None, step_ms=10)
    _mmap(out / folder / f"{tag}_mmap", spec.spectrogram_generator())
    done.write_text("")
    log(f"features: {tag} done")


FEATURE_SETS = ("positive", "near_miss", "my_voice", "speech", "noise", "party", "ambient")


def stage_features(work: Path, only: list[str] | None, shard: tuple[int, int] | None = None) -> None:
    sets = work / "sets"
    want = only or list(FEATURE_SETS)
    clip_splits = (("training", "train", 2, 10), ("validation", "validation", 1, 10), ("testing", "test", 1, 1))
    if "positive" in want:
        _clip_features(work, "positive", sets / "positive", clip_splits)
    if "near_miss" in want:
        _clip_features(work, "near_miss", sets / "near_miss", clip_splits)
    if "my_voice" in want and (sets / "my_voice_train").exists():
        # Few clips: each is augmented many times. The held-out 20 % is tested by `evaluate`, not here.
        _clip_features(work, "my_voice", sets / "my_voice_train", (("training", None, 40, 10),))
    if "speech" in want:
        if shard:
            _long_features(work, "speech", sets / "speech", "*.flac", "training", True, shard)
        else:  # the slowest set (about 1 s per 3 s piece): 4 processes, one quarter each
            procs = [subprocess.Popen([sys.executable, __file__, "features", "--work", str(work), "--only", "speech",
                                       "--shard", f"{k}/4"]) for k in range(4)]
            if any(p.wait() for p in procs):
                sys.exit("features: a speech shard failed")
            (work / "features" / "speech" / ".done").write_text("")
    if "noise" in want:
        _long_features(work, "noise", sets / "noise_train", "*.wav", "training", True)
    if "party" in want:
        _long_features(work, "party", sets / "party_train", "*.wav", "training", True)
    if "ambient" in want:
        _long_features(work, "ambient_validation", sets / "ambient_validation", "*.wav", "validation_ambient", False)
        _long_features(work, "ambient_test", sets / "ambient_test", "*.wav", "testing_ambient", False)


# --------------------------------------------------------------------------------------------- train --


def training_config(work: Path, steps: list[int], lrs: list[float], run_name: str, neg_weight: float = 20,
                    near_weight: float = 3.0) -> Path:
    import yaml

    f = work / "features"

    def neg(name: str, weight: float, strategy: str = "random") -> dict:
        return {"features_dir": str(f / name), "sampling_weight": weight, "penalty_weight": 1.0, "truth": False,
                "truncation_strategy": strategy, "type": "mmap"}

    feats = [{"features_dir": str(f / "positive"), "sampling_weight": 2.0, "penalty_weight": 1.0, "truth": True,
              "truncation_strategy": "truncate_start", "type": "mmap"}]
    if (f / "my_voice" / ".done").exists():
        feats.append({"features_dir": str(f / "my_voice"), "sampling_weight": 1.0, "penalty_weight": 1.0,
                      "truth": True, "truncation_strategy": "truncate_start", "type": "mmap"})
    feats += [neg("near_miss", near_weight, "truncate_start"), neg("speech", 10.0), neg("party", 10.0), neg("noise", 5.0),
              neg("ambient_validation", 0.0, "split"), neg("ambient_test", 0.0, "split")]
    cfg = {"window_step_ms": 10, "train_dir": str(work / "trained_models" / run_name), "features": feats,
           "training_steps": steps, "positive_class_weight": [1] * len(steps), "negative_class_weight": [neg_weight] * len(steps),
           "learning_rates": lrs, "batch_size": 128,
           "time_mask_max_size": [0] * len(steps), "time_mask_count": [0] * len(steps),
           "freq_mask_max_size": [0] * len(steps), "freq_mask_count": [0] * len(steps),
           "eval_step_interval": min(500, max(10, sum(steps) // 4)),
           "clip_duration_ms": 1500, "target_minimization": 0.9, "minimization_metric": None,
           "maximization_metric": "average_viable_recall"}
    path = work / f"training_parameters-{run_name}.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


def stage_train(work: Path, steps: list[int], lrs: list[float], run_name: str, neg_weight: float = 20,
                near_weight: float = 3.0) -> None:
    cfg = training_config(work, steps, lrs, run_name, neg_weight, near_weight)
    run([sys.executable, "-m", "microwakeword.model_train_eval", f"--training_config={cfg}",
         "--train", "1", "--restore_checkpoint", "1", "--test_tf_nonstreaming", "0",
         "--test_tflite_nonstreaming", "0", "--test_tflite_nonstreaming_quantized", "0",
         "--test_tflite_streaming", "0", "--test_tflite_streaming_quantized", "1", "--use_weights", "best_weights",
         "mixednet", "--pointwise_filters", "64,64,64,64", "--repeat_in_block", "1, 1, 1, 1",
         "--mixconv_kernel_sizes", "[5], [7,11], [9,15], [23]", "--residual_connection", "0,0,0,0",
         "--first_conv_filters", "32", "--first_conv_kernel_size", "5", "--stride", "3"])


# ------------------------------------------------------------------------------------------ evaluate --


def _model_path(work: Path, run_name: str = "hey_hermes") -> Path:
    return work / "trained_models" / run_name / "tflite_stream_state_internal_quant" / "stream_state_internal_quant.tflite"


def _streaming_probs(model_path: Path, audio) -> list[float]:
    """Run the quantized streaming model over 16 kHz audio with upstream's own inference helper (the way
    a device feeds it), then average over a window of 5 outputs like micro_wake_word does."""
    import numpy as np
    from microwakeword.inference import Model

    # A fresh interpreter per file: the streaming model keeps state from one call to the next.
    raw = Model(str(model_path)).predict_clip(np.asarray(audio, dtype=np.float32), step_ms=10)
    flat = np.asarray(raw, dtype=np.float32).reshape(-1)
    if len(flat) < 5:
        return [float(x) for x in flat]
    window = np.convolve(flat, np.ones(5) / 5, mode="valid")
    return [float(x) for x in window]


def _held_out(folder: Path) -> list[Path]:
    """The 20 % of clips microWakeWord's Clips keeps out of training (its seeded split, seed 10). Its further
    split into validation and test has no seed, so both halves are used: the same clips every run."""
    from microwakeword.audio.clips import Clips

    clips = Clips(input_directory=str(folder), file_pattern="*.wav", max_clip_duration_s=None, remove_silence=False,
                  random_split_seed=10, split_count=0.1)
    import datasets

    rows = [r for part in ("validation", "test")
            for r in clips.split_clips[part].cast_column("audio", datasets.Audio(decode=False))["audio"]]
    return sorted(Path(r["path"]) for r in rows)


def stage_evaluate(work: Path, cutoffs: list[float], model: Path | None = None, out_name: str = "evaluation.json") -> dict:
    """Real-audio checks the trainer's numbers cannot give: false rejects on your held-out voice clips,
    false accepts per hour on DiPCo's eval sessions (never trained on) and on held-out noise/music."""
    import numpy as np
    import soundfile

    model = model or _model_path(work)
    if not model.exists():
        sys.exit(f"evaluate: no trained model at {model}")
    sets = work / "sets"
    result: dict = {"model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(), "cutoffs": {}}

    def peaks(paths: list[Path], pad: float = 1.0) -> list[float]:
        out = []
        for p in paths:
            a, sr = soundfile.read(p, dtype="float32", always_2d=True)
            a = a[:, 0]
            if sr != 16000:
                import scipy.signal

                a = scipy.signal.resample_poly(a, 16000, sr).astype("float32")
            a = np.concatenate([np.zeros(int(pad * 16000), "float32"), a, np.zeros(int(pad * 16000), "float32")])
            out.append(max(_streaming_probs(model, a) or [0.0]))
        return out

    def false_accepts(paths: list[Path]) -> tuple[float, dict[float, int]]:
        hours, counts = 0.0, {c: 0 for c in cutoffs}
        for p in paths:
            a, _ = soundfile.read(p, dtype="float32")
            hours += len(a) / 16000 / 3600
            probs = np.array(_streaming_probs(model, a))
            for c in cutoffs:
                above = probs >= c
                counts[c] += int(np.sum(above[1:] & ~above[:-1]) + (above[0] if len(above) else 0))
        return hours, counts

    mine = sorted((sets / "my_voice_test").glob("*.wav"))
    synth = _held_out(sets / "positive")
    party_h, party = false_accepts(sorted((sets / "ambient_test").glob("*.wav")))
    noise_files = sorted((sets / "noise_test").glob("*.wav"))
    noise_h, noise = false_accepts(noise_files)
    mine_peaks = peaks(mine) if mine else []
    synth_peaks = peaks(synth)
    for c in cutoffs:
        result["cutoffs"][str(c)] = {
            "your_voice_false_reject": (sum(p < c for p in mine_peaks) / len(mine_peaks)) if mine_peaks else None,
            "synthetic_false_reject": sum(p < c for p in synth_peaks) / len(synth_peaks),
            "party_false_accepts_per_hour": party[c] / party_h if party_h else None,
            "noise_music_false_accepts_per_hour": noise[c] / noise_h if noise_h else None,
        }
    result.update({"your_voice_clips": len(mine_peaks), "synthetic_clips": len(synth_peaks),
                   "party_hours": round(party_h, 2), "noise_music_hours": round(noise_h, 2)})
    # The held-out wake word clips again, mixed with held-out noise and music (fixed seed): real rooms are not quiet.
    rng = np.random.default_rng(3)
    noises = [soundfile.read(f, dtype="float32")[0] for f in noise_files]
    noisy: dict[int, list[float]] = {10: [], 5: [], 0: []}
    for f in synth:
        a, sr = soundfile.read(f, dtype="float32")
        if sr != 16000:
            import scipy.signal

            a = scipy.signal.resample_poly(a, 16000, sr).astype("float32")
        a = np.concatenate([np.zeros(16000, "float32"), a, np.zeros(16000, "float32")])
        n = noises[rng.integers(len(noises))]
        start = rng.integers(0, max(1, len(n) - len(a)))
        seg = np.pad(n[start : start + len(a)], (0, max(0, len(a) - len(n[start : start + len(a)]))))
        voice = np.sqrt(np.mean(a[16000:-16000] ** 2)) + 1e-9
        level = np.sqrt(np.mean(seg**2)) + 1e-9
        for snr in noisy:
            mix = a + seg * (voice / level) / (10 ** (snr / 20))
            noisy[snr].append(max(_streaming_probs(model, mix / max(1.0, float(np.max(np.abs(mix)))))))
    for c in cutoffs:
        for snr, pk in noisy.items():
            result["cutoffs"][str(c)][f"noisy_{snr}db_false_reject"] = sum(x < c for x in pk) / len(pk)
    near = _held_out(sets / "near_miss")
    near_peaks = peaks(near)
    for c in cutoffs:
        result["cutoffs"][str(c)]["near_miss_false_accept"] = sum(p >= c for p in near_peaks) / len(near_peaks)
    result["near_miss_clips"] = len(near_peaks)
    (work / out_name).write_text(json.dumps(result, indent=2))
    log("evaluate: " + json.dumps(result, indent=2))
    return result


# -------------------------------------------------------------------------------------------- export --


def stage_export(work: Path, out: Path, cutoff: float, arena: int, run_name: str = "hey_hermes") -> None:
    src = _model_path(work, run_name)
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


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", choices=["all", "download", "generate", "prepare", "features", "train", "evaluate", "export"])
    p.add_argument("--work", type=Path, default=HERE / "work" / "permissive")
    p.add_argument("--out", type=Path, default=HERE / "model")
    p.add_argument("--my-voice", type=Path, help="folder of your own 'hey hermes' clips (record.py makes them)")
    p.add_argument("--samples", type=int, default=3000, help="synthetic wake word clips (upstream notebook: 1000)")
    p.add_argument("--near-misses", type=int, default=2000, help="synthetic near-miss clips")
    p.add_argument("--batch", type=int, default=50, help="piper generation batch size (GPU: 100)")
    p.add_argument("--steps", default="10000,10000", help="training steps per stage")
    p.add_argument("--lr", default="0.001,0.0001", help="learning rate per stage")
    p.add_argument("--run", default="hey_hermes", help="name of the training run (its own folder)")
    p.add_argument("--neg-weight", type=float, default=20, help="negative_class_weight (higher: fewer false wakes)")
    p.add_argument("--near-weight", type=float, default=3.0, help="sampling weight of the near-miss phrases")
    p.add_argument("--limit", type=int, help="cap files per set (quick test runs)")
    p.add_argument("--only", action="append", choices=FEATURE_SETS, help="features: just these sets")
    p.add_argument("--shard", help=argparse.SUPPRESS)  # k/n: one part of the speech set (run by features itself)
    p.add_argument("--phrase", action="append", help="phrase(s) to synthesize; default: hey hermes + phonetic variants")
    p.add_argument("--cutoff", type=float, default=0.9, help="probability_cutoff written to the manifest")
    p.add_argument("--cutoffs", default="0.5,0.6,0.7,0.8,0.85,0.9,0.95", help="evaluate: cutoffs to report")
    p.add_argument("--model", type=Path, help="evaluate: another .tflite (e.g. the previous model) instead of the new one")
    p.add_argument("--arena", type=int, default=30000,
                   help="tensor_arena_size for the manifest (hey_jarvis, same architecture, uses 22860; headroom added)")
    a = p.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    s = a.stage
    if s in ("download", "all"):
        stage_download(a.work)
    if s in ("generate", "all"):
        stage_generate(a.work, a.phrase or DEFAULT_PHRASES, a.samples, a.batch, a.near_misses)
    if s in ("prepare", "all"):
        stage_prepare(a.work, a.limit, a.my_voice)
    if s in ("features", "all"):
        stage_features(a.work, a.only, tuple(int(x) for x in a.shard.split("/")) if a.shard else None)
    if s in ("train", "all"):
        stage_train(a.work, [int(x) for x in a.steps.split(",")], [float(x) for x in a.lr.split(",")], a.run,
                    a.neg_weight, a.near_weight)
    if s in ("evaluate", "all"):
        cut = [float(c) for c in a.cutoffs.split(",")]
        model = a.model or _model_path(a.work, a.run)
        stage_evaluate(a.work, cut, model, f"evaluation-{a.model.stem if a.model else a.run}.json")
    if s in ("export", "all"):
        stage_export(a.work, a.out, a.cutoff, a.arena, a.run)


if __name__ == "__main__":
    main()
