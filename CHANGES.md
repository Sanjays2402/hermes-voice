# Changelog

## Unreleased

- Video mode: "no more video" now turns the camera off. The filler-word strip ate the leading
  "no" before matching, so the off pattern's own "no more video" alternative could never fire
  and the words went to Hermes instead.

- Hermes Visual Memory is now also a native Hermes plugin (`extras/visual-memory`, `plugin.yaml`): the seven
  tools registered with Hermes, the service started inside Hermes on the first tool call, memory in Hermes's
  plugin data directory, the camera token read through Hermes. Vision goes through Hermes's own model routing
  (a "Visual memory" model slot that follows the user's vision model) or a local OpenAI-compatible server; the
  plugin never runs the Codex CLI. Passes `hermes plugins validate --install-deps` and `hermes plugins doctor`.
  The standalone service and MCP server work as before; `mcp` is now an optional extra (`.[mcp]`), since Hermes
  ships its own.
- Docs: Visual Memory's live test with a local model (qwen3.6 through Ollama): each problem found and its
  fix, what still works poorly (keys and small metal things), and where the time of an answer goes.

## 0.2.0 — 2026-11-09

- Hermes Visual Memory (`extras/visual-memory`): Hermes remembers what the Echo Show's camera saw and answers
  "what's in front of you?", "what changed?", "where did you last see …?" and "what was here earlier?",
  with before/after pictures on the Echo's screen. One picture a second is checked locally (quality,
  steadiness, real change; changed views are saved on the computer). The vision model looks only when
  visual memory is turned on, when the camera is uncovered, and when asked (with a local model, which costs
  nothing, also when the desk changes and settles). The first
  look finds the desk; only the desk is compared and tracked (people, chairs and the room behind are
  ignored), or the whole view on request. Vision by the user's Codex CLI or a local model on any
  OpenAI-compatible server (Ollama, LM Studio; `--local-model`).
  A covered camera is "may be obstructed", not "everything is gone"; an object that can't be seen is told
  apart as possibly hidden, gone from a visible spot, or just not re-detected. SQLite memory, retention and
  delete controls, off until turned on, a launchd service. Seven Hermes tools (MCP).
- Setup skill: an optional visual memory step (`vismem_setup.py`): installs the service in its own Python
  environment, starts it at login (launchd or systemd), connects the tools; `--check`, `--dry-run`,
  `--uninstall [--delete-data]`. Visual memory stays off until the user turns it on.
- Screen: a "Visual memory on" chip and the pictures Hermes shows; tool names for visual memory and agent tasks.
- Agent tasks (`extras/agent-tasks`): a read-only view of the other Hermes agents' task boards and schedules
  for a voice profile. Only list/show commands can run; nothing can be created, assigned or handed off.
- Faster replies on long conversations: the lines that change every turn (the local time, the open recipe,
  video mode) now go in a short bracketed note in front of the user's words instead of the system message.
  Hermes puts the system message ahead of the conversation history, so a changing clock there made the
  model re-read the whole history each turn. Prompt-cache reuse went from about 55% to about 97%.

- Hermes Eye (`devices/echo/eye-app`, [docs/eye.md](docs/eye.md)): the Echo Show's camera for a vision
  service on the LAN. A 21 KB app with no screen. It serves snapshots and MJPEG with a token, opens the
  camera only on request, shows a "● REC" badge while the camera is open, and answers "covered" for a
  closed shutter and "blocked" when the mute button cuts the camera. Installed over USB ADB; no firmware
  change.
- Setup skill 0.2.0: an optional camera step. `eye_setup.py` builds Hermes Eye on the user's own computer,
  signed with a key it makes there, so there's no prebuilt app to trust and no shared key. It installs the
  app over USB, gives it a token, connects `look` to the voice profile (backed up first) and takes a test
  picture. `--reinstall` covers a lost key.
- Recipes on the Hermes screen, from Mealie: `extras/mealie` is a small MCP server (six tools: search and
  read recipes, the meal plan, the shopping list; nothing that deletes) for a Hermes profile. Recipes
  Hermes reads open on the screen of the device that asked; searches show photo cards. A cooking view shows
  one step at a time with Back/Next, swipes, a step timer button and an ingredient checklist. While a
  recipe is open, "next step", "back", "step 4", "ingredients" and "close the recipe" are answered by Home
  Assistant at once, and other questions reach Hermes with the recipe and step on screen. Uses Home
  Assistant's Mealie integration (`recipes.py`).
- Hermes screen: tools from connected MCP servers show a plain name ("Opening the recipe", or the tool's
  name without the `mcp__server__` prefix), and a tool keeps its label when it finishes. Hermes Eye's `look`
  shows as "Looking through the camera".
- Video mode ([docs/eye.md](docs/eye.md#video-mode)): the user's live picture in a corner of the Hermes
  screen while they talk. It turns on by voice ("video mode") or when Hermes looks, and Hermes is told while
  it's on. After three replies without the camera, the screen asks "Done with the camera?". It ends by voice
  ("stop video"), by button, after 5 idle minutes, or when the shutter or mute cuts the camera. `look`
  (extras/eye) and Hermes Eye 0.4.1 (live window, `/video`).
- Hermes screen: if the voice hasn't started 3 s after a reply is complete (no speech, a failed voice, a
  typed turn), the whole reply shows instead of staying blank.

- Streamed replies cut off by the token limit retain the partial reply instead of adding the failure
  fallback (#3). The setup skill carries the same client fix.
- Document the Skills Hub installation block and add a static compatibility check using Hermes' actual
  scanner and unchanged install policy. The full device guide and setup operations remain included (#4).
- Correct the research log: gateway plugins can add HTTP routes; named-profile routing and explicit
  authentication still need care. The current helper remains in use.

- Tap to correct on the Hermes screen: while Hermes thinks or answers, a tap stops it, the Echo says "Go
  ahead." and listens again without the wake word, and the corrected request continues the same Hermes
  session with a note about what was misheard (`hermes_voice/screen/retry`).
- The Hermes screen recovers from a broken update by itself: if its script fails to start (an update with a
  mistake in it), the page reloads every 30 s instead of staying frozen on "Connecting".
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
