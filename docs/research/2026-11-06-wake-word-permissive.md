# "Hey Hermes" on permissively licensed data — 2026-11-05/06

**Question.** Can the "Hey Hermes" wake word model be released under MIT like the rest of Hermes Voice?

**Answer.** Not the first model: part of its training data is licensed for non-commercial use only, and the
model is reasonably treated as carrying that. So the model was retrained on data whose licenses allow any
use. The new model (candidate, not yet shipped) rejects near-miss phrases far better than the old one, has
no false wakes on 5.2 hours of held-out party, noise and music audio at its cutoff, and misses a few more
"hey hermes" clips in loud noise. It still needs a test with real voices on an Echo before it replaces the
bundled model.

## 1. Inputs of the first model, and their licenses

| Input | Used for | License | Verdict |
|---|---|---|---|
| Kevin Ahrendt's negative features (`kahrendt/microwakeword` on Hugging Face) | speech, party, non-speech negatives and the false-accept test | CC BY-NC 4.0 (the model notice said so) | must go |
| FMA xsmall (`mchl914/fma_xsmall`) | background noise | the uploader labels it MIT, but FMA tracks carry their own mixed CC licenses, some non-commercial | must go |
| AudioSet shard (`agkphysics/AudioSet`) | background noise | CC BY 4.0 covers the labels; the audio is from YouTube videos | must go |
| MIT environmental impulse responses (`davidscripka/...`) | room echo | "unknown" on Hugging Face | replaced |
| piper-sample-generator, LibriTTS-R generator | the wake word clips | MIT; trained on LibriTTS-R (CC BY 4.0) | kept (see 2) |
| microWakeWord | training code | Apache 2.0 | kept |

## 2. Permissive replacements (checked at the source)

