---
name: hermes-voice-setup
description: Turn one Hermes profile into a voice assistant through Home Assistant Assist (hermes_voice integration), with an Echo Dot or Echo Show as the speaker.
version: 0.1.0
author: hermes-voice contributors
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [voice, home-assistant, assist, echo, smart-home]
    requires_toolsets: [terminal]
---

# Hermes Voice Setup

Wires a dedicated Hermes profile into Home Assistant (HA) as an Assist conversation agent, so speech from
a voice device goes: device → HA Assist (speech-to-text) → `hermes_voice` → this profile → HA (text-to-speech) → device.
All scripts are stdlib Python, idempotent, and support `--dry-run`. They only touch the profile named
with `--profile` and refuse `default`.

The skill code is MIT-licensed. The bundled `hey_hermes.tflite` wake word model is for
[non-commercial use only](assets/wakeword/MODEL_NOTICE.md).

## When to Use

- The user wants to talk to their Hermes agent by voice, through Home Assistant.
- The user has (or wants) an Echo Dot or Echo Show running echo-dot-assist, or any other Assist voice satellite.
- The user asks to repair or re-verify an existing hermes_voice setup.

## Quick Reference

| Step | Command |
|---|---|
| Plan profile changes | `python3 ${HERMES_SKILL_DIR}/scripts/voice_profile.py --profile voice --create --dry-run` |
| Prepare + start profile | `python3 ${HERMES_SKILL_DIR}/scripts/voice_profile.py --profile voice --create --write-soul --tools safe --reasoning low --service` |
| Change what voice may do | `python3 ${HERMES_SKILL_DIR}/scripts/voice_profile.py --profile voice --tools safe\|home\|full\|web,file,...` |
| Plan HA changes | `python3 ${HERMES_SKILL_DIR}/scripts/ha_setup.py --profile voice --docker --config-dir DIR --dry-run` |
| Install + configure HA | `python3 ${HERMES_SKILL_DIR}/scripts/ha_setup.py --profile voice --docker --config-dir DIR --set-default` |
| Text test through HA | `python3 ${HERMES_SKILL_DIR}/scripts/verify.py --recall` |
| Background work on an Echo Show's screen | `python3 ${HERMES_SKILL_DIR}/scripts/screen_activity.py --profile voice --install` |
| Show every profile's work (opt-in) | `python3 ${HERMES_SKILL_DIR}/scripts/ha_setup.py --profile voice --screen-activity all` |
| Device guide | `${HERMES_SKILL_DIR}/references/devices.md` |
| What it changes, and how to undo each (read before removing anything) | `${HERMES_SKILL_DIR}/references/changes-and-undo.md` |

## Ground rules

- **Use the scripts.** If one fails, read its error, fix the cause (for example start the gateway) and run it
  again. Do not replace a step with your own API calls or helper scripts; if you cannot fix it, stop and tell
  the user what failed.
- **Profiles stay where Hermes keeps them** (`hermes profile create`). Never move or symlink a profile, set
  `HERMES_HOME`, or copy `auth.json` or other credentials. A new profile already shares the Hermes install's
  provider login. When the user wants files in a particular folder, that means `--config-dir`, `--data-dir`
  and `--secrets-dir`.
- **Leave existing things alone** unless the user chose them: other Hermes profiles, a Home Assistant that is
  already running, other containers. If the profile name the user wants already exists, ask before changing it.
- Show a `--dry-run` before each step that changes something, and say what it will change.

## Procedure

1. **Pick the voice profile.** Ask the user which profile should be the voice. Recommend a new
   dedicated one (default name `voice`) over their main agent. Explain why: voice turns are short, it should
   not share the main agent's rate limit, and its tools/MCP servers should be ones they are happy to run
   from a spoken command. Always run with `--dry-run` first and show the user the planned changes.
   - New profile: `voice_profile.py --profile voice --create [--clone-from <main>] --write-soul --service`.
     `--service` installs the gateway as a login service so it survives restarts (`--start` runs it only
     until logout). `--write-soul` installs the voice persona "Hermes" (replacing the generic SOUL.md
     `hermes profile create` writes; a SOUL.md the user wrote is kept unless `--force-soul`).
     `--clone-from` copies that profile's config, provider login, `.env`, SOUL.md, skills, memory and
     MCP servers. Without it the new profile uses the provider login shared by the Hermes install (root
     `auth.json`, so it shares that rate limit); give it its own with `hermes -p voice setup`.
   - Existing profile: `voice_profile.py --profile <name> --service` (adds the API server key/port, marks it
     standalone, disables `session_search`; backs up every file it edits under
     `profiles/<name>/hermes-voice-backups/`).
   - Already served by a multiplexing host gateway: add `--multiplexed`; the URL becomes
     `http://127.0.0.1:8642/p/<name>/v1`.
   - **Ask what Hermes may do by voice** and pass it as `--tools`. Anyone in earshot of the speaker can use
     these tools. `safe` (web search, memory, todo; recommended), `home` (safe + Home Assistant control),
     `full` (Hermes defaults: terminal, files, code, browser; for coding by voice), or an exact
     comma-separated list. Applies to the `api_server` platform only. Restart the gateway after changing it
     (`hermes -p <name> gateway restart`).
