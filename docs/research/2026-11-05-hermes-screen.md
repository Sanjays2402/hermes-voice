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

**Live check with a real task (18:05).** One task created on the real board with
`hermes kanban create "Hermes screen test (safe to archive)" --assignee voice --initial-status blocked`
(created blocked, so no worker can pick it up). The helper listed it under `attention` and the live page
showed "● Blocked: Hermes screen test (safe to archive)" under the clock.

## 6. Keeping the Show's page current

**Problem.** The Show keeps the page it loaded at start. After an update of the integration it went on
running the old page: the new idle lines never appeared on the device.

**Fix, first attempt.** The subscribe snapshot carries a page version (a hash of `index.html`, `app.js` and
`style.css`, taken when Home Assistant starts); a page that reconnects and sees a different version reloads.
*Test:* restart Home Assistant with the files unchanged → no reload (correct); change `style.css`, restart →
**no reload** (wrong).

**Cause.** The page never reconnected: with Home Assistant in Docker Desktop, a restart left the browser's
websocket looking open (Docker Desktop's port forwarding keeps the client side alive), so `onclose` never
fired. The same would strand the Show after a Wi-Fi drop.

**Fix.** A heartbeat: the page sends Home Assistant's `ping` every 30 s; without a `pong` within 10 s it runs
its close handling at once and reconnects. *Tests:* 75 s idle with heartbeats → no reconnect, no reload
(correct); change `style.css`, restart Home Assistant (up at 18:10:21) → the page reloaded itself at
18:10:24 (correct).

**Caveat.** A Show still running a page from before this change has neither, so it needs one reload by hand
(power the Show off and on; it starts Hermes Screen again with the saved address and login).

## 7. Demo tasks for a video, and what they revealed

**Goal (user).** Realistic tasks on the screen for a demo video: client work for a shop, this project, and
more. Real Hermes tasks, not a fake data source.

**Finding: the gateway dispatches tasks by itself.** The user's understanding was that nothing picks up
tasks automatically, and no separate `hermes kanban daemon` process was running. But the voice profile's
gateway has the kanban dispatcher built in. After one demo task was put up for review (`unblock` → `claim` →
`request-review`), the gateway, within a minute and with no further command:
1. claimed it for review as the voice profile (run 5) and requested changes ("no reviewable draft was
   delivered");
2. moved it back to ready, claimed it again and spawned a worker (run 6) to write the draft, spending model
   calls.
The task could not be blocked while in review; it was archived, which stopped it. Two workers had run.

*Safe on this setup:* tasks created with `--initial-status blocked` (never dispatched), tasks completed with
`complete --force` (done), and one task claimed by hand with a long TTL (shows as running; no worker was
spawned for it). *Not safe:* anything put in `ready` or `review`; the gateway picks it up.

**Finding: a busy board can stall the helper.** While tasks were being written in bulk, one background refresh
timed out (`hermes kanban boards list` over 20 s). The helper kept serving its last good answer and the next
refresh worked, as designed; the screen was up to a minute behind.

**Finding: "degraded" was the Mac's internal disk.** Hermes' readiness check reported 97.5 % used on the disk
Hermes lives on (the internal drive: 460 GB, 12 GB free); the large data drive was 20 % used and is not where
Hermes keeps its files. The overview now says why a gateway is degraded ("low disk (98%)") instead of the bare
word. Also: Hermes' "today" counters use the UTC day, so they reset at 00:00 UTC.

**Changes from user review of the screen.**
- The overview's middle column shows **finished work** (done tasks, last 3 hours) instead of recent chat
  sessions. Chat titles ("Check current time", "Confirm previous message") are not worth a screen, the same
  reasoning as dropping the last question from the idle screen.
- The *Work* column shows running and blocked or review tasks, then scheduled jobs.

## 8. The Show kept an old page; the screen was dim

**Observed (user).** After a power cycle the Show showed the old page: no task lines, and the old "Recent"
column of chat sessions, which no longer existed in the current page. The page in a desktop browser was
current.

**Cause.** Home Assistant serves static files without cache headers, so the Show's WebView kept its copies
(`index.html`, `app.js`) and reused them without asking again. The auto-reload of section 6 could not help:
the cached page was older than that code. The ThinkPad (asked about) plays no part after the install; the
page comes over Wi-Fi from Home Assistant.

**Fix, server.** `/hermes_voice/screen` (and `/`, `/index.html`, every address a screen may have saved) is
now served by the integration with `Cache-Control: no-store`, and loads `app.js?v=<version>` and
`style.css?v=<version>` (the page version), so a new version is always a new address. Checked with curl:
both addresses answer 200, `no-store`, versioned addresses. *Not enough on its own:* a power-cycled Show
still showed the old page, because it never asked the server for the page again.

**Fix, app 0.2.** The app clears its WebView cache every time it starts (`WebView.clearCache(true)`; the
page is small). Installed over debug access (`adb install -r`, same signing key); the Show then showed the
task lines (screenshot taken over adb).

**Dim screen.** hassmic sets only the light band's brightness and dims its own face; nothing sets the
screen's brightness while another app is in front. App 0.2 sets its window to full brightness by default and
gives the page a bridge (`HermesScreen.setBrightness`, `getBrightness`, remembered by the app); the overview
shows a brightness slider only inside the app.

**Brightness, measured (19:10).** Over adb: `mScreenBrightnessOverrideFromWindowManager=255`, and
`/sys/class/leds/lcd-backlight/brightness` 255 of `max_brightness` 255. The app's full brightness had taken
effect; the panel is simply not bright. What looked dim was the page: a near-black background, the avatar at
34 % behind the clock, grey secondary text.

**Iterations with the user, on the device.** A brighter page (lighter background, avatar at 62 %) washed out
the idle text; halfway between was tried next. The user's call: **text always at full brightness; the slider
adjusts only the background** (the avatar behind the clock, 10 % to 85 % opacity, default in the middle),
remembered by the page; the backlight stays at its maximum (the page resets the app to full, since the earlier
slider could have lowered it). Confirmed on the device by screenshot.

**Auto-reload confirmed on the device.** Each of these steps was deployed by copying the page and restarting
Home Assistant; the Show reloaded the new page by itself within about a minute every time (section 6), with no
adb and no power cycle.

**Correction (user, 19:18).** The slider was misread as a background control. What is wanted: the slider
dims **the whole screen** (it may sit on a bedside table); the idle screen's background carries a fixed,
gentle darkening so the clock and tasks stand out; on the wake word it zooms out and everything is at normal
brightness; text is at full brightness relative to the slider. *Built:* the slider sets the backlight through
the app (remembered by the app; down to the app's floor of 5 %); in a plain browser it dims the page with a
CSS filter; the idle avatar is at a fixed 45 %; the page no longer resets the backlight on load (that would
undo a bedside setting at every reload).

*Confirmed by the user on the device (19:22): the slider dims the whole screen, low enough for a bedside table.*

## 9. The running marker, and the label

**Problem.** The running task's marker, the character ◐, rendered as a tiny half-moon on the Show (its font
lacks the glyph; the fallback is small). Everything below was checked with screenshots taken over adb.

| Try | Result on the Show (Chromium WebView 118) | In a desktop browser |
|---|---|---|
| ◐ character, spinning | tiny glyph | fine |
| CSS ring (border with `color-mix`) | nothing | fine |
| CSS ring (plain border colours) | nothing | fine |
| SVG ring, spinning | nothing | fine |
| SVG ring, not animated | nothing | fine |
| ● character (as the blocked marker) | visible | fine |

Two confounders were ruled out on the way: the page itself was current (a label change in the same deploy
showed up), and the device log (`adb logcat`) showed no script errors. The ring's failure was not explained;
● is what the Show demonstrably draws. *Decision:* the running marker is a purple ● that pulses (opacity, 2.1 s
per cycle after the user asked for it 30 % slower than 1.6 s). Blocked stays an amber ●, finished a green ✓.

Also learned: after a deploy, restarting the app over adb before Home Assistant was fully up could leave it
on the previous page; restarting Home Assistant first, then the app, always gave the current page.

**Label (user).** The status line top left and the overview's title say "Hermes Voice" instead of "Hermes".

## 10. Timers, mute and Do not disturb

**Gap.** hassmic's own face shows a Stop button while a timer rings, mute and Do not disturb; the agent
screen showed none of them.

**Evidence.** hassmic exposes, per Echo, entities with fixed original names (checked in the entity registry):
`switch` "Mute", `switch` "Do not disturb", `binary_sensor` "Timer ringing", `sensor` "Ringing timers" (the
names), `button` "Stop ringing". Running voice timers live in Home Assistant's timer manager
(`hass.data["intent.timer"]`, `TimerInfo`: id, name, seconds, `seconds_left`, `created_seconds`,
`is_active`, `device_id`); it allows one handler per device, and the Echo's integration holds it, so the
screen reads the manager every 2 s instead (semi-private; noted as a risk).

**Built.** The subscribe feed sends each followed Echo's mute, Do not disturb, ringing timer and its names
(snapshot and `device` events), and its running and paused timers with an end time (`timers` events, always
one at the start). A new command `hermes_voice/screen/stop_timer` presses only that Echo's "Stop ringing"
button. The page: timers count down at the top of the idle lines; a ringing timer covers the screen with
"Timer done", its name and a large Stop button; "Mic off" (red) and "Do not disturb" badges next to the
label, and the status dot turns red when muted.

**Tested.** 3 new tests and 4 updated (100 in all). Live, with timers set on the Show through Home Assistant's
own handler (`conversation/process`, agent `conversation.home_assistant`, the Show's device id; no Hermes
call): the countdown ticked on the page; at zero the page showed "Timer done · 20 s" and Stop; Stop pressed
the Show's button and the ringing ended within 10 ms (history: `timer_ringing` on 02:28:45.77, Stop pressed
02:28:57.02, off 02:28:57.03 UTC). An earlier 25 s timer was also stopped through the button, 10 s into
ringing, apparently from the Show itself.

**Found on the way.** The first test run hung for 10 minutes: the first `timers` message was not sent when
there were no timers (an empty list compared equal to "nothing sent yet"), and a test waited for it forever.
The feed now always sends the first list, and `tests/run.sh` gives each test 60 s (`--timeout 60`).

## 11. Listening animation

Words cannot appear while you speak (section 2, and no partial speech-to-text in Home Assistant 2026.10.4),
and the Echo does not send its sound level to Home Assistant, so nothing on the page can follow the voice.
*Decision (user):* voice bars, seven bars beside "Listening…", each bouncing at its own pace (0.47–0.83 s,
`transform: scaleY` only). Checked in the demo; on the Show by the user.

## 12. Speed: measured, changed, measured again

Timings from Home Assistant's pipeline debug (`assist_pipeline/pipeline_debug/get`; kept in memory, so they
start empty after a restart) and the voice profile's `agent.log`. Same question, "How did we rescue the Echo
Dot?", seconds from the wake word:

| Stage | Before | Aggressive end of speech + persona | + blank lines, 60-char first sentence |
|---|---|---|---|
| speaking + end-of-speech wait | 3.9 | 2.6 | 2.9 |
| speech-to-text (Parakeet) | 0.27 | 0.18 | 0.19 |
| to Hermes' first sentence | 3.0 | 2.5 | 2.6 |
| voice starts (HA `tts_start_streaming`) | 8.8 | 8.0 (waited 1.0 s for 60 chars) | 7.3 |

Findings:
- **Hermes is about 3 s of a 9-10 s turn**: 0.6-0.7 s from speech-to-text to Hermes starting the turn, 1.6 s
  to the model's first words (gpt-6.1-sol, reasoning low), 0.7 s more to the end of the first sentence.
- **End of speech**: the Echo's "finished speaking detection" set to aggressive cut about 1.3 s for the same
  sentence. Two mishearings appeared in the same session ("the Dapples", "Hermes agent book"); the user
  mumbles, so not attributed to the setting.
- **Home Assistant starts streaming speech only after 60 characters** (`STREAM_RESPONSE_CHARS`), so a short
  first sentence waits for the second. The persona asks for a first sentence of ten to fifteen words.
- **Piper speaks a sentence only when the next one starts** (Wyoming Piper's `SentenceBoundaryDetector`: a
  boundary needs the next sentence's capital letter, or a blank line). Speech ran a sentence or two behind,
  the screen showed the whole answer before any sound, and "One moment." stayed silent through a web search.
  Each spoken piece now ends with a blank line (voice cleanup on only: with it off, pieces are tokens).
- **Speak at the first comma was dropped**: Piper would hold the clause until the sentence ends anyway.
- **Parakeet ignores an initial prompt** (the server's sherpa handler accepts and drops it), so names cannot
  be primed without switching to a Whisper model.
- **The speaker woke itself** mid-reply, saying "the latest Hermes Agent release": microWakeWord on the Echo
  keeps listening while it speaks (echo-dot-assist's guide lists this). Spoken text now swaps the name and
  the persona says "I". Muting the mic while it speaks was considered and rejected: it would end barge-in
  ("Hey Hermes, stop") and needs a change on the Echo. *Result:* with both changes the user reported the
  persona "worked well". The swap is an option (*Don't say the name aloud*, on by default) for anyone using
  another wake word.
- **Fast, jumbled playback** of the first sentence or two happens now and then (the user: perhaps one in twenty
  replies; seen on the Dot before these changes). Not reproduced on demand; to be traced from the pipeline
  record of the next occurrence.

## 13. What changes, and how to undo it

Every change Hermes Voice makes (Home Assistant options and settings, the voice profile, the helper, the
Echo, the screen app) is listed with its undo in [changes-and-undo.md](../changes-and-undo.md), also
shipped in the skill so an agent can read it before removing anything.

## 14. Next up: a suggestion on the idle screen

**Question.** The user wanted the idle screen to offer a suggestion as well as background work, written by
the voice profile (the user's choice, 2026-11-05).

**Evidence.**
- Hermes has its own "suggestions" (`cron/suggestions.py`), but those are proposed *scheduled jobs* the user
  accepts with one tap, not a line of advice. Not this.
- `hermes cron create` runs a prompt on a schedule, can run a script from the profile's `scripts/` folder
  first and put its output in the prompt (`--script`), and with `--deliver local` keeps the answer in
  `<profile>/cron/output/<job id>/<time>.md`: a header, `## Prompt` (with the script output), then
  `## Response` and the answer. Hermes tells jobs they may answer `[SILENT]`.
- `hermes tools list --platform cron` for the voice profile: every toolset on by default (terminal, files,
  code execution, computer use, delegation, cron jobs). A job that writes one line needs none of them.

**Decision.** A scheduled job, *Hermes Voice: next up*, in the voice profile at 8:00, 12:00, 16:00 and
20:00. Its script lists the profile's tasks (read-only `hermes kanban list`); the prompt asks for one line of
at most 60 characters, starting with a verb, nothing private (the screen is in a room), or NONE. The helper
reads the newest answer under 12 hours old (a failed run falls back to the one before) and adds it to
`/activity` as `next_up`; the integration passes on only its text and time. Opt-in, one command on or off.
Scheduled jobs in the voice profile are limited to `todo` when it is turned on, with `config.yaml` backed up.
*Alternatives:* the helper asking the gateway for a line itself (it holds the key; but a hidden model call per
refresh and a session per call, with no way for the user to see or pause it in `hermes cron list`); a
suggestion per screen refresh (cost).

**Built.** `screen_activity.py --next-up on|off` (`--next-up-schedule`), `read_next_up()`, `next_up` in
`/activity` and in the overview; the page shows "→ Next up: …" as the last idle line and first in the
overview's *Work*, and no longer lists the job as "Next: …". Docs: [show-screen.md](../show-screen.md#next-up),
[changes-and-undo.md](../changes-and-undo.md#the-next-up-job).

**Tested.**
- 10 new tests (120 in all): newest answer, markdown stripped; a failed run falls back; NONE, None.,
  [SILENT], older than 12 hours, a paused job: nothing; a long line is cut to 80 characters; `next_up` in the
  activity only with a profile folder; the board script lists only this profile's tasks; the integration
  passes on text and time only.
- Live (23:16): `--next-up on` wrote the script, limited the voice profile's scheduled jobs to `todo`
  (`config.yaml` backed up) and created the job; `hermes -p voice cron run` gave "Test voice response speed
  on the Echo Show" from a board of two blocked and seven finished tasks; `--check` showed it as `next_up`.

**Problem found and fixed.** The first deployed page declared `next` twice in one function: a syntax error
that stops the whole script, so a screen would show nothing. Found in the browser's console within minutes,
fixed and redeployed. `tests/run.sh` now runs `node --check` on the page's script, since no Python test
loads it.
