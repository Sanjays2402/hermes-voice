# Decisions

Choices made while building v0.1 without asking, and why. Newest last.

1. **Talk to Hermes through its OpenAI-compatible API server, non-streaming, with plain aiohttp.** The
   non-streaming response carries Hermes' failure flags (`finish_reason`, `hermes.failed`,
   `X-Hermes-Error`), which the fallback logic needs. No `openai` dependency in the integration.
2. **Always send `X-Hermes-Session-Id`; send only the newest user message.** Hermes loads history from
   its own database when the header is present. Avoids the first-message session-name collision and HA's
   5-minute history drop.
3. **`per_device` without a device id** (typed text, REST calls) keys on the HA user id, then on the
   conversation id. Lets the mode work and be tested from the REST API.
4. **Window id = window start as Unix seconds** (`ha-dev-<key>-<start>`). Readable and unique per device.
5. **Session id characters limited to `[A-Za-z0-9_.:-]`, 256 chars max.** Hermes uses ids in file names and
   rejects path-unsafe or longer ids.
6. **`user_key` is a template string** (`{user_id}`, `{device_id}`), empty = off. Hermes' per-user memory
   hook is the `X-Hermes-Session-Key` header (`api_server_openai_routes.py`, docs "Long-term memory
   scoping"); it affects memory providers that use the gateway session key (e.g. Honcho), not the
   built-in file memory.
7. **Voice instruction goes in a system message.** Hermes layers client system messages on top of its core
   prompt as an ephemeral prompt (verified in the spike: SOUL.md markers stayed in every reply).
8. **Fallback on any failure, including HTTP 401** (plus a reauth prompt at setup). Nothing raw is spoken.
9. **Minimum HA version 2026.10.0 in `hacs.json`.** Only 2026.10.4 was tested; the code uses recent APIs
   (`OptionsFlowWithReload`, chat log based `ConversationEntity`). Lower it after testing older versions.
10. **Tests run in a Docker image built from the HA 2026.10.4 image** plus
    `pytest-homeassistant-custom-component==0.13.375` (which pins exactly that HA), so test and runtime
    match and nothing is installed on the host.
11. **`session_search` disabled via `agent.disabled_toolsets`** (global suppression, applied last), not by
    editing `platform_toolsets.api_server`. Simpler and covers every platform of the voice profile.
12. **Skill scripts are stdlib-only**, including a ~80-line websocket client for HA's WS API (long-lived
    token, entity registry, Assist pipeline update), following Hermes' "no external dependencies" skill
    guideline.
13. **Skill matches existing entries by base URL from HA diagnostics**: HA's REST entry list has no
    `unique_id`; the diagnostics carry `base_url` with the key redacted.
14. **Docker HA restarts use `docker restart -t 120`** after waiting for `.storage/auth` to contain the new
    token (HA saves its auth store 300 s after first start). The REST restart service did nothing on a
    fresh container in testing.
15. **Credentials created by the skill go to `~/.config/hermes-voice/secrets/` (0600)** by default, outside
    any Hermes profile and outside the repo.
16. **Device scope narrowed to Echo (echo-dot-assist) for v0.1** at the user's request mid-build. The
    T-Deck Plus / ESP32 ESPHome work (phase 3) was not done; `devices/esp32/` was not created.
17. **Wake word pipeline runs natively in a venv**, not Docker: a torch+TensorFlow image would sit on the
    internal drive (Docker Desktop VM). `piper-sample-generator==3.2.0` uses `piper-tts==1.3.0`, which has
    macOS arm64 wheels, so the old `piper-phonemize` macOS gap does not apply. The notebook's
    root-level `generate_samples.py` no longer exists upstream; the pipeline uses `python -m piper_sample_generator`.
18. **Wake word phrases**: "hey hermes" plus phonetic variants ("hey her mees", "hey hermiss"); 3000
    positives by default (upstream notebook: 1000), other hyperparameters as upstream. `tensor_arena_size`
    30000 (upstream hey_jarvis with the same architecture: 22860) for headroom.
19. **Credits list TECHO5** as a related, separate Echo firmware project (HuskerMinion/techo5). It is not
    mentioned in echo-dot-assist's README and was not used.
20. **Repo `CHANGES.md` is the release changelog.** Changes made on this machine outside the repo (Hermes
    profile edits, containers) are logged with rollback in a change log next to the repo on the development
    machine, which is not part of the release.
21. **Live testing went through a dev-only counting proxy** (kept next to the repo on the
    development machine, not in the repo) that refuses model calls after 40.
22. **Wake word dependency fixes against the upstream notebook** (all found while running it on 2026-11-03):
    piper-sample-generator runs from its pinned source tree on `PYTHONPATH` (the PyPI wheel lacks
    `piper_train/`); microWakeWord is installed editable from its pinned source (`find_packages()` misses
    `microwakeword/audio`); `pymicro-features==2.0.2` from PyPI replaces the notebook's macOS fork (API
    changed to `process_samples`); `audiomentations==0.43.1` (0.33 lacks `AddColorNoise`); `tensorboard`
    added; AudioSet comes from one pinned parquet shard read with pyarrow + soundfile (the tar file is gone
    and `datasets` 3.6 cannot parse the shard's newer feature metadata). Every Hugging Face download is pinned
    to a dataset revision.
23. **Training runs on the Mac (CPU).** A probe suggested ~0.05 s/step, but with the full negative sets it is
    ~1.2 s/step (~3.5 h for 10,000 steps). Still practical unattended, so the model is trained here; the Linux
    CUDA script stays for faster runs.
24. **The training Python is a uv-managed standalone CPython 3.11 on the data drive**: the machine's
    pyenv 3.11 lacks `_lzma` (needed by `datasets`). uv's `python install` also links `python3.11` into
    `~/.local/bin`; that link was removed again.
25. **The wake word is trained on permissively licensed data only** (2026-11-06), so the model can be MIT
    like the code: LibriSpeech, MUSAN filtered per file, OpenSLR simulated RIRs, DiPCo, and the LibriTTS-R
    Piper generator. ShareAlike is left out with NonCommercial and NoDerivatives, since it could reach the
    model. Near-miss phrases are trained as negatives; two training stages. Evidence and numbers:
    `docs/research/2026-11-06-wake-word-permissive.md`. Supersedes the data choices in 18, 22 and 23.
26. **Tuning to your own voice is optional and stays local.** The published model never includes anyone's
    recordings. `wakeword/record.py` makes clips; `pipeline.py --my-voice DIR` trains on 80 % and tests on 20 %.
