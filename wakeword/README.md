# "Hey Hermes" wake word (microWakeWord)

A reproducible pipeline that trains a [microWakeWord](https://github.com/kahrendt/microWakeWord) model for
"hey hermes" and writes the two files Home Assistant voice devices load: `model/hey_hermes.tflite` and the
ESPHome-format manifest `model/hey_hermes.json` (micro_wake_word v2).

It follows microWakeWord's own `notebooks/basic_training_notebook.ipynb` (commit `4665173`) as a script with
resumable stages. Upstream code is downloaded at the pinned commits at run time; none of it is copied here.

## Run it

```bash
# macOS (Apple Silicon) or Linux; Python 3.10-3.12 built with lzma
uv python install 3.11                       # if your Python lacks lzma (pyenv builds without xz do)
PYTHON=$(uv python find 3.11) wakeword/train.sh --samples 3000 --steps 10000
```

Train on a Linux + NVIDIA box over SSH and copy the result back:

```bash
wakeword/remote_linux_cuda.sh user@gpu-box
```

Stages (each skips finished work, so re-running resumes): `generate` → `augdata` → `negatives` →
`features` → `train` → `export`. Run one with `.venv/bin/python wakeword/pipeline.py <stage>`.
`pipeline.py probe` does a tiny end-to-end run to check the setup and measure speed.

Disk: about 30 GB under `wakeword/work/` (negative features are 5.7 GB zipped, ~25 GB unpacked, plus augmentation audio and generated features).
`work/` and `.venv/` are git-ignored.

## Pinned inputs

| Input | Pin |
|---|---|
| microWakeWord | `kahrendt/microWakeWord@4665173cd35f1cff9a61e06fc427f124766c488e` (installed editable: its `find_packages()` misses `microwakeword/audio`) |
| piper-sample-generator | `rhasspy/piper-sample-generator@2971426a55072f7d22fec416ca7800df8bd23207` source on `PYTHONPATH` (the PyPI wheel omits `piper_train/`, needed by the LibriTTS-R generator) |
| Generator model | `en_US-libritts_r-medium.pt` from piper-sample-generator release v2.0.0 |
| Python packages | `requirements.txt`; resolved versions in `requirements.lock.<os>-<arch>.txt` |
| Negative features | `kahrendt/microwakeword@0da95f9` on Hugging Face: `speech`, `dinner_party`, `no_speech`, `dinner_party_eval` |
| Augmentation audio | `davidscripka/MIT_environmental_impulse_responses@b824a1e`, `agkphysics/AudioSet@0c609e8` shard `data/bal_train/09.parquet`, `mchl914/fma_xsmall@bfef24f` |

Differences from the upstream notebook (all forced by current package versions; see DECISIONS.md):
`python -m piper_sample_generator` instead of the removed root `generate_samples.py`; `pymicro-features` 2.0.2
from PyPI on every OS (the notebook's macOS fork has an older API); `audiomentations` 0.43.1 (0.33 lacks
`AddColorNoise`); `tensorboard` added (TensorFlow 2.21 no longer installs it); AudioSet read from a parquet shard (the notebook's `bal_train09.tar` is gone); 3000 positive samples
from "hey hermes" plus two phonetic spellings instead of 1000.

## Hardware notes

- Measured on a Mac Studio (M1 Max, CPU only, TensorFlow 2.21): 3000 clips generated in ~30 s; features
  for 3000 clips ~19 min; training ~1.2 s per step with the full negative sets, so the default 10,000 steps
  take about 3.5 hours. (A tiny probe with only the eval negatives ran at ~0.05 s/step, which is misleading.)
- On Linux with an NVIDIA GPU, `train.sh` installs CUDA TensorFlow and uses generation batch 100.

## Using the model

- **echo-dot-assist** accepts a microWakeWord `.tflite` with its `.json` manifest (marked experimental there):
  https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/GUIDE.md#wake-word.
  This model was tested on a second-generation Echo Dot running echo-dot-assist.
- ESPHome devices: `micro_wake_word: models: - model: <url or path to hey_hermes.json>`. Not tested here.
- `probability_cutoff` in the manifest comes from the test results at the end of training (see
  `model/TRAINING_RESULTS.md` if present); tune it on the device if it triggers too often or too rarely.

## License

The pipeline code is [MIT-licensed](../LICENSE). The trained model is for
[non-commercial use only](model/MODEL_NOTICE.md).
