# hey_hermes model — training results

Trained 2026-11-03/04 on a Mac Studio (M1 Max, CPU only) with `wakeword/train.sh --samples 3000 --steps 10000`
(pipeline commit in this repo; inputs pinned in `../README.md`). Training took ~3 h 2 min
(21:47 → 00:49); the full run including downloads and features ~3 h 40 min.

| File | SHA-256 |
|---|---|
| `hey_hermes.tflite` (62,304 bytes, quantized streaming model) | `d102c17b71f12a72e5cd51d9c4bb2f6f431f2a2c9c4b809be7ba0d14dca7dea2` |
| `hey_hermes.json` | micro_wake_word manifest v2 |

## Test results (TFLite quantized streaming model, best weights)

Testing set = held-out **synthetic** "hey hermes" clips (same TTS generator as training) + the ambient
`dinner_party_eval` negatives. From microWakeWord's own evaluation:

| probability_cutoff | false reject rate | false accepts per hour (ambient) |
|---|---|---|
| **0.92** (manifest value) | 5.3 % | 0.00 |
| 0.89 | 4.7 % | 0.19 |
| 0.61 | 2.7 % | 0.38 |
| 0.44 | 2.3 % | 0.56 |

Last validation (step 10,000, nonstreaming): recall 94.9 % at precision 100 %, 0.10 estimated false
positives per hour on the ambient validation set.

## Caveats

- The positives are synthetic. Upstream notes that testing with samples made the same way as the training
  samples makes a model look better than it is with real voices. Expect a higher false reject rate with
  real speakers; lower the cutoff on the device if it misses too often.
- `tensor_arena_size` 30000 is an estimate (same architecture as upstream hey_jarvis, which uses 22860).
- The model was tested on a second-generation Echo Dot running echo-dot-assist. The table above
  reports the offline evaluation. ESPHome hardware remains untested.
- The model is for [non-commercial use only](MODEL_NOTICE.md).
