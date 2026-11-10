# Hermes Visual Memory

Hermes remembers what the Echo Show's camera saw and can answer **"What changed?"**, **"Where did you last
see my red screwdriver?"** or **"What was on the desk earlier?"**, and show the before/after pictures on the
Echo's screen.

It runs on the same computer as Hermes. It takes one picture a second from Hermes Eye (the Show's camera
app, [docs/eye.md](../../docs/eye.md)) and decides locally when the desk has really changed. A vision model
looks only when it's turned on, when the camera is uncovered, and when someone asks. Memory is a SQLite file
and a folder of snapshots.

The demo: three things on the desk. "Hey Hermes, turn on visual memory" ("OK, I'm watching your desk"; the
Echo shows the desk outlined and the things on it boxed). "What's in front of you?" Cover the camera, take
one thing away, uncover. "What did I take away?" ("The keys are gone", with before/after on the screen.)

## How it decides

```
turned on → first look: finds the desk, names what's on it (the Echo shows it)
camera (1 picture/s) → quality check → desk steady for 2.5 s? → changed vs the last look?
                                                                 │ no: nothing to do
                                                                 │ yes: picture saved on this computer
question ("what changed?", "what's in front of you?") or camera uncovered → one look, comparing with the
last one → memory: what stayed, moved, appeared; what can't be seen, and why
```

- **Looks only when needed.** No look runs in the background: a day where nothing is asked costs nothing
  beyond the first look. Changed views are kept locally (for a later search back through the day).
  With a local model, looks are free, so it also looks whenever the desk changes and settles (at most one
  every 10 s, `look_when_settled`): "what's in front of you?" then answers at once. With Codex,
  `auto_analyze = true` brings back a look at every settled change, with the limits below.
- **The desk, not the room.** The first look also marks the work surface (desk, table, counter, shelf).
  Only that part of the picture is compared, and only things standing on it are tracked: someone sitting
  behind the desk, a chair or a doorway is not a change. "Watch the whole room" (`area = "all"`) watches
  everything. The first look doesn't wait more than 4 s for a still view, so someone moving in front of the
  camera doesn't hold it up.
- **Uncovering the camera** starts the comparison at once (`look_after_uncover`), so "what did I take
  away?" is answered in time: one look per cover.

- **Picture quality**: clear, uncertain or unreadable. A closed shutter, a hand over the lens, darkness,
  glare or heavy blur make it *unreadable*: memory stops updating and keeps the last reliable scene. A hand
  right over the lens is bright (auto-exposure) but has almost no detail, so detail is compared with this
  view's recent normal (it drops from ~320 to 20–55 on the Show 5). Hermes says "the camera may be
  obstructed", never more than that.
- **Change**: brightness patterns *and* edges must change, after evening out exposure and a small nudge of
  the camera, so room light and soft shadows don't count. Movement (a hand passing) never settles, so
  it is never analysed.
- **After an obstruction** the next steady scene is compared with the one before. If it looks the same, no
  model call is made and "What changed?" says nothing changed.
