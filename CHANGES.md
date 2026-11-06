# Changelog

## Unreleased

- Wake word pipeline retrained on permissively licensed data only (LibriSpeech, MUSAN filtered per file,
  OpenSLR simulated room impulse responses, DiPCo, the LibriTTS-R Piper generator), with near-miss phrases
  as negatives, two training stages, and `pipeline.py evaluate`: misses on held-out clips clean and in
  noise, false wakes per hour on party, noise and music audio it never heard, and near misses. The candidate
  model wakes on 12.5 % of near misses (the old one: 82 %) with no false wakes at its cutoff; the bundled
  model is replaced after a device test. `wakeword/record.py` and `--my-voice`: optional tuning to your own
  voice, kept local. `docs/research/2026-11-06-wake-word-permissive.md`.
- Next up on the Hermes screen (opt-in): `screen_activity.py --profile voice --next-up on` creates a
  scheduled job in the voice profile that writes one short suggestion four times a day from its task board
  and memory; the helper passes on the latest (`next_up`), the idle screen shows it as its last line. It
  limits the profile's scheduled jobs to the `todo` toolset (they had every tool by default), with a backup.
- `tests/run.sh` checks the screen page's script with `node --check`.
- New page `docs/changes-and-undo.md`: everything Hermes Voice changes in Home Assistant, Hermes and on the
  Echo, and how to undo each.
- Background work on the Hermes screen (`docs/show-screen.md`, step 4): the idle screen shows Hermes' tasks
  that are running, ready for review or blocked, and that finished in the last 3 hours, instead of the last
  question. Read-only helper `screen_activity.py` (in the skill; login service with `--install`) answers
  from `hermes kanban ... --json`, guarded by the voice profile's key. New option *Background work on the
  screen*: this profile only (default), all profiles, or off; `ha_setup.py --screen-activity`.
- The speaker no longer says its own name: spoken text swaps "Hermes" ("the latest Hermes Agent release" is
  said as "the latest agent release"), and the voice persona says "I". microWakeWord on an Echo keeps listening
  while the Echo speaks, and an Echo Show 5 woke itself mid-reply. The screen keeps the real text. New option *Don't say the name aloud* (on by default) turns the swap off.
- Faster spoken replies: every sentence handed to text-to-speech ends with a blank line, so Piper speaks it at
  once (its sentence splitter otherwise waits for the next sentence to start, which put speech a sentence or
  two behind and kept the working phrase silent until the tool returned). The voice persona asks for a first
  sentence of ten to fifteen words (Home Assistant starts speech only after 60 characters) and short replies.
- Screen: the answer appears at about the pace it is spoken (13.5 characters a second from when the Echo
  starts speaking) instead of all at once ahead of the voice.
- Screen: voice bars while listening (seven bars at their own paces; the Echo does not send its sound level,
  so they do not follow the voice).
