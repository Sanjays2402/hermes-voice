# What Hermes Voice changes, and how to undo each

Everything Hermes Voice (the integration, the setup skill, the screen) changes on your machines, why, and the
way back. Nothing here needs code edits to undo. The [research log](research/README.md) has the evidence
behind each choice.

Placeholders: `<profile>` is your voice profile (the skill's examples use `voice`), `<echo>` is the Echo's
entity prefix in Home Assistant (for example `echo_show_5_1a2b3c`), `<config>` is Home Assistant's config
folder.

## Home Assistant

### The Hermes Voice integration

| What | Undo |
|---|---|
| The integration's files in `<config>/custom_components/hermes_voice/` (from HACS, the release zip or `ha_setup.py`) | Settings → Devices & services → Hermes Voice → ⋮ → Delete, then remove the folder (or uninstall in HACS) and restart Home Assistant |
| One entry per Hermes profile (gateway URL, the profile's API key) | Settings → Devices & services → Hermes Voice → the entry → ⋮ → Delete |

### The integration's options

Settings → Devices & services → Hermes Voice → **Configure**. Each one can be turned off or reset there.

| Option | Default | What it does | To undo |
|---|---|---|---|
| Session mode, idle reset | per conversation, 30 min | Which turns share one Hermes session | Pick the other mode |
| Memory scope key | empty | Sends `X-Hermes-Session-Key` for per-user memory | Empty it |
| Clean replies for speech | on | Strips markdown, links, code and emoji from what is spoken; hands text-to-speech whole sentences, each ending with a blank line so Piper speaks it at once (Piper otherwise waits for the next sentence) | Off: text is spoken as it streams, unchanged |
| Voice instruction | a short "answer for a speaker" system message | Added to every turn; does not replace the profile's SOUL.md | Empty it |
| Spoken fallback, timeout | "I can't reach my brain right now…", 30 s | What is said when Hermes fails | Change the text or time |
| Stream replies | on | Speech starts while Hermes is still writing | Off: the whole reply is written, then spoken (slower) |
| Working phrase | "One moment." | Said when Hermes starts a tool before saying anything | Empty it |
| Tell Hermes the date and time | on | Adds Home Assistant's local date and time to every turn (Hermes has no clock) | Off |
| Don't say the name aloud | on | Spoken replies say "the agent" instead of "Hermes" ("the latest agent release"; "Hey Hermes" is said as "the wake word"), so an Echo running the "Hey Hermes" wake word does not wake itself. The screen and the text keep the real words. Needs *Clean replies for speech* | Off |
| Background work on the screen | this profile only | What the Hermes screen shows of Hermes' task board | *All profiles* (opt-in) or *Off* |
| Activity helper port | 8660 | Where the screen's activity helper listens | Change it, or turn the screen's background work off |

### The Assist pipeline

`ha_setup.py --set-default` and `speech_setup.py --set-default` change the **preferred pipeline** (Settings →
Voice assistants → the pipeline):

| What | Undo |
|---|---|
| Conversation agent = your Hermes profile | Pick another agent (for example Home Assistant) |
| **Prefer handling commands locally** turned on: Home Assistant answers the time, timers and device commands itself, everything else goes to Hermes | Turn it off in the pipeline, or run `ha_setup.py` with `--no-local-commands` |
| Speech-to-text and text-to-speech = the Wyoming Whisper and Piper servers `speech_setup.py` started | Pick others in the pipeline |

### Home Assistant settings the skill sets

| What | When | Undo |
|---|---|---|
| Time zone = this computer's | `ha_setup.py --docker` onboarding a new Home Assistant (`--time-zone` to choose) | Settings → System → General → Time zone |
| Internal URL = the LAN address | `speech_setup.py --internal-url` (Echos fetch reply audio from it) | Settings → System → Network → Local network |
| An admin user `hermes-voice` and a long-lived token (secrets folder) | `ha_setup.py --docker` on a new Home Assistant | Settings → People → Users; Profile → Security → delete the token |

### Speech servers

`speech_setup.py --docker` starts two containers on 127.0.0.1, models in `--data-dir`:

```bash
docker rm -f hv-whisper hv-piper     # then delete the Wyoming entries in Settings → Devices & services
```

### The Echo in Home Assistant

| What | Undo |
|---|---|
| The Echo's ESPHome entry (added by you, or by IP when Home Assistant runs in Docker Desktop) | Settings → Devices & services → ESPHome → the Echo → Delete |
| *Allow the device to perform Home Assistant actions* (echo-dot-assist asks for it) | The entry → Configure → untick |
| Wake word = **Hey Hermes** (`select.<echo>_wake_word`) | Pick another word there; with microWakeWord off on the Echo, "Alexa" comes back |
| Optional tuning: **Finished speaking detection = aggressive** (`select.<echo>_finished_speaking_detection`). Saved about 1.3 s per question on an Echo Show 5; may cut off slow speakers | Set it back to *default* |

### The Hermes screen

| What | Undo |
|---|---|
| A non-admin user for the screen and its long-lived token | Settings → People → Users → the user → Delete user (its tokens go with it) |
| The page at `/hermes_voice/screen` | Served by the integration; removing the integration removes it |
| Your own avatar pictures in `<config>/www/hermes_voice/` (served as `/local/hermes_voice/`) | Delete the files |

## Hermes

### The voice profile (`voice_profile.py`)

Only ever touches `~/.hermes/profiles/<profile>/`, and **backs up every file before changing it** into
`~/.hermes/profiles/<profile>/hermes-voice-backups/<date-time>/`.

| What | Undo |
|---|---|
| A new profile (`--create`, optionally `--clone-from`) | `hermes profile delete <profile>` |
| `SOUL.md`, the voice persona (`--write-soul`, from the skill's `references/SOUL.voice.md`): helpful and concise; a first sentence of ten to fifteen words that answers (speech starts when it ends; Home Assistant waits for 60 characters); at most two more short sentences; no lists, markdown or emoji; says "I", never its own name or the wake word | Copy the previous `SOUL.md` back from `hermes-voice-backups/`, or edit it; then `hermes -p <profile> gateway restart` |
| `gateway.standalone: true` in the profile's `config.yaml` (its own gateway and port; not with `--multiplexed`) | `hermes -p <profile> config set gateway.standalone false`, or restore `config.yaml` from the backup |
| `session_search` added to `agent.disabled_toolsets` (keeps voice answers from digging through old chats; `--keep-session-search` skips it) | Remove it from that list in `config.yaml`, or restore the backup |
| The API server in the profile's `.env` (`API_SERVER_KEY`, port, host) | Remove those lines (Home Assistant then cannot reach this profile) |
| Tools for voice (`--tools safe\|home\|full\|<list>`, on the `api_server` platform only) | `voice_profile.py --profile <profile> --tools <another preset>`, or `hermes -p <profile> tools` |
| Thinking effort (`--reasoning low`) | `voice_profile.py --profile <profile> --reasoning <level>` |
| The gateway as a login service (`--service`) | `hermes -p <profile> gateway uninstall` |

### The screen's activity helper (`screen_activity.py --install`)

A read-only login service next to Hermes (launchd on macOS, a systemd user unit on Linux) that runs
`hermes kanban ... --json` and answers only the voice profile's API key, on 127.0.0.1:8660.

```bash
python3 skill/hermes-voice-setup/scripts/screen_activity.py --profile <profile> --uninstall
```

### The next up job

Opt-in, made by `screen_activity.py --next-up on`.

| What | Undo |
|---|---|
| A scheduled job *Hermes Voice: next up* in the voice profile (four short model calls a day; the answer stays local) and the read-only script `scripts/hermes_voice_next_up.py` in the profile | `python3 skill/hermes-voice-setup/scripts/screen_activity.py --profile <profile> --next-up off` (removes both), or `hermes -p <profile> cron remove <id>` |
| The profile's scheduled jobs limited to the `todo` toolset (they had every tool, terminal and computer use included). `config.yaml` is backed up to `hermes-voice-backups/` first | Restore `config.yaml` from that backup, or `hermes -p <profile> tools enable --platform cron <toolsets>`. `--next-up off` leaves the limit in place, since it is the safer setting |

### The task board

Hermes Voice never writes to it. If you made display-only tasks for a demo, create them with
`--initial-status blocked` (a gateway with the kanban dispatcher works on anything *ready* or in *review* by
itself) and remove them with `hermes kanban archive <id> ...`.

## The Echo

| What | Undo |
|---|---|
| echo-dot-assist itself (bootloader unlock, root, hassmic in place of Alexa) | Not Hermes Voice's to undo: follow echo-dot-assist's own README. The unlock cannot be reversed by software; Amazon's firmware can be reflashed |
| The "Hey Hermes" model and microWakeWord, uploaded on the Echo's settings page (`http://<echo-ip>:28931/` → Wake word) | Delete the model there and switch the engine back to Amazon's |
| The Hermes Screen app (Echo Show) | The settings page → Screen → pick the face; then `adb uninstall org.hermesvoice.screen` (debug access: the settings page → System → Debug access) |
| Kiosk Satellite, if you said yes to it in echo-dot-assist's setup | Removed the way echo-dot-assist's README describes; the face comes back on the Screen page |
| Debug access (adb over Wi-Fi) | Closes by itself after 30 minutes |

## Your own computer

| What | Undo |
|---|---|
| Building the screen app: a JDK and the Android SDK (`devices/echo/screen-app/build.sh` lists them; accepting Google's SDK license) | Uninstall them with the tool you used (for example `brew uninstall openjdk@17` and `brew uninstall --cask android-commandlinetools`) |
| The app's signing key, `devices/echo/screen-app/build/screen.keystore` (not in git) | Keep it if you want to update the installed app; without it, uninstall and reinstall |