2. **Home Assistant.** Ask whether they already run HA.
   - Existing HA: they create a long-lived token (HA → Profile → Security) and save it to a file.
     Install the integration via HACS (custom repository) unless you can write to HA's config directory,
     then run `ha_setup.py --profile <name> --ha-url HA_URL --token-file FILE --hermes-url HERMES_URL`.
     `--hermes-url` must be the address HA can reach (LAN IP of this machine, not 127.0.0.1, unless HA runs
     in Docker on this same machine, where the default `host.docker.internal` works). If HA is on another
     machine, the profile's API server must listen on the LAN: rerun `voice_profile.py --host 0.0.0.0`.
   - No HA: `ha_setup.py --profile <name> --docker --config-dir DIR --set-default` starts
     `ghcr.io/home-assistant/home-assistant:stable`, onboards it, stores the admin password and a token in
     `~/.config/hermes-voice/secrets/` (0600), installs and configures `hermes_voice`.
   - `--set-default` makes the profile the conversation agent of the preferred Assist pipeline and turns on
     *Prefer handling commands locally*: Home Assistant answers its own sentences (time, timers, lights)
     at once and sends everything else to Hermes. `--no-local-commands` leaves it off.
   - **Ask which voice** they want for replies and pass `speech_setup.py --piper-voice`, e.g.
     `en_US-lessac-medium` (female, default) or `en_US-ryan-high` (male); others:
     https://rhasspy.github.io/piper-samples/. To change it later, set the voice on the Assist pipeline
     (Settings → Voice assistants).
   - Speech: if the pipeline has no speech-to-text / text-to-speech yet, run
     `speech_setup.py --docker --data-dir DIR --set-default --internal-url http://<LAN-IP>:8123`. It runs
     Wyoming Whisper (Parakeet model for English) and Piper in Docker on 127.0.0.1, adds them to HA and sets
     them on the preferred pipeline. Without `--docker` it only adds servers that already run
     (`--whisper-host/--piper-host`). With an existing HA, pass the same `--ha-url HA_URL --token-file FILE`.
     Users with HA OS can use the Whisper and Piper add-ons, or HA Cloud.
3. **Device.** Read `references/devices.md` and walk the user through the Echo path (echo-dot-assist).
   echo-dot-assist unlocks the bootloader and writes the system partition to replace the Alexa client
   while keeping Fire OS. The install can brick the device. Link to its guide for the user to run.
   For a Dot 2 on Windows, also read the recovery section in `references/devices.md`. The tested unit
   needed a hardware short after firebreak 0.8.0 stalled. Stop a waiting installer before manual recovery.
   Allow a write already in progress to finish.
   Check the firmware build selected by the exact firebreak version. A rooted-system or “both slots”
   message does not verify a fallback, and the satellite may be installed only in the active slot.
   Obtain explicit approval before enabling debug access or changing firmware, slots or boot state.
   Debug access needs its own action-button press after the settings-page login approval.
   Then offer the "Hey Hermes" wake word: the model is in `${HERMES_SKILL_DIR}/assets/wakeword/`. The user
   uploads both files on the Echo's settings page (`http://<echo-ip>:28931/`, approval by the Echo's action
   button; steps in `references/devices.md`), then you set the Echo's `select.<echo>_wake_word` entity to
   "Hey Hermes" through HA's API (`POST /api/services/select/select_option`).
4. **Echo Show screen (optional).** With an Echo Show, offer the Hermes screen (`references/devices.md`,
   "Hermes Screen on an Echo Show"): an app on the Show that shows the agent, not the house. Its idle screen
   can list Hermes' background work from the kanban board (running, waiting for review or blocked, finished
   in the last 3 hours). That needs the read-only helper next to Hermes:
   `screen_activity.py --profile voice --install` (a login service on 127.0.0.1:8660, guarded by the
   profile's API key; `--check` prints what the screen would get). By default the screen shows only tasks
   assigned to the voice profile. If the user asks to see all their profiles' work ("show everything on the
   screen"), tell them that anyone who can see the Show will then see every profile's task titles, and on
   their yes run `ha_setup.py --profile voice --screen-activity all` (`profile` to go back, `off` to hide
   it). The same choice is in Home Assistant: Hermes Voice → Configure → "Background work on the screen".
5. **Verify.** `verify.py --recall` sends three turns through HA's conversation API (three model calls).
   With an existing HA, pass the same `--ha-url HA_URL --token-file FILE`.
   Success = a reply in the profile's own voice/personality and `"recalled": true`.

## Pitfalls

- **Spoken fallback ("I can't reach my brain right now")** means HA could not get an answer: gateway down,
  wrong URL/key, timeout, or the provider is rate limited (HTTP 429). Check `hermes -p <name> gateway status`
  and the profile's provider. A voice profile sharing the main agent's subscription hits its limits.
- **Gateway URL from Docker**: `127.0.0.1` inside the HA container is the container. Use
  `host.docker.internal` (Docker Desktop / OrbStack) or the host's LAN IP.
- **HA's internal URL**: a voice device downloads each spoken reply from HA. HA in Docker hands out its
  container address (e.g. `172.17.0.4`) unless its internal URL is set to the host's LAN address
  (`speech_setup.py --internal-url`, or Settings → System → Network).
- **Multiplexing**: Hermes serves all profiles from one host gateway by default. `voice_profile.py` marks
  the voice profile `gateway.standalone: true` so it runs its own gateway on its own port. Use
  `--multiplexed` instead if the user runs the host multiplexer with the API server enabled.
- **Memory is profile-wide.** Anything said to the voice profile can be saved to its long-term memory and
  recalled by every later conversation, including other household members.
- **session_search** is disabled by the script: in voice use it caused wrong cross-conversation answers and
  ~10 s extra latency. Re-enable with `hermes -p <name> config set agent.disabled_toolsets '[]'`.
- HA ends an Assist conversation after 5 minutes of silence. For longer memory per speaker, set the
  integration's session mode to `per_device` (`ha_setup.py --session-mode per_device`).

## Verification

- `verify.py` exits 0 and prints the reply with its latency (plain turns are typically 2–6 s).
- In HA: Settings → Voice assistants → the pipeline's conversation agent is the profile's entity.
- `hermes -p <name> gateway status` shows the gateway running.
