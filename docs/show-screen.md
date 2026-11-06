# Hermes screen for the Echo Show

Status: steps 1–4 are built and run on an Echo Show 5 as its home screen (the feed, the page, the app:
install steps in [devices/echo/README.md](../devices/echo/README.md#hermes-screen-on-an-echo-show); background
work: [Background work](#background-work); suggestions: [Next up](#next-up)). Memories are not built. The research record is
[docs/research/2026-11-05-hermes-screen.md](research/2026-11-05-hermes-screen.md).

To remove the screen or turn parts of it off, see [what Hermes Voice changes, and how to undo each](changes-and-undo.md#the-hermes-screen).

## Timers, mute and Do not disturb

On an Echo with hassmic the screen shows running voice timers counting down (top of the idle lines), fills
the screen with *Timer done* and a **Stop** button while one rings (it presses the Echo's own "Stop
ringing" button), and shows *Mic off* and *Do not disturb* next to the label.

## Background work

The idle screen shows what Hermes is doing on its task board (kanban): tasks running, ready for your review
or blocked, and finished in the last 3 hours. Without any, it shows the next scheduled job, or nothing.

- **Who sees what.** Hermes Voice → Configure → *Background work on the screen*: **This profile only**
  (default: tasks assigned to the voice profile, the agent you set up for the device), **All profiles**
  (every profile's task titles, to anyone who can see the screen), or **Off**. Or ask Hermes: the skill runs
  `ha_setup.py --profile voice --screen-activity all|profile|off`.
- **Careful with test tasks.** A Hermes gateway with the kanban dispatcher (the voice profile's did) picks up
  tasks in *ready* or *review* by itself and runs workers. For demos, create tasks with
  `--initial-status blocked`, finish them with `hermes kanban complete <id> --force`, and archive them after.
- **How.** Home Assistant cannot run `hermes` and Hermes' API has no task-board endpoint, so a small
  read-only helper runs next to Hermes: `screen_activity.py --profile voice --install` (in the skill). It
  runs `hermes kanban ... --json`, listens on 127.0.0.1:8660, accepts only the voice profile's API key, and
  passes on titles, owners, status and times, never task bodies or results. `--check` prints what the
  screen would get.

## Next up

Opt-in: one short suggestion under the work lines, for example "→ Next up: Test voice response speed on the
Echo Show", written by the voice profile a few times a day from its task board and what it remembers.

```bash
python3 skill/hermes-voice-setup/scripts/screen_activity.py --profile voice --next-up on   # or off
```

- **What it does.** Puts a small read-only script in the profile's `scripts/` folder (it lists the profile's
  tasks: open ones and those finished in the last day) and creates a Hermes scheduled job, *Hermes Voice:
  next up*, at 8:00, 12:00, 16:00 and 20:00 (`--next-up-schedule` to change; `hermes -p voice cron run <id>`
  for one now). The job's answer stays on this computer (`--deliver local`); the helper passes on the
  latest line, if it is under 12 hours old, as `next_up` in `/activity`. The answer *NONE* shows nothing.
- **Tools.** Scheduled jobs in a profile get every tool by default, the terminal and computer use included.
  This job needs none, so turning it on limits the voice profile's scheduled jobs to `todo` (task planning,
  harmless), after backing up `config.yaml` to `hermes-voice-backups/`. Undo: [changes-and-undo.md](changes-and-undo.md#the-next-up-job).
- **Cost.** One short model call per run, four a day by default.
- **Privacy.** The prompt asks for one line with no private details, other people's names, money or health:
  anyone in the room can read the screen.
- **Where it shows.** Idle: last of the three lines (timers and work above it). Overview: first in *Work*.
  The job itself is not listed as "Next: …" on the idle screen.

Idle shows a close-up of the avatar's eyes behind the clock (zoom 8×; `?zoom=`, and `?eyes=x,y` for where the
eyes are in a custom picture); on the wake word it zooms out to the whole avatar on the right, and back after
the answer. Only transform and opacity animate.

## Using the page

Open `http://<home-assistant>:8123/hermes_voice/screen` in a browser (960×480 is the Show 5's size).

- **Login**: once with `#token=<long-lived token>` in the address; the page keeps it in that browser and
  removes it from the address. Without one it uses Home Assistant's own login in that browser. Use a
  **non-admin** Home Assistant user for a screen: the page needs nothing more.
- **Which Echo**: `?device=<device id>` follows one voice device (the id is in the device page's address in
  Home Assistant). Without it, the page follows whichever device spoke last.
- `?hold=<seconds>`: how long an answer stays up after the Echo goes quiet (default 60).
- `?demo` plays a scripted conversation without Home Assistant; `?demo=idle` stays on the idle screen.
- **Avatar**: put `avatar.svg` / `avatar.png` (or `.webp`, `.jpg`, `.gif`) in `<config>/www/hermes_voice/`
  to replace the default; `avatar-listening.*`, `avatar-thinking.*`, `avatar-answering.*` replace it in
  that state (Home Assistant serves `www/` only if the folder existed when it started). Or `?avatar=<url>`.
  The default is shown as a tinted mask; your own pictures are shown as they are.
- Tap the idle screen for the overview; it closes by itself after 90 seconds or when the Echo hears you.

Tested on 2026-11-05: "What is the weather like in Pensacola, Florida?" on the Echo Show 5 showed
Listening, Thinking with the question, *Searching the web* and *Reading a page* (forecast.weather.gov) with
ticks, then the answer streaming in, about 17 seconds from wake word to answer. Target: the Echo Show 5 1st gen running echo-dot-assist (hassmic), tested
with Hermes Voice on 2026-11-05. The same page should work on other Shows and in a desktop browser.

## Goal

A screen for the agent, not for the house. hassmic's built-in face shows that the Echo is listening or
speaking; a Home Assistant dashboard shows devices. This screen shows **Hermes**: what you asked, what it is
doing (tools), what it answers, and what it has going on (scheduled jobs, recent conversations).

Four views:

| View | When | Shows |
|---|---|---|
| **Idle** | most of the time | The avatar, the time, one line of context: the next scheduled job, or the last thing asked |
| **Listening / thinking** | wake word heard → reply starts | The avatar in a listening, then thinking state; your words once speech-to-text has them; tool activity ("Searching the web…") |
| **Answering** | while the reply plays, then for a while | The reply in large text as it streams; details that were not spoken (links, lists) stay on screen afterwards |
| **Agent overview** | swipe or tap | Scheduled jobs, recent conversations, gateway status; later memories and background work (see [What Hermes would need to expose](#what-hermes-would-need-to-expose)) |

Sketch of the answering view (960×480):

```text
┌──────────────────────────────────────────────────────────────┐
│  (avatar)   Hermes · answering                     3:53 PM   │
│                                                              │
│  "How many days until Christmas?"                            │
│                                                              │
│   There are 50 days until Christmas,                         │
│   a Friday this year.                                        │
│                                                              │
│  ✓ checked the date                                          │
└──────────────────────────────────────────────────────────────┘
```

## The device

Read from the tested Show 5 over adb (read-only):

- Fire OS 6 on Android 7.1.2 (API 25), screen **960×480** landscape at **160 dpi** (1 CSS px = 1 device px).
- WebView: Amazon's Chromium WebView **118** (`com.amazon.webview.chromium`, the system WebView provider).
  Modern CSS, ES2022, WebSocket and SVG all work.
- echo-dot-assist lets **any installed app be the home app**: its settings page's *Screen* page lists every
  launchable app; hassmic opens the chosen one at start and brings it back when it ends (three times a minute
  at most). hassmic keeps two strips for itself whatever app is in front: a few rows along the **top** (pull
  down opens its drawer: Face, Music, Do not disturb, volume) and the **light band along the bottom** (swipe =
  Back). The layout must leave both free.
- **hassmic owns the microphone.** The screen app must not ask for it; it only displays.
- hassmic's own screen UI receives only state from hassmic (listening/thinking/speaking patterns, a sound
  level, mute, timers, music). It never gets the words, so the words have to come from Home Assistant.

## Where the data comes from

| Data | Source | Notes |
|---|---|---|
| Listening / thinking / speaking / idle | Home Assistant: the Echo's `assist_satellite.<echo>` entity state (`idle`, `listening`, `processing`, `responding`) | Live via HA's websocket (`subscribe_entities`). |
| What you said | `hermes_voice` sees each turn's text (`user_input.text`); for turns Home Assistant answers itself (*Prefer handling commands locally*), HA's chat log has it | Final text only, see [Limits](#limits). |
| Hermes's reply, word by word | `hermes_voice` already streams it (`conversation.py`, `_stream_deltas`) | Only the integration sees the deltas: HA's chat log subscription (`conversation/chat_log/subscribe`) sends whole messages, not deltas, and it is admin-only. |
| Tool activity | `hermes_voice` already gets Hermes's tool events (`client.py` `ToolEvent`, logged as external tool calls) | Name and start/end; a friendly label per tool ("web_search" → "Searching the web…"). |
| HA's own answers (time, timers) | HA chat log, read inside the integration with `async_subscribe_chat_logs` | Filtered to the Echo's device id. |
| Scheduled jobs | Hermes gateway `GET /api/jobs` | Exists today (checked against the voice profile's gateway). |
| Recent conversations | Hermes gateway `GET /api/sessions?limit=N` | Exists: `title`, `preview`, `last_active`, `message_count`, `tool_call_count`, `source`. |
| Gateway status | Hermes gateway `GET /health/detailed` | Exists: `active_agents`, `gateway_busy`, today's requests/messages/tokens/latency. |
| Skills | Hermes gateway `GET /v1/skills` | Exists. |
| Memories, background work | not exposed | See below. |

### Recommended shape: one feed from the integration

`hermes_voice` adds two websocket commands to Home Assistant and serves the page itself:

- `hermes_voice/screen/subscribe` (`device_id`): one live stream per Echo with
  `state` (from the satellite entity), `heard` (your words), `delta` (reply text), `tool_start` / `tool_end`,
  `reply_done`, `error`, and HA-local turns (`local_answer`). The integration already has every piece except
  the satellite state and the chat-log hook, both a few lines.
- `hermes_voice/screen/overview`: jobs, recent sessions and health, fetched from the Hermes gateway by the
  integration with the key it already stores, cached for a minute. **The Hermes API key never reaches the
  Show.**
- The page itself as static files from the integration (`/hermes_voice/screen/`), so there is nothing else
  to host.

The Show then needs only a Home Assistant token, for a **dedicated non-admin HA user** ("Hermes Screen"):
the two commands do not require admin, and the admin-only chat log stays inside the integration.

Alternatives considered: the page talking to Hermes directly (needs the Hermes key on the Show, and Hermes
does not know the Echo's listening state); the page using HA's admin-only chat log (needs an admin token on
the Show, gets no deltas).

## How it runs on the Show

| Option | How | For | Against |
|---|---|---|---|
| **A. Our own small app (recommended)** | A minimal Android app: one full-screen WebView loading the page, no permissions beyond the network, set as the home app on the Screen page | Clean, small, nothing third-party; we control reconnects, screen-on, the top/bottom strips | Needs an Android build (SDK build tools, or a GitHub Actions build) and `adb install` (echo-dot-assist's debug access opens adb over Wi-Fi for 30 min) |
| B. Kiosk Satellite | Reinstall it (`scripts/kiosk-install.sh`), point it at the page | No app to build | Third-party app with adb-level access that updates itself; was removed from the test Show on purpose |
| C. Desktop browser | Open the page on any computer | Development and demos | Not the Show |

Plan: build and test the page in a desktop browser at 960×480 first (C), then the small app (A).

Setup on the Show (app A): `adb install`, then give it the Home Assistant URL and the screen user's token once
(over adb, or a one-time setup page on the Show), then pick it on echo-dot-assist's Screen page. The drawer
from the top edge still reaches hassmic's face and music.

## The avatar

- Default: **Nous Girl**, the Nous Research character that ships with Hermes Agent
  (`assets/nous-girl-black.svg`, `assets/nous-girl-white.svg` in hermes-agent, MIT, © Nous Research). Credit
  it in the page and the repo. One static drawing, black on white or white on black.
- **Swappable**: the page loads `avatar.svg` or `avatar.png` from the Home Assistant config folder
  (`<config>/www/hermes_voice/`) if present, else the default. Optional per-state images
  (`avatar-listening.*`, `avatar-thinking.*`, `avatar-speaking.*`) replace it in that state.
- States without new artwork, in CSS: a soft breathing glow when idle, a ring that reacts while listening,
  orbiting dots while thinking, a gentle pulse while speaking, a dim red tint when muted. Later, the SVG's
  layers could be animated (blink, hair, headphones lighting up) or a few extra poses drawn.

## What Hermes would need to expose

For the overview, checked against the Hermes gateway the voice profile runs:

| Want | Today | Needed |
|---|---|---|
| Scheduled jobs | `GET /api/jobs` | nothing |
| Recent conversations | `GET /api/sessions` | nothing |
| Busy / idle, today's usage | `GET /health/detailed` | nothing |
| **Memories** | Not on the gateway. The dashboard's `GET /api/memory` (separate `hermes` web server) returns only the provider and file sizes. | A read-only gateway endpoint, e.g. `GET /api/memory/entries` (the built-in `MEMORY.md` / `USER.md` entries, newest first, limited), behind the API key. Opt-in on the screen: it is a shared screen in a room. |
| **Background work** (what Hermes is doing now, across sessions) | Runs can be read only by id (`GET /v1/runs/{id}`, `…/events`); no list. | A list of active runs or sessions, e.g. `GET /v1/runs?status=running`, with title and current tool. Until then: `active_agents` and sessions' `last_active`. |
| Kanban / plans | In the profile's databases, not on the API | Optional, later. |

Asking upstream (Nous Research) for the two endpoints is the clean path; a Hermes plugin could add them
meanwhile, without changing hermes-agent itself.

## Limits

- **Words while you speak.** Home Assistant's pipeline reports speech-to-text only when it ends
  (`stt-end`): there is no partial-transcript event, and the tested Whisper (Wyoming faster-whisper) is not a
  streaming engine. So the screen shows "Listening…" with a moving ring while you talk, and your words appear
  right after you stop (about the speech-to-text time, under a second here). Live words would need a streaming
  speech-to-text engine and Home Assistant passing partial results through; revisit when HA supports it.
- **Sound level.** hassmic's own face reacts to your voice level, but the level is not sent to Home
  Assistant, so this page cannot react to it.
- **Privacy.** The screen shows what is said, in a shared room. Options: hide the transcript, clear the
  answer after N minutes, keep the overview opt-in, and never show memories by default.
- **One Echo, one page.** Each Show subscribes to its own device id; a desktop browser can watch any Echo.

## Build plan

1. **Integration feed**: the two websocket commands, the chat-log hook for HA-local answers, the static
   page route, tests (pytest-homeassistant-custom-component, as the rest of the integration).
2. **Page**: idle, listening/thinking, answering views at 960×480, avatar with CSS states, reconnect logic;
   tested in a desktop browser against the test Home Assistant.
3. **Show app**: the minimal WebView app, its one-time setup, install and home-app selection; documented in
   `devices/echo/README.md`.
4. **Overview**: jobs, sessions, health; memories and background work once Hermes exposes them.

Each step is separately testable; step 3 is the only one that touches the Show.
