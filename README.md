# Hermes Voice

Talk to one of your [Hermes Agent](https://github.com/NousResearch/hermes-agent) profiles out loud, through
Home Assistant Assist, on an old Amazon Echo running echo-dot-assist.

On an Echo Show, the screen follows along: what it heard, the answer as it is written, the tools Hermes is
using, and, when idle, what it is working on in the background. The screen is a web page, so you can make
it your own ([The Hermes screen](#the-hermes-screen-echo-show)).

[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist) replaces the Alexa client while
keeping Fire OS and the Echo's audio processing. Hermes Voice runs on the Home Assistant and Hermes
hosts. It connects Assist to a Hermes profile once the Echo has been set up as an Assist satellite.

```
 Echo (echo-dot-assist)          Home Assistant                        Hermes Agent
 ┌──────────────┐  audio  ┌──────────────────────────────┐  HTTPS/HTTP  ┌─────────────────────┐
 │ wake word,   │ ──────▶ │ Assist pipeline              │ ───────────▶ │ one profile's       │
 │ mic, speaker │ ◀────── │  speech-to-text (Whisper…)   │  /v1/chat/   │ API server          │
 └──────────────┘  audio  │  conversation: hermes_voice ─┼─ completions │ (SOUL.md, memory,   │
                          │  text-to-speech (Piper…)     │ ◀─────────── │  tools, provider)   │
                          └──────────────────────────────┘   reply      └─────────────────────┘
```

This repo adds only the missing pieces:

| Piece | What it does |
|---|---|
| `custom_components/hermes_voice` | Home Assistant conversation agent that sends each turn to a Hermes profile with a stable `X-Hermes-Session-Id`, so Hermes keeps the conversation. Streams the reply so speech starts early, cleans it for speech, and speaks a short fallback instead of error text. |
| `custom_components/hermes_voice/screen`, `devices/echo/screen-app` | The Hermes screen: a page Home Assistant serves with the conversation as it happens, and a small Android app that shows it full screen on an Echo Show. |
| `skill/hermes-voice-setup` | A Hermes skill: your own agent prepares a voice profile, installs/configures Home Assistant + hermes_voice, and verifies it. |
| `docs/changes-and-undo.md` | Everything Hermes Voice changes in Home Assistant, Hermes and on the Echo, and how to undo each. |
| `docs/voice-profile.md` | How to set up a dedicated voice profile by hand (SOUL.md, tools, thinking effort, separate provider). |
| `wakeword/` | Reproducible pipeline that trains a "hey hermes" microWakeWord model, plus the trained model (`wakeword/model/`). |

Everything else is existing open-source work, used as-is; see [CREDITS.md](CREDITS.md).

## How it works

- **One Home Assistant entry per Hermes profile.** The profile is selected by URL: a standalone gateway on
  its own port (`http://HOST:8650/v1`) or the multiplexed gateway route (`http://HOST:8642/p/<profile>/v1`).
- **Hermes keeps the history.** hermes_voice sends only the new user message plus the session header.
  Session modes:
  - `per_conversation` (default): `ha-<conversation_id>`. Home Assistant starts a new conversation after
    5 minutes of silence.
  - `per_device`: `ha-dev-<device>-<window>`. One session per voice device, continued until it has been
    idle for `idle_reset_minutes` (default 30). Window state survives restarts.
- **Personality stays the profile's.** Hermes applies the profile's SOUL.md and long-term memory. hermes_voice
  adds one short "answer briefly, for speech" system message (configurable).
- **No Home Assistant device control in v0.1.** Hermes gets no HA tools from this integration. To let the
  agent control devices, give the profile Home Assistant's MCP server.

## The Hermes screen (Echo Show)

On an Echo Show the home screen becomes Hermes':

- **Idle**: the avatar, the time, running timers, and Hermes' background work: tasks running, waiting for
  review or blocked, the next scheduled job, and (opt-in) a short "next up" suggestion.
- **Listening and thinking**: your words once speech-to-text has them, and each tool as Hermes uses it
  ("Searching the web", "Reading a page").
- **Answering**: the reply appears as it is written, while it is spoken.
- **Tap** the idle screen for an overview of scheduled jobs and recent sessions. Timer alarms get a big Stop button.

**Make it yours.** The screen is one HTML page (`custom_components/hermes_voice/screen/`: `index.html`,
`style.css`, `app.js`) that gets a live feed of events from Home Assistant over its websocket: listening,
thinking, each part of the reply, tool calls, timers, background work. Swap the avatar by dropping a picture
in Home Assistant's `www/hermes_voice/` folder, restyle it, or build something else on the same feed. Open
`/hermes_voice/screen?demo` in a desktop browser to work on it without an Echo.

Set up: [devices/echo/README.md](devices/echo/README.md#hermes-screen-on-an-echo-show). Design, options and
the feed: [docs/show-screen.md](docs/show-screen.md).

## What you need

- A Hermes Agent install with a working model provider (`hermes setup`).
- Docker Desktop on macOS for the one-machine setup shown below. The skill can also configure an
  existing Home Assistant installation. The Docker examples use `host.docker.internal`; with native
  Docker on Linux, configure addresses that the Home Assistant container can reach.
- A voice device: an Echo Dot or Echo Show running [echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist)
  (see [devices/echo/README.md](devices/echo/README.md)), or another Assist satellite.

## Quick start (let your Hermes agent set it up)

1. Install the skill:
   ```bash
   hermes skills install majorminorlabs/hermes-voice/skill/hermes-voice-setup
   ```
   It carries everything it needs: setup scripts, the Home Assistant integration and the "Hey Hermes"
   wake word model.
2. Ask your agent: *"Use the hermes-voice-setup skill to make me a voice assistant."* It asks you a few
   questions (which profile, what the voice assistant may do, whether you already run Home Assistant),
   shows a `--dry-run` of each step, and then runs:
   ```bash
   voice_profile.py --profile voice --create --write-soul --tools safe --reasoning low --service
   ha_setup.py --profile voice --docker --config-dir ~/hermes-voice/ha-config --set-default
   speech_setup.py --docker --data-dir ~/hermes-voice/speech --set-default --internal-url http://<LAN-IP>:8123 --piper-voice en_US-ryan-high
   verify.py --recall
   ```
   `--tools` decides what Hermes may do when spoken to: `safe` (web search, memory), `home` (+ Home Assistant
   control), `full` (terminal, files, code: for coding by voice) or your own list. See
   [docs/voice-profile.md](docs/voice-profile.md#4b-choose-what-hermes-may-do-by-voice).
3. Set up the Echo with echo-dot-assist and switch it to "Hey Hermes"
   ([devices/echo/README.md](devices/echo/README.md)).

## Manual install

1. Prepare a voice profile and start its API server: [docs/voice-profile.md](docs/voice-profile.md).
2. HACS → ⋮ → Custom repositories → add this repository's URL, category *Integration* → install
   **Hermes Voice** → restart Home Assistant. Or download `hermes_voice-<version>.zip` from the release,
   unzip it into `<config>/custom_components/` to create `<config>/custom_components/hermes_voice/`, then restart.
3. Settings → Devices & services → Add integration → **Hermes Voice**: gateway URL, the profile's
   `API_SERVER_KEY`, a display name. The URL is validated with `GET /v1/models` (no model call).
4. Settings → Voice assistants → your pipeline → Conversation agent → the new Hermes entity. The pipeline
   also needs speech-to-text and text-to-speech (Wyoming Whisper + Piper, or HA Cloud); `speech_setup.py`
   in the skill sets those up.
5. Options: session mode, idle reset, voice cleanup, voice instruction, fallback message, timeout,
   optional memory scope key (`X-Hermes-Session-Key`), stream replies (on), working phrase ("One moment.",
   said when Hermes starts a tool before saying anything; empty turns it off), tell Hermes the date and time
   (on: Hermes has no clock, so each turn carries Home Assistant's local date and time).
6. Recommended: in the pipeline, turn on **Prefer handling commands locally**. Home Assistant then answers
   its own sentences at once (the time, timers on the device, lights and other devices) and sends everything
   else to Hermes. Those answers are Home Assistant's, not Hermes', and Hermes does not see those turns.
   `ha_setup.py --set-default` turns it on.

Requires Home Assistant 2026.10 or newer (the version tested).

## Devices

| Device | Status |
|---|---|
| Echo Dot 2nd gen via [echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist) | Tested end to end (flashed from Windows, "Hey Hermes" wake word, Home Assistant in Docker on macOS). Device setup is echo-dot-assist's; it can brick the Echo. |
| Echo Show 5 1st gen (2019) via echo-dot-assist | Tested end to end (flashed from an Ubuntu live USB, "Hey Hermes" wake word, Home Assistant in Docker on macOS). Needs a Linux PC and a 16 GB drive; see [devices/echo/README.md](devices/echo/README.md#echo-show-5-1st-gen-on-an-ubuntu-live-usb). |
| Echo Dot 3rd gen (2018), Echo 2nd gen, Echo Show 5 2nd gen, Echo Show 8, Echo Input via echo-dot-assist | Supported by echo-dot-assist; not tested by this project. |
| Other Home Assistant Assist satellites (e.g. Voice PE) | Should work, since the satellite only needs a pipeline whose conversation agent is hermes_voice. Untested. |

The tested Echo Dot 2 required a hardware short after the unlock stalled. The device guide records
the Windows driver issue, recovery point, firmware mismatch and subsequent slot inspection.
It also explains why matching firmware in both slots does not establish a working fallback.
Read the [Echo Dot 2 recovery notes](devices/echo/README.md#echo-dot-2-recovery-on-windows) before recovering a stalled unlock.

The tested Echo Show 5 needed no opening, but its first unlock run reported success and left it locked; a rerun
worked. The device guide has the exact downloads, disk space, timings and the full run
([Echo Show 5 notes](devices/echo/README.md#echo-show-5-1st-gen-on-an-ubuntu-live-usb)).

## Research log

Everything tested on real hardware is written down in [docs/research/](docs/research/README.md): what was
checked, the evidence, the decisions and why, and what went wrong.

## Known limits

- **Latency**: about 3.5 s per plain turn in testing, almost all of it the model; Home Assistant adds about
  0.03 s. Turns with a tool (web search) took 7 s in all, but speech started at about 3 s with "One moment."
  (streaming, on by default). `voice_profile.py --reasoning low` makes the model answer sooner.
- **Rate limits**: a voice profile that shares your main agent's provider login shares its rate limit. On
  429/timeout/connection errors hermes_voice speaks a fallback line. Use a separate provider or a fallback
  chain ([docs/voice-profile.md](docs/voice-profile.md)).
- **Memory is per profile**, not per speaker; anything said may be remembered for everyone using that device.
- **Wake word**: the "Hey Hermes" model is tested on one Echo Dot 2nd gen and one Echo Show 5 1st gen in one home. If it misses you
  or wakes on its own, adjust its threshold on the Echo's settings page.
- Streaming speech needs a text-to-speech engine that supports it (Piper via Wyoming does). With others
  the full answer is generated before speech starts.

## Development

```bash
tests/run.sh                   # pytest (pytest-homeassistant-custom-component), ruff, mypy in the pinned HA 2026.10.4 image
tools/sync_skill_assets.sh     # after changing the integration or the wake word model: refresh the skill's copies
tools/build_release.sh         # release files in dist/ (integration zip, skill zip, wake word zip, checksums)
```

Docs: [architecture](docs/architecture.md) · [voice profile](docs/voice-profile.md) ·
[troubleshooting](docs/troubleshooting.md) · [what it changes, and undo](docs/changes-and-undo.md) · [decisions](DECISIONS.md)

## License

The code is [MIT-licensed](LICENSE). The bundled `hey_hermes.tflite` wake word model is for
[non-commercial use only](wakeword/model/MODEL_NOTICE.md).
