# "Hey Hermes" wake word (microWakeWord)

A reproducible pipeline that trains a [microWakeWord](https://github.com/kahrendt/microWakeWord) model for
"hey hermes" and writes the two files Home Assistant voice devices load: `model/hey_hermes.tflite` and the
ESPHome-format manifest `model/hey_hermes.json` (micro_wake_word v2).

It follows microWakeWord's own `notebooks/basic_training_notebook.ipynb` (commit `4665173`) as a script with
resumable stages, and trains **only on data whose license allows any use** (below), so the model can be
MIT like the code. Upstream code is downloaded at the pinned commits at run time; none of it is copied here.

> **Status (2026-11-06):** the pipeline trains on permissive data. The bundled `model/hey_hermes.tflite` is
> still the first model (non-commercial, see `model/MODEL_NOTICE.md`) until the new one has been tested with
> real voices on an Echo. Evidence and numbers: [the research log](../docs/research/2026-11-06-wake-word-permissive.md).

## Run it

```bash
# macOS (Apple Silicon) or Linux; Python 3.10-3.12 built with lzma
uv python install 3.11                       # if your Python lacks lzma (pyenv builds without xz do)
PYTHON=$(uv python find --python-preference only-managed 3.11) wakeword/train.sh
```

On a Linux + NVIDIA box over SSH, copying the model back: `wakeword/remote_linux_cuda.sh user@gpu-box`.

Stages (each skips finished work, so re-running resumes): `download` → `generate` → `prepare` →
`features` → `train` → `evaluate` → `export`. Run one with `.venv/bin/python wakeword/pipeline.py <stage>`.

Time on a Mac Studio (M1 Max, CPU): downloads depend on the mirror (31 GB); clip generation about 5 min;
unpacking and sorting about 10 min; features about 75 min (speech runs as 4 processes); training about
8 min (20,000 steps); evaluation about 5 min. Disk: about 75 GB under `wakeword/work/` (git-ignored).

## Data, and why each is allowed

| Data | License | Used for |
|---|---|---|
| [LibriSpeech](https://www.openslr.org/12/) train-clean-100, dev-other | CC BY 4.0 | speech the model must ignore (8,000 utterances) |
| [MUSAN](https://www.openslr.org/17/) noise and music | per file: only CC BY, CC0, public domain | noise and music to ignore, and as background |
| [OpenSLR 28](https://www.openslr.org/28/) simulated room impulse responses | Apache 2.0 | room echo (the recorded ones come from other databases: not used) |
| [DiPCo](https://zenodo.org/record/8122551) dinner party corpus | CDLA-Permissive-1.0 | party chatter; its eval sessions only for the test |
| Piper LibriTTS-R generator (`rhasspy/piper-checkpoints`, trained from scratch on LibriTTS-R) | MIT, data CC BY 4.0 | "hey hermes" clips and near misses |
| [microWakeWord](https://github.com/kahrendt/microWakeWord) @ `4665173` | Apache 2.0 | training code |

MUSAN mixes licenses inside the corpus. `prepare` reads its `LICENSE` files and keeps a file only under CC
BY, CC0, public domain or US government; NonCommercial, NoDerivatives and ShareAlike (which could reach the
model) are left out, and so is anything unclear. Each decision is in `work/permissive/sets/musan_licenses.tsv`.

## The test

`evaluate` checks the streaming quantized model, fed the way a device feeds it, on audio it never trained
on: held-out "hey hermes" clips (clean and mixed with noise at 10, 5 and 0 dB), 2.6 h of dinner party, 2.6 h
of noise and music, and held-out near-miss phrases ("hey hermit", "hey thermos", ...). `--model` runs it on
any other `.tflite`, so models can be compared on the same audio. Results go to `work/permissive/evaluation-<run>.json`.

## Tune it to your voice (optional)

The published model is trained on synthetic voices and public recordings only. If it misses you, train a
copy that knows your voice. It stays on your computer.

1. On your phone, record one voice memo saying "Hey Hermes" 60–100 times with about a second between: near
   and across the room, quiet and loud, mumbled and clear.
2. Cut it into clips: `wakeword/.venv/bin/python wakeword/record.py split memo.m4a` (needs ffmpeg for phone
   formats). Or record at the computer: `record.py mic --count 60` (needs `pip install sounddevice`).
3. Train with them: `wakeword/train.sh --my-voice wakeword/work/my_voice --run my_voice --out wakeword/work/my_model`.
   20 % of your clips are held out, and `evaluate` reports how often the model misses *your* voice.
4. Upload `my_model/hey_hermes.tflite` and `.json` on the Echo's settings page, like the published model.

This is the full training run (the downloads and about 2 hours). A faster fine-tune is not built yet.

## Using the model

- **echo-dot-assist** accepts a microWakeWord `.tflite` with its `.json` manifest (marked experimental there):
  https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/GUIDE.md#wake-word.
  Tested on a second-generation Echo Dot and an Echo Show 5 (1st gen) running echo-dot-assist.
- ESPHome devices: `micro_wake_word: models: - model: <url or path to hey_hermes.json>`. Not tested here.
- `probability_cutoff` in the manifest comes from the evaluation; tune it on the device if it triggers too
  often or too rarely.

## License

The pipeline code is [MIT-licensed](../LICENSE). For the bundled model, see [model/MODEL_NOTICE.md](model/MODEL_NOTICE.md).