- Screen: voice timers count down on the idle screen; a ringing timer fills the screen with a Stop button
  (presses the Echo's own "Stop ringing"); "Mic off" and "Do not disturb" badges. New websocket
  command `hermes_voice/screen/stop_timer`. Tests time out after 60 s each.
- Screen: the running task's marker is a pulsing purple dot (the ◐ character was tiny on the Echo Show);
  the top-left label reads "Hermes Voice"; X search and the task board have friendly tool names.
- Screen: text at full brightness; the idle background is gently darkened; the overview's slider dims the
  whole screen (the backlight, in the app), remembered.
- Hermes Screen app 0.2: clears its page cache on every start (an Echo Show kept an old page after an
  update) and sets the screen's brightness (full by default; a slider in the overview). The page itself is
  served uncached with versioned file addresses.
- Screen overview: the middle column shows finished work instead of recent chat sessions; a degraded
  gateway says why (e.g. "low disk (98%)", from Hermes' readiness checks).
- The screen page reloads itself after an update (the snapshot carries a page version) and notices a dead
  connection (a ping every 30 s) instead of waiting forever after Home Assistant restarts.
- Research logs in `docs/research/`: the Echo Show 5 install and the Hermes screen, kept like a study.
- Hermes Screen app for the Echo Show (`devices/echo/screen-app/`, step 3 of `docs/show-screen.md`): a
  full-screen WebView of the screen page, network permission only, built with `build.sh` (no Gradle),
  installed over echo-dot-assist's debug access and picked as the home app. The idle screen is now a
  close-up of the avatar's eyes behind the clock that zooms out to the whole avatar on the wake word.
- Hermes screen page (`docs/show-screen.md`, step 2) at `/hermes_voice/screen`: idle (clock, next job or
  last question), listening, thinking (question, tool activity), answering (the reply as it is written,
  links and lists included), Home Assistant's own answers, and a tap-for overview (jobs, recent
  conversations, status). Nous Girl from Hermes Agent as the default avatar (MIT, notice included),
  replaceable from `<config>/www/hermes_voice/`. Works with a non-admin Home Assistant user. `?demo` mode.
- Live feed for a Hermes screen (`docs/show-screen.md`, step 1): websocket commands
  `hermes_voice/screen/subscribe` (the Echo's state, what was heard, Hermes' reply as it is written, its
  tools, and answers Home Assistant gave itself) and `hermes_voice/screen/overview` (Hermes' jobs, recent
  sessions and status, fetched with the integration's key, which never reaches the screen); the page is
  served at `/hermes_voice/screen/` (placeholder until step 2). The integration now depends on `http`.
- Echo Show 5 1st gen tested end to end: install from an Ubuntu live USB with exact downloads, the unlock that
  reports success without unlocking (and how to check), disk space (16 GB drive), the release build vs. the
  NDK, timings, the screen, and the wake word step that leaves the Echo deaf if skipped
  (`devices/echo/README.md`, troubleshooting).
- Tell Hermes the local date and time on every turn (Home Assistant's clock and time zone), so "what time
  is it?" and date questions work; option *Tell Hermes the date and time*, on by default. Before, Hermes
  searched the web for the time and could not read it.
- `ha_setup.py --set-default` also turns on *Prefer handling commands locally* in the pipeline (time,
  timers, device control answered by Home Assistant); `--no-local-commands` leaves it off.
- `ha_setup.py` sets the time zone of a Home Assistant it onboards to this computer's (`--time-zone` to
  choose); before, a new Home Assistant stayed on UTC and told the time hours off.
- Document the tested Echo Dot 2 Windows recovery, including the MediaTek driver block,
  hardware short, firebreak firmware defaults and possible interference between installers.
- Record the read-only A/B findings and the separate action-button approval for temporary debug access.

## 0.1.0 — 2026-11-04

First version.

- `hermes_voice` Home Assistant integration (HA 2026.10+):
  - config flow (gateway URL, API key, display name; validated with `GET /v1/models`), one entry per
    Hermes profile, reauth flow, options flow, diagnostics with the key redacted;
  - sends `X-Hermes-Session-Id` on every turn and only the new user message; session modes
    `per_conversation` (`ha-<conversation_id>`) and `per_device` (`ha-dev-<device>-<window>`, idle reset,
    persisted windows);
  - optional `X-Hermes-Session-Key` (`user_key` option, `{user_id}` / `{device_id}` placeholders);
  - voice cleanup of replies (markdown, code, URLs, emoji, tables), on by default;
  - streamed replies (on by default): HA starts speaking before the whole answer is written; Hermes' own
    tool calls are logged as external tool calls, and a configurable working phrase ("One moment.") is
    said when a tool starts before anything else;
  - optional voice instruction system message;
  - spoken fallback on rate limit, timeout, connection, auth and agent errors (including Hermes' HTTP 200
    replies that carry a failed run);
  - 81 tests (pytest-homeassistant-custom-component), ruff and mypy clean.
- `hermes-voice-setup` Hermes skill, installable on its own (`hermes skills install`; carries the
  integration and wake word model in `assets/`), with idempotent, `--dry-run`-capable scripts:
  `voice_profile.py` (with `--tools safe|home|full|<list>`: what the voice assistant may do, and
  `--reasoning`: model thinking effort), `ha_setup.py`, `speech_setup.py` (Wyoming Whisper/Parakeet + Piper, HA internal URL),
  `verify.py`.
- Docs: architecture, voice profile setup, troubleshooting; Echo (echo-dot-assist) device guide.
- `wakeword/`: pinned, resumable microWakeWord pipeline for "hey hermes", and a trained model
  (`wakeword/model/hey_hermes.tflite` + `.json`; 5.3 % false rejects / 0 false accepts per hour on synthetic test data;
  tested on a second-generation Echo Dot running echo-dot-assist). The bundled model is for
  non-commercial use only.
- Release files built from the committed tree by `tools/build_release.sh`: integration zip, skill zip,
  wake word zip, SHA256SUMS.

Setup updates restart the authenticated Home Assistant instance through its API.