| Data | License (where checked) | Used for |
|---|---|---|
| [LibriSpeech](https://www.openslr.org/12/) train-clean-100, dev-other | CC BY 4.0 (OpenSLR page, Hugging Face card) | speech negatives: 8,000 utterances, about 27 h |
| [MUSAN](https://www.openslr.org/17/) noise and music | CC BY 4.0 overall (OpenSLR); per-file licenses in its `LICENSE` files | negatives and background, **filtered per file** (below) |
| [OpenSLR 28](https://www.openslr.org/28/) simulated room impulse responses | Apache 2.0 | room echo (3,000 sampled). Its *recorded* RIRs come from other databases with their own terms: not used |
| [DiPCo, Dinner Party Corpus](https://zenodo.org/record/8122551) | CDLA-Permissive-1.0 (Zenodo record metadata) | party chatter: dev sessions, arrays U02–U05, as negatives and background; dev array U01 to validate; **eval sessions only to test** |
| Piper LibriTTS-R generator (`en_US-libritts_r-medium.pt`, exported from `rhasspy/piper-checkpoints`) | MIT repository; its model card: trained from scratch on LibriTTS-R, CC BY 4.0 | "hey hermes" and near-miss clips |

Two traps found on the way:

- **The Piper *voice* of the same name is a different model.** `rhasspy/piper-voices` en_US/libritts_r/medium
  says "fine-tuned from English lessac medium". The generator the pipeline uses is the checkpoint in
  `piper-checkpoints`, whose card says "trained from scratch". The pipeline downloads that one by URL.
- **MUSAN is not uniformly CC BY.** Its `LICENSE` files are free-text blocks, one per album or source, and
  they mix CC BY with CC BY-SA, BY-ND and BY-NC. Some folders state one license for everything (Free Sound
  noise: "marked as in the Public Domain"; rfm: "CC BY 3.0" at the top).

**Decision: what counts as permissive.** Kept: CC BY (any version), CC0, public domain, US government. Left
out: NonCommercial, NoDerivatives and **ShareAlike** (a ShareAlike license could reach the model), and any
file without a clear license. Every decision is written to `work/permissive/sets/musan_licenses.tsv`.
First parser run: 266 kept, 850 noise files unreadable (the folder-wide statements); fixed to apply a
folder's license to files listed without their own. Final: **1,256 kept** (843 Free Sound public domain,
86 Sound Bible CC BY 3.0 / public domain, 327 music CC BY / public domain), **327 left out** (Jamendo,
hd-classical and FMA albums under BY-SA or BY-ND), 7 without a license entry.

## 3. What else changed in training

- **Near-miss phrases as negatives**, as upstream does with its adversarial phrases: 2,000 clips of 20
  phrases ("hey hermit", "hey herman", "hey hershey", "hermes", "hey terms", "hey thermos", "a hermes bag",
  ...), same generator. Sampling weight 3.
- **Two training stages**: 10,000 steps at learning rate 0.001, then 10,000 at 0.0001.
- **Speech set** cut from 20,000 to 8,000 utterances: feature extraction ran at about 1 spectrogram a
  second (RIR convolution on long clips), about 20 h for the full set. Split across 4 processes it took
  about an hour.
- **Training is fast now**: about 8 minutes for 20,000 steps on the Mac (M1 Max, CPU), against about 3 h for
  10,000 with the old 25 GB negative sets. Most of the cost moved to feature extraction (about 75 min).

## 4. A test that does not flatter the model

The old results came from upstream's own test: held-out *synthetic* clips plus the CC BY-NC ambient set.
`pipeline.py evaluate` adds checks on audio no model was trained on, with the streaming quantized model fed
the way a device feeds it (upstream's `microwakeword.inference.Model`, a fresh interpreter per file, outputs
averaged over 5 like micro_wake_word):

- **Misses**: the 600 held-out "hey hermes" clips (the 20 % microWakeWord's seeded split keeps out of
  training), clean, and mixed with held-out noise and music at 10, 5 and 0 dB.
- **False wakes per hour**: DiPCo's eval sessions (2.6 h, one far-field microphone) and held-out MUSAN noise
  and music (2.6 h).
- **Near misses**: the 400 held-out near-miss clips; a "wake" is any output over the cutoff.
- **Your voice** (optional): 20 % of your own recordings, held out.

Pitfalls found while building it, all fixed before the numbers below:

- The generated clips are 22.05 kHz. Training resamples (microWakeWord's `Clips`); a quick script that did not
  produced nonsense per-phrase numbers.
- microWakeWord's second split (validation vs test) has no seed, so "the test split" differed run to run.
  The evaluation uses both halves: the same 20 % every time.
- A streaming model keeps state between calls; one interpreter per file.

## 5. Results

Same evaluation, same clips, for the old model and four new variants (2026-11-06 02:10):

| Cutoff 0.9 | clean | 10 dB | 5 dB | 0 dB | party false wakes/h | noise/music false wakes/h | near misses that wake it |
|---|---|---|---|---|---|---|---|
| Old model (CC BY-NC data) | 0.0 % | 0.0 % | 0.8 % | 4.8 % | 0.38 | 0.00 | **81.8 %** |
| New, one stage | 2.3 % | 3.2 % | 7.5 % | 20.7 % | 0.38 | 0.00 | 8.0 % |
| **New, two stages (candidate)** | **0.0 %** | **1.0 %** | **2.7 %** | **9.3 %** | **0.00** | **0.00** | **12.5 %** |
| Two stages, negative weight 10 | 0.3 % | 1.2 % | 2.7 % | 10.5 % | 1.54 | 0.00 | 8.8 % |
| Two stages, near-miss weight 1.5 | 1.7 % | 1.7 % | 4.7 % | 15.8 % | 0.38 | 1.16 | 7.8 % |

At cutoff 0.95 the candidate: 0.0 / 1.3 / 2.8 / 10.5 % missed, no false wakes, 11.8 % of near misses.

Near misses per phrase at 0.9 (earlier, smaller held-out set): the old model woke on 100 % of "hey hermit",
"hey herman", "hey her mess", "hey germs", "hey hershey", "hey herbs", "hey firms", "hey terms", "hey homies",
"hey thermos", "hey hare miss"; the candidate on 0–18 % of those. Both wake on "hey her nieces" (88 %),
which is close to the wake word itself; the candidate on 25 % of "a hermes bag", which contains it.

**Reading.** The old model behaves like a "hey h..." detector: it almost never misses, and it wakes on most
phrases that start like the wake word. The candidate tells them apart, does not wake on party chatter at
0.9, and gives up a few percent of misses in loud noise. Upstream's own test on augmented clips (its
background mix) agrees in direction: the candidate missed 9.7 % at 0.92 with no false accepts on the
party test audio.

**Not yet tested.** Real voices. The positives are synthetic, which flatters every model (upstream says
so). Next: the candidate on the Echo Show and the Echo Dot with real speakers, and optionally a run with the
user's own recordings (`record.py`, `--my-voice`).

## 6. Your own voice, optional

`wakeword/record.py split memo.m4a` cuts one long phone recording ("Hey Hermes" 60–100 times, a pause
between) into clips with webrtcvad; tested on a synthetic memo (20 of 20 found, AAC, light noise). `pipeline.py
prepare --my-voice DIR` holds out 20 % to measure misses on that voice and trains on the rest. The published
model never includes anyone's recordings (decision 26 in `DECISIONS.md`).

## 7. Reproduce

```bash
wakeword/train.sh                     # downloads, features, two-stage training, evaluation, export
wakeword/.venv/bin/python wakeword/pipeline.py evaluate --model path/to/other.tflite   # any other model
```

Downloads 31 GB (OpenSLR's EU mirror was about 25× faster than the default in a test; DiPCo is checked
against Zenodo's md5); unpacked audio 36 GB; features 7.4 GB.
