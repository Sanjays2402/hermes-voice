# A screen for the agent on the Echo Show — 2026-11-05

**Question.** The Echo Show has a screen. What should it show for a Hermes voice assistant, where does the
data come from, and how does it get onto the device without hacking it?

**Answer so far.** A page served by the hermes_voice integration, shown by a 13 KB app that is the Show's
home screen. Idle: the avatar's eyes behind the clock; on the wake word it zooms out to the whole avatar and
shows the question, Hermes' tools and the answer. Step 4 shows what Hermes is doing in the
background on the idle screen (section 5). Design: [docs/show-screen.md](../show-screen.md).

## 1. What the user wants (requirements, in their words where it matters)

- A screen for "the agent stuff", not home control. Kiosk Satellite (a Home Assistant dashboard) was removed
  for that reason.
- An avatar: Nous Research's "Nous Girl" (Nous is fine with it being used).
- While listening and thinking: the words as spoken, a thinking state, tool activity.
- Answering: the reply, with what speech leaves out.
- Idle (user's design): a close-up of the avatar's eyes as the background with the time; on the wake word it
  zooms out to the whole face on the right, the conversation on the left. Avatar on the right (user).
- Not the last question asked on the idle screen ("I genuinely don't know any time I would ever need to know
  the last question"). Instead: nothing, what is being worked on, what recently finished, or a suggestion.
- Background work defaults to the voice profile; an opt-in shows all profiles. Simple: a setting, or tell
  Hermes. "It needs to be something simple and not something they have to hack the hell out of."

## 2. Where the data is (evidence)

| Need | Source | How it was checked |
|---|---|---|
| Listening / processing / responding / idle | Home Assistant `assist_satellite.<echo>` state | Entity history during a real turn: idle → listening → processing → responding |
| Words while speaking | **Not available** | HA's pipeline events (`assist_pipeline/models.py`, HA 2026.10.4) have `stt-start`, `stt-vad-*`, `stt-end` only; the tested Whisper is not a streaming engine. Words appear when speech-to-text ends |
| What was heard, the reply as written, tools | hermes_voice itself (the turn text; streamed `TextDelta` and `ToolEvent`) | Code: `conversation.py` |
| HA's own answers (local handling) | HA chat log, `content_added` events | Live: a new conversation fires `created` with no content, then one `content_added` per message; HA records its local intent as an assistant message under the pipeline's agent with no text, then its answer under `conversation.home_assistant`. The first hook missed both; fixed and tested |
| Which Echo a chat log belongs to | **Not recorded** in the chat log | `ChatLog.as_dict()` has no device. Hermes turns carry `device_id`; for HA-local answers the device whose satellite is mid-turn is used |
| `conversation/chat_log/subscribe` | Admin-only, whole messages | `conversation/http.py` (`@require_admin`). Not used by the page |
| Scheduled jobs | Hermes gateway `GET /api/jobs` | Read-only call against the voice profile's gateway |
| Recent sessions | Hermes gateway `GET /api/sessions?limit=N` | Fields: title, preview, last_active, message_count, tool_call_count, source |
| Gateway status | Hermes gateway `GET /health/detailed` | active_agents, gateway_busy, today's requests/messages/tokens/latency |
| Memories | **Not on the gateway** | The dashboard's `GET /api/memory` returns provider and file sizes only |
| Background tasks | **Not on the gateway**; Hermes' kanban board | See step 4 |

The Show itself (read over adb, read-only): Fire OS 6 on Android 7.1.2 (API 25), 960×480 at 160 dpi, Amazon's
Chromium WebView 118. echo-dot-assist lets any launchable app be the home app; hassmic keeps a strip along
the top (its drawer) and the light band along the bottom (Back) whatever app is in front, and owns the
microphone. hassmic's own screen gets state from hassmic (patterns, sound level), never the words.

## 3. Decisions

- **One feed, from the integration.** Two websocket commands (`hermes_voice/screen/subscribe`,
  `hermes_voice/screen/overview`) so the Show needs only a **non-admin** Home Assistant token and never the
  Hermes key. Rejected: the page talking to Hermes (needs the key on the Show, no satellite state); HA's
  admin-only chat log (admin token on the Show, no word-by-word text).
- **The page is served by the integration** (`/hermes_voice/screen`), so there is nothing else to host.
- **Our own app, not Kiosk Satellite.** A minimal WebView app with network permission only. Kiosk Satellite
  would work without building anything but has adb-level access and updates itself.
- **System fonts only.** The Show is firewalled from the internet; Roboto is its system font.
- **The default avatar as a mask**, so each state tints it; custom pictures shown as they are.
- **Idle zoom by transform only** (scale and translate on one layer), for a slow device.

## 4. Built and tested

| Step | What | Tested |
|---|---|---|
| 1. Feed | `screen.py`, hooks in `conversation.py`, `client.async_get_json` | 6 tests; live: overview through a non-admin user, HA-local answer, a full Hermes turn from the Show (state, heard, reply word by word) |
| 2. Page | `screen/index.html`, `style.css`, `app.js`, default avatar with MIT notice; `?demo` | In a desktop browser at 960×480: every state in the demo; live with a web-search question on the Show: Listening → Thinking (question) → Searching the web → Reading a page (forecast.weather.gov) → answer streaming, about 17 s from wake word to answer |
| 3. App | `devices/echo/screen-app/` (AndroidManifest, one activity, `build.sh`: aapt2, javac, d8, zipalign, apksigner) | Installed over echo-dot-assist's debug access, started with the page address; screenshot from the Show; set as the home app |

Findings while building:

- A static folder URL is not served by HA (403): `/hermes_voice/screen` redirects to `index.html`.
- The demo followed a device id stored by an earlier live test, so it never showed "listening"; the demo now
  ignores stored devices.
- The tool chip showed a full URL; web addresses now show their site.
- hassmic lists launchable apps for the Screen page only when its screen process starts, so an app installed
  later is missing from the list. Killing the screen's `app_process` (a child of `/system/hassmic/hassmic`)
  restarts it with the new list; voice is unaffected (its port stayed up).
- A design linter flagged a side-border accent (removed), animating width/height (removed), and Roboto
  (kept: the Show's system font, offline).

## 5. Step 4: background work on the idle screen

**Question.** Hermes keeps working in the background (scheduled jobs, tasks on its board). How can the idle
screen show what is running and what just finished, by default only for the voice profile, optionally for
all profiles, without anything a user has to hack together?

**Evidence (2026-11-05, Hermes Agent as installed on the test Mac).**

- Hermes' task board is **kanban**, "durable SQLite-backed task board shared across Hermes profiles"
  (`hermes kanban --help`). One database per installation, boards inside it.
- `hermes kanban list --status <s> [--assignee <profile>] --json` and `hermes kanban boards list --json` are
  supported CLI commands with JSON output. Task fields (`hermes_cli/kanban_output.py`, `_TASK_DICT_FIELDS`):
  id, title, assignee, status, created_at, started_at, completed_at, result and more. Statuses: triage,
  todo, ready, scheduled, running, blocked, review, done, archived.
- **The gateway API has no kanban endpoint**, and gateway plugins cannot add HTTP routes
  (`hermes_cli/plugins.py`: tools, hooks, middleware, platforms, but no routes).
- **The dashboard server has one** (`plugins/kanban/dashboard/plugin_api.py`: `/board`, `/workers/active`,
  `/stats`, …, mounted at `/api/plugins/kanban/`), but it authenticates with a per-process session token
  injected into the dashboard page (`hermes_cli/web_server.py`); it changes on every restart. Rejected as
  fragile.
- Home Assistant runs in Docker and cannot run `hermes` commands.
- Job runs (`cron/executions.py`) record status, started_at, finished_at and error, but no output text.
- On the test installation the board was empty at the time of writing (one board, `default`, no tasks).

**Decision.** A small read-only helper next to Hermes (`screen_activity.py` in the skill, standard library
only) that runs the supported `hermes kanban` commands and answers one request: what is running and what
finished recently. It listens on this computer only and accepts only the voice profile's existing API key
(which only Home Assistant holds). Scope is chosen in Home Assistant (Hermes Voice → Configure → *Background
work on the screen*: off / voice profile, the default / all profiles), and the skill can set it, so a user can
just ask Hermes. Suggestions ("next up") come later, as an opt-in scheduled job in a profile that knows the
user's work.

**Alternatives considered.** Reading the kanban database file directly (couples to its schema; the CLI is the
supported interface); the dashboard API (fragile token); asking Hermes in a chat turn (a model call per
refresh); a request to Nous for gateway endpoints (the clean long-term fix; worth filing either way).

**Built.**

| Part | What |
|---|---|
| Helper | `skill/hermes-voice-setup/scripts/screen_activity.py`: `GET /activity?scope=profile\|all` (Bearer = the profile's `API_SERVER_KEY`), `GET /health`; runs `hermes kanban boards list` and `hermes kanban --board <b> list --status <s> [--assignee <profile>] --json` for running, blocked, review and done; passes on id, title, assignee, status and times only (no body, result or paths); "just finished" = done in the last 3 hours; at most 6 per group. `--check` prints the answer; `--install` / `--uninstall` a login service (launchd on macOS, systemd user unit on Linux) |
| Integration | Option *Background work on the screen*: Off / This profile only (default) / All profiles; option *Activity helper port* (8660). The overview asks the helper on the gateway's host with the entry's key and passes on the same fields |
| Page | Idle: up to three lines, running first (◐, with how long), then *Ready for your review* / *Blocked* (●), then finished (✓, how long ago); the profile name only when all profiles are shown; else the next scheduled job; else nothing. The last question is no longer shown. The overview's first column is *Work* (the same items, then scheduled jobs). The page refreshes the overview every 30 s while idle |
| Skill | `ha_setup.py --screen-activity off\|profile\|all`; SKILL.md: offer the screen with an Echo Show, install the helper, and before switching to *all*, tell the user that anyone who sees the Show will see every profile's task titles |

**Tested.**

- 9 new tests (97 in all): profile scope passes `--assignee`, all scope does not; archived boards skipped;
  finished tasks older than 3 hours dropped; only display fields leave the helper; a bad scope and a failing
  `hermes` are errors; the HTTP handler refuses no key or a wrong key (401) and a bad scope (400); the cache;
  the integration asks the helper with the entry's key on the gateway's host, asks nothing when off, asks
  for `scope=all` when chosen, reports an unreachable helper as an error instead of failing the overview.
- Live on the test Mac (Hermes Agent installed, one empty board): the helper as a launchd login service
  answered `/health`, refused a request without the key (401), and returned an empty activity with the key;
  Home Assistant in Docker reached it at `host.docker.internal:8660`; the overview through the non-admin
  screen user returned the activity with no errors.

**Problem found and fixed.** The first overview after a restart timed out. *Measured:* each `hermes
kanban` call takes about 1.2 s (the CLI's start-up), and the helper made five in a row, about 6 s against
the integration's 5 s limit. *Fix:* the queries run in parallel (a cold answer now takes about 2.8 s), the
helper answers from its cache and refreshes in the background after 15 s, and the integration allows the
helper 15 s.

**Not yet tested.** A real running or finished task on the screen (the board was empty; creating a test
task touches the shared board and could start a worker, so it waits for the user's go-ahead). The demo
(`?demo=idle`) shows all three kinds with sample tasks.

**Next.** Suggestions ("next up"): an opt-in scheduled job in a profile that knows the user's work, its
latest line passed on by the helper. And a request to Nous for read-only kanban and run endpoints on the
gateway, which would make the helper unnecessary.
