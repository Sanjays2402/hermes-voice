# Hermes screen for the Echo Show (design)

Status: step 1 (the integration's live feed) is built and tested live on an Echo Show 5; steps 2–4 are not built yet. Target: the Echo Show 5 1st gen running echo-dot-assist (hassmic), tested
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
