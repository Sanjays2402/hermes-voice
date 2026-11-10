# Hermes Voice

Talk to one of your [Hermes Agent](https://github.com/NousResearch/hermes-agent) profiles out loud, through
Home Assistant Assist, on an old Amazon Echo running echo-dot-assist.

On an Echo Show, the screen follows along: what it heard, the answer as it is written, the tools Hermes is
using, and, when idle, what it is working on in the background. The screen is a web page, so you can make
it your own ([The Hermes screen](#the-hermes-screen-echo-show)).

[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist) replaces the Alexa client while
keeping Fire OS and the Echo's audio processing. Hermes Voice runs on the Home Assistant and Hermes
hosts. It connects Assist to a Hermes profile once the Echo has been set up as an Assist satellite.

## Echo compatibility: check before buying

> [!WARNING]
> **Check the exact model number and installed Fire OS build before buying an Echo for this project.**
> A used unit can already have an Amazon update that blocks its unlock, even if the model is listed below.
> Ask the seller for a photo of the model label and the full software version. Treat compatibility as
> unknown if the seller can't confirm the build. A factory reset leaves the installed firmware in place.

The table lists the firmware builds that echo-dot-assist targets. To check a unit before buying, compare
its **currently installed build**, and its bootloader where required, with the linked model guide.
Some guides install the target build after unlocking, so the installed build and the target build can differ.
Keep a stock unit off Wi-Fi until its model guide calls for a connection.

| Echo | Model number | Target Fire OS build | Model guide |
|---|---|---|---|
| Echo Dot 2nd gen (2016) | `RS03QR` | 6574.1 (`NS65741`, build `8146`) | [biscuit](https://github.com/Gamer92000/echo-dot-assist/blob/main/devices/biscuit/README.md) |
| Echo 2nd gen (2017) | `XC56PY` | 6.5.7.2 (`NS6572`, build `6436`) | [radar](https://github.com/Gamer92000/echo-dot-assist/blob/main/devices/radar/README.md) |
| Echo Dot 3rd gen (2018) | `D9N29T` | 6574.1 (`NS65741`, build `8138`) | [donut](https://github.com/Gamer92000/echo-dot-assist/blob/main/devices/donut/README.md) |
| Echo Input (2018) | `C1125P` | 6.5.7.5 (`NS6575`, build `8338`) | [cupcake](https://github.com/Gamer92000/echo-dot-assist/blob/main/devices/cupcake/README.md) |
| Echo Show 5 1st gen (2019) | `H23K37` | 6574.1 (`NS65741`, build `8149`) | [checkers](https://github.com/Gamer92000/echo-dot-assist/blob/main/devices/checkers/README.md) |
| Echo Show 8 1st gen (2019) | `C7H6N3` | 6.5.7.0 (`NS6570`, build `6074`) | [crown](https://github.com/Gamer92000/echo-dot-assist/blob/main/devices/crown/README.md) |
| Echo Show 5 2nd gen (2021) | `C76N8S` | 6574.1 (`NS65741`, build `8149`) | [cronos](https://github.com/Gamer92000/echo-dot-assist/blob/main/devices/cronos/README.md) |
| Echo Show 8 2nd gen (2021) | `A8H3N2` | 7574.1 (`PS75741.7080N`, build `7080`); the guide also covers builds `7077`, `7082` and `7806` | [athena](https://github.com/Gamer92000/echo-dot-assist/blob/main/devices/athena/README.md) |

**Echo Show 5 1st gen (`H23K37`):** Fire OS **6575.1 (`NS65751`, build `8848`)** blocks the unlock in
our guide. Our working setup used build `8149`. See the [Echo Show 5 setup notes](devices/echo/README.md#echo-show-5-1st-gen-on-an-ubuntu-live-usb).

**Echo Dot 3rd gen:** Look for `D9N29T`. The 2019 refresh (`C78MP8`) and the version with a clock
(`36EBT3`) are unsupported. See the [upstream Echo model list](https://github.com/Gamer92000/echo-dot-assist/blob/main/devices/MODELS.md).

## Project components

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
| `devices/echo/eye-app`, `extras/eye` | Hermes Eye: a small app that lets Hermes use the Echo Show's camera (snapshots, video mode, a REC badge), and the `look` tool for the voice profile. The setup skill builds it on your computer with your own key. |
| `extras/visual-memory` | Hermes Visual Memory: remembers what the Echo Show's camera saw, answers "what changed?" and "where did you last see …?", and shows before/after pictures on the Echo. Off until turned on; local SQLite; the vision model is called only when a steady scene changed. |
| `extras/agent-tasks` | A read-only view of the other Hermes agents' task boards for a voice profile. |
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
- **Tap to correct**: if it misheard you, tap the screen while it thinks or answers. It stops, says "Go
  ahead.", and listens again without the wake word; Hermes is told what it misheard.
- **Recipes**: with [Mealie](https://mealie.io) connected ([extras/mealie](extras/mealie/README.md)), recipes
  Hermes looks up open on the screen, and you cook from it: one big step at a time, "next step" by voice or
  a swipe, ingredients to tick off, a timer button on steps with a time.
- **Tap** the idle screen for an overview of scheduled jobs and recent sessions. Timer alarms get a big Stop button.
- **Camera (optional)**: with [Hermes Eye](docs/eye.md), Hermes can look through the Show's camera ("what am
  I holding?", "read this"). In **video mode** your live picture sits in a corner of the screen while you
  talk, like a video call. Say "video mode" to start it, or just ask Hermes to look. It ends when you say
  "stop video", after five quiet minutes, or when you close the shutter or press mute. After a few
  questions that don't use the camera, the screen asks if you're done with it.

**The camera and privacy.** A **● REC** badge shows on the screen whenever the camera is on. The camera's
shutter and the mute button always win: with the shutter closed Hermes is told it sees nothing, and mute
cuts the camera off in the Echo's kernel, where no app can override it. Pictures go to your voice profile's
model only when Hermes looks, and nothing is saved. The camera is off unless something asked for it in the
last 15 seconds, or video mode is on.

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

**Skill installation is currently blocked by Hermes' security scanner.** The command below is the
intended installation path, but it does not currently succeed for an unverified community skill.
Use the [manual install](#manual-install) while this is being resolved; do not disable scanning or
use a force override. The setup scripts and device guide remain available in this repository.
See [the scan findings and release check](docs/skill-installation.md) and [issue #4](https://github.com/majorminorlabs/hermes-voice/issues/4).

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

We tested Hermes Voice end to end on the Echo Dot 2nd gen (flashed from Windows) and Echo Show 5 1st gen
(flashed from an Ubuntu live USB), both with the "Hey Hermes" wake word and Home Assistant in Docker on macOS.
The remaining Echo models have upstream support and are untested with Hermes Voice. See the
[model and firmware table](#echo-compatibility-check-before-buying).

Other Home Assistant Assist satellites, such as Voice PE, should work with an Assist pipeline that uses
Hermes Voice as its conversation agent. These are untested by this project.

Flashing an Echo with echo-dot-assist can brick it. The Echo Show 5 setup uses a Linux PC and a 16 GB work drive.

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