- **An object not found again** is judged from the pictures, not just the model's word:
  - its spot looks exactly as before → *unconfirmed* (very likely still there; the model missed it);
  - something new now covers its spot → *possibly obstructed* ("I last saw it on the left, but the view may
    be obstructed now");
  - its spot is visible and changed, nothing in front → *missing* ("it was on the left, but I don't see it
    there anymore" — never "it was taken").
- **Objects** are remembered by what they look like (category, colours, markings, position, neighbours), not
  by permanent identities. The model links an object to an earlier one; if it doesn't, the same kind of
  thing (one candidate, no clashing colour) or the same spot and size links it. A colour that wasn't clear
  is not stored, so "the red screwdriver?" can be answered "I saw a screwdriver whose colour I couldn't
  make out".

Positions are said as the user sees them facing the Echo, and pictures are shown mirrored (like the
video-mode window), so "on the left" matches the screen.

## The vision model

Either the user's own **Codex CLI** (default), or **a local model** on any OpenAI-compatible server
(Ollama, LM Studio, llama.cpp, vLLM): `vision = "local"`, `local_url`, `local_model` (setup:
`vismem_setup.py --profile voice --local-model <name> [--local-url …]`). With a local model, pictures never
leave the network. Local models are shown only the current picture (`model_compares = "auto"`): given
the earlier list to compare with, qwen3.6 copied it back in 2 of 3 tries ("still there" for a snow globe
that had been taken), so the matching to earlier objects is done locally, by kind and spot. Ollama models are loaded when visual memory is turned on and kept loaded for
`local_keep_alive` (a cold load took 30–60 s).

| local model (Mac Studio M1 Max, Ollama) | one picture | compare two | notes |
|---|---|---|---|
| `qwen3.6:35b-a3b` | 5.4 s | 7.5–12 s | good names and boxes |
| `gemma3:12b` | 12.5 s | – | vague names ("device", "base"), boxes off |

The Codex CLI (`codex exec`, images attached, a JSON schema for the answer, `--ephemeral`,
read-only sandbox, no tools used). It runs on the ChatGPT/Codex plan already in use: no new account, API key
or bill; each analysis counts toward that plan's usage limits. Measured on a Mac Studio (M1 Max):

| model | one look | notes |
|---|---|---|
| `gpt-6.1-sol` (default) | 10–19 s (median ~14 s), ~19.8k tokens in (≈15k of them Codex's own instructions), 130–330 out | names things most reliably ("metal keys on ring") |
| `gpt-6-luna` (quick, used for every look someone waits for) | 5–7 s, at busy times 12–22 s | about twice as fast, vaguer names |

**Speaking in time.** The Echo stops waiting for a spoken answer 15 s after the question. So looks someone
is about to wait for (the view just before the camera is covered, the one after, and a question's own
look) use the quicker model (`quick_model`, ~6–9 s), a question waits at most `quick_wait` (9 s) for its
look, and Hermes says one short line ("The keys are gone. New: a phone.") while the pictures and details
go on the screen. If the look isn't done, Hermes says so and the pictures appear when it is.

Limits: at most `manual_per_hour` (20) looks an hour, 0 for no limit. (With `auto_analyze`, automatic looks
also have a 60 s gap and 10 an hour / 30 a day.) Every call is logged with time, tokens and success
(`vismem usage`).

## Install

Needs Python ≥ 3.11, the Codex CLI signed in, and Hermes Eye on the Show.

The setup skill does all of this: `skill/hermes-voice-setup/scripts/vismem_setup.py --profile voice` (with `--check`, `--dry-run`, `--uninstall`). By hand:

```bash
cd extras/visual-memory
uv venv -p 3.11 .venv && uv pip install -p .venv/bin/python -e .
cp config.example.toml ~/.config/hermes-voice/visual-memory.toml   # set eye_url
.venv/bin/python -m vismem.cli install-service                     # a launchd agent; starts now and at login
```

macOS: a background service needs **Local Network** access to reach the Echo. If `vismem status` says the
camera is "offline" while `curl` reaches it, allow the Python that runs the service in System Settings →
Privacy & Security → Local Network (or build the venv from a Python that already has it, e.g. the one
Hermes runs on).

Connect it to a Hermes profile (back up its config first):

```bash
hermes -p <profile> config set mcp_servers.vismem \
  '{"command": "<path>/extras/visual-memory/.venv/bin/python", "args": ["-m", "vismem.mcp_server"]}'
hermes -p <profile> gateway restart
```

The Echo's screen (the Hermes screen page, `custom_components/hermes_voice/screen`) connects by itself to
`<this computer>:28951` and shows a "Visual memory on" chip and the pictures Hermes shows.

## Use

By voice: "turn on visual memory" (or "watch the whole room"), "what's in front of you?", "what changed?",
"what did I take away?", "where's my red screwdriver?",
"show me where you saw the keys", "what was here before the camera was covered?", "was there a black box
here yesterday?", "turn off visual memory", "delete all visual memory".

From a shell (`vismem` = `.venv/bin/python -m vismem.cli`):

```
vismem status [-v]            on/off, camera state, last reliable scene, items, calls and tokens today
vismem on | off               start / stop observing (off stops all pictures at once)
vismem changed | scene [now|before_obstruction|earlier|yesterday|<iso time>]
vismem find red screwdriver   vismem history keys --since yesterday
vismem show comparison|item <words>|scene     vismem hide
vismem usage [days]           model calls, failures, median time, tokens
vismem forget all|images|session | forget older <days>
vismem install-service | uninstall-service
```

## Hermes tools (MCP)

`visual_memory` (on/off/status), `what_changed`, `find_object`, `describe_scene`, `object_history`,
`show_evidence`, `forget_visual_memory`. Each calls the local API and returns text written to be spoken,
which keeps the visible / possibly obstructed / gone distinctions.

Local API (127.0.0.1:28950, `Authorization: Bearer <data_dir/api_token>`): `GET /status /changed?fresh=1
/find?q= /scene?which= /history?q=&since=&until= /usage?days= /evidence/{comparison|item|scene}/<id>.jpg`,
`POST /session {on, mode} /show {what, query} /hide /forget {scope, days}`.

## Privacy

- Off until turned on, and never resumes by itself after a restart (`resume_after_restart = false`). While
  on, the Echo shows Hermes Eye's red **REC** badge and a "Visual memory on" chip.
- One still picture a second is looked at in memory and dropped; no video is recorded. Snapshots are kept
  only for analysed scenes, and are deleted after `retention_days` (14); observations after `history_days`
  (90). `vismem forget …` deletes on request.
- Snapshots leave this computer only when analysed: they go to OpenAI through the user's Codex CLI. The
  model is told to ignore people. There is no face recognition and no record of who was there.
- The camera's shutter and the Echo's mute button are respected: both stop the picture at the source.
- Data lives in `~/.local/share/hermes-visual-memory` (this account only, 0700/0600). The API listens on
  127.0.0.1 with a token; the display feed listens on the LAN but only answers the Echo's own address, and
  serves only the picture currently shown, under a random name.

## Tests

`.venv/bin/python -m pytest` — 49 tests: picture quality, light and camera nudges, a hand over the lens,
cover/uncover with no change (no model call), missing/moved/added, a box in front of an object, a thin
thing laid over an empty spot, an object the model overlooks or renames, ambiguous screwdrivers, persistence
across a restart, rate limits, the HTTP API, token, display allow-list and evidence pictures. Synthetic
scenes and a scripted model; no real photos are in the repository.

Live results on the Echo Show 5 (2026-11-08):

| test | result |
|---|---|
| A recognition | passed: camper snow globe, white marker, metal keys/ring, scissors with turquoise handles, a phone; boxes on the right objects. Labels vary between looks ("metal ring" / "metal keys"). |
| B cover, no change | passed: a hand over the lens is "may be obstructed" for the whole time; no object reported missing. |
| C/D/E remove, move, add (demo) | passed by voice with before/after on the screen: marker moved, keys and phone new. C re-run after F (keys hidden behind a box, then taken away): Hermes said "no longer visible"; the stored judgement was still "spot unchanged" because the spot was compared with the box picture, so spots are now compared with the picture where the object was last seen, with a per-spot measure (real keys: 1.0 changed; unchanged objects ≤ 0.1). |
| F box in front | passed live: "where are my keys?" with a box in front of them → "last seen on the right; the view may be obstructed" (a fresh look, 10 s; the box covered their spot completely). |
| G ambiguous | passed live: yellow pencil, yellow and blue writing tools; the right one found by colour, and "I haven't seen one" for a colour that wasn't there. |
| H unreadable | passed live (hand, shutter); fixtures for dark and blur. |
| I persistence | passed live: baseline and items survived service restarts. |
| J unchanged scene | fixtures: 1 call in 10 minutes. Live with a person in view: about 1 call a minute (each settle after someone moves), held to the hourly cap. |
| K voice | passed: "what changed?" on the Echo, answered by the voice profile from memory (6.7 s). |
| L evidence | passed: before/after pictures on the Echo's screen, changes boxed. |

## Limitations

- A question answered by a fresh look takes as long as the look (5–7 s with Codex's quick model, 12–22 s
  when Codex is busy; 7–12 s with qwen3.6 locally). Past `quick_wait` (9 s) Hermes says it's still looking
  and the pictures appear on the screen when done; the Echo stops waiting for speech 15 s after a question.

- A hand or arm resting on the desk is a change like anything else (it is only saved, not looked at).
- Analyses take 10–19 s with `gpt-6.1-sol`. "What changed?" asked right after uncovering waits for the
  comparison (up to ~22 s); asked later it answers from memory at once.
- Object names vary between looks; matching by spot and kind covers most of it, not all.
- Positions are coarse (left/middle/right, top/bottom of the view), not desk coordinates.
- Mirrored evidence pictures show text backwards.
- A question about the present takes a fresh look first when the view changed since the last one (10–20 s);
  when the automatic looks are at their limit, every such question pays that.

## Next

- **Search back in time.** "Where did my phone go?" / "what was here yesterday?": look through the saved
  pictures newest first (or within the time asked), halving the range, so a day of changes takes a handful
  of looks.
- Limits by voice ("no limit on visual memory"), a "still using visual memory?" card every 30 minutes, a
  larger REC badge, and the live picture in a corner of the Echo's screen while on.

## Rollback

```bash
.venv/bin/python -m vismem.cli uninstall-service      # stops it; memory stays
rm -rf ~/.local/share/hermes-visual-memory            # deletes memory and pictures
hermes -p <profile> config set mcp_servers.vismem null   # or restore the profile backup
```

## Reuse

Written for this project; MIT. [where-i-put](https://github.com/void-cat-0/where-i-put) was reviewed: it
publishes no license (so no code could be reused) and is a Rust/RTSP/YOLO stack; only its general idea
(an observation log with appeared/disappeared events and snapshot evidence) informed this design.
