# Troubleshooting

Issues hit while building and testing v0.1, and what fixed them.

## Echo Dot 2 unlock stalls or the boot gesture stops working

The tested Windows unlock stalled with firebreak 0.8.0 and required a hardware short.
Read the [Echo Dot 2 recovery notes](../devices/echo/README.md#echo-dot-2-recovery-on-windows)
for the driver failure, tested short point and firmware/slot findings. Stop a waiting installer
before running another one. A rooted-system message does not verify both slots or a fallback.

## Echo Show 5: "Exploit most likely successful!" but the Show boots back into Amazon's setup

amonet prints that as soon as the Show stops answering; it does not check the unlock. If the Show comes back
on Amazon's language screen, or Volume + at power-on shows the Echo logo with a red triangle, the unlock did
not take. From three-button fastboot (Volume −, Volume +, Mute while plugging in power), `fastboot getvar
unlock_status` says `false`. Nothing was written: run the setup again and type `YES` again. Watch the Show's
screen during the run, and prefer a USB 2.0 port on the PC itself. Details:
[devices/echo/README.md](../devices/echo/README.md#echo-show-5-1st-gen-on-an-ubuntu-live-usb).

## echo-dot-assist setup downloads the Android NDK and runs out of disk

It builds from source when GitHub has no release build of the commit you cloned (a commit pushed minutes ago,
or local changes). Check out the newest release tag (`git tag -l 'v*'`) or wait for GitHub to build the commit,
then run the setup again: it offers "Use it (recommended): nothing to compile". On a live USB, also work from a
16 GB drive, not the RAM-backed live session.

## The Echo stopped answering after uploading the "Hey Hermes" model

Uploading the model removes "Alexa" and leaves the Echo's wake word slot empty. In Home Assistant, set the
Echo's Wake word (`select.<echo>_wake_word`) to **Hey Hermes**.

## The assistant says "I can't reach my brain right now"

That is hermes_voice's fallback. The HA log has the reason (`Hermes turn failed (<kind>, session <id>)`).

| Kind | Cause | Fix |
|---|---|---|
| `rate limited` | The profile's provider returned HTTP 429. Seen in testing when the voice profile shared the main agent's subscription. Hermes can return HTTP 200 with the provider's error text; hermes_voice detects that and does not read it out. | Separate provider or `hermes -p <profile> fallback add`; wait for the limit to reset. |
| `timeout` | No reply within the timeout (default 30 s). Long tool runs. | Raise the timeout in options; use a faster model; trim tools on the voice profile. |
| `HermesConnectionError` | Gateway not running, wrong port, or HA can't reach it. | `hermes -p <profile> gateway status`; check the URL from HA's point of view (below). |
| `HermesAuthError` | Wrong `API_SERVER_KEY`. | HA shows a re-authentication prompt; paste the key from the profile's `.env`. |

## "Could not reach the Hermes gateway" in the config flow

- HA in Docker: `127.0.0.1` is the container itself. Use `http://host.docker.internal:<port>/v1` (Docker
  Desktop, OrbStack) or the host's LAN IP. On Docker Desktop for Mac, a gateway bound to `127.0.0.1` on the
  host was reachable via `host.docker.internal` in testing.
- HA on another machine: bind the API server to the LAN (`API_SERVER_HOST=0.0.0.0`) and protect the port.
- The API server only starts when `API_SERVER_KEY` is at least 16 characters.

## Wrong answers from "other conversations", slow turns

The `session_search` tool lets the agent search all past transcripts of the profile. In testing, a new
conversation asked "what is my codeword?" and the agent answered with a codeword from a different
conversation, taking ~11 s. Disable it for the voice profile:
`hermes -p <profile> config set agent.disabled_toolsets '["session_search"]'` (see voice-profile.md).

## Something said once shows up in every conversation

Long-term memory is per profile. In testing, "Remember this: my codeword is MERLIN" was written to the
profile's `memories/USER.md` and would be visible to every later conversation. Edit or clear
`~/.hermes/profiles/<profile>/memories/USER.md` / `MEMORY.md`, and tune SOUL.md for what to remember.

## The conversation forgets after a few minutes

Home Assistant ends an Assist conversation after 5 minutes without a turn
(`homeassistant/helpers/chat_session.py`, `CONVERSATION_TIMEOUT`). In `per_conversation` mode the next turn
starts a new Hermes session. Use `per_device` mode with `idle_reset_minutes` for longer continuity.

## ha_setup.py: "Home Assistant rejected the token" right after onboarding

On a freshly started HA, the auth store saves with a 300-second delay
(`homeassistant/auth/auth_store.py`, `INITIAL_LOAD_SAVE_DELAY`). Restarting HA within that window loses a
newly created long-lived token. `ha_setup.py` now waits until the token is written to `.storage/auth`
before restarting, and if a stored token is rejected it logs in with the admin password it saved and
creates a new token.

## ha_setup.py: integration not loaded after restart

Calling the `homeassistant.restart` service over REST on a fresh container did not restart it in testing.
For Docker installs `ha_setup.py` uses `docker restart -t 120`, then waits until `hermes_voice` appears in
the config-flow handlers (and stops with an error if it doesn't).

## Hermes CLI warns "'gateway.standalone' (or 'agent.reasoning_effort') is not a recognized config key"

Expected for both. `agent.reasoning_effort` is read by the gateway (`hermes_constants.resolve_reasoning_config`)
and is the key Hermes' own `/reasoning --global` writes. Hermes reads the key (it prints "This gateway stays standalone" at start) but its config checker
doesn't list it. Hermes calls it a temporary compatibility setting; the alternative is the multiplexed
gateway URL `/p/<profile>/v1`.

## Cloning a profile copied more than expected

`hermes profile create --clone` copies config, `.env`, SOUL.md, skills, memory files and the
`mcp_servers:` block. Review those on a voice profile; anything it can do can be triggered by voice.

## Tests

`tests/run.sh` runs inside `hermes-voice-test:2026.10.4` (built from the HA 2026.10.4 image +
`pytest-homeassistant-custom-component==0.13.375`). If `conversation` fails to set up in a test with
`KeyError: 'homeassistant.exposed_entities'`, the `homeassistant` core integration was not set up first
(the autouse fixture in `tests/conftest.py` does it).

## Testing with the REST API answers "Sorry, I am not aware of any area called …"

`POST /api/conversation/process` without `agent_id` uses Home Assistant's built-in agent, not the
conversation agent set on your Assist pipeline. Pass `"agent_id": "conversation.<your_hermes_entity>"`
(that is what `verify.py` does), or test through an Assist pipeline run.

## The device hears you, but no reply is played

The device downloads each spoken reply from Home Assistant's URL. HA in Docker hands out its container
address (seen in testing: `http://172.17.0.4:8123/api/tts_proxy/...`), which the device cannot reach. Set
the internal URL to the host's LAN address: Settings → System → Network → Local network, or
`speech_setup.py --internal-url http://<LAN-IP>:8123`.

## Slow speech-to-text

With `--model small-int8` or `base-int8`, the Wyoming Whisper container uses faster-whisper, which took about
2.3 s for a 3-second clip on an M1 Max in Docker (ARM Linux). Leaving `--model` unset with `--language en`
selects NVIDIA Parakeet through sherpa-onnx: 0.25 s for the same clip, with punctuation. `speech_setup.py`
uses that default.

## Long silence before an answer

- Check streaming is on (integration options → *Stream replies*) and the pipeline's text-to-speech supports
  streaming (Piper via Wyoming does). Then a tool turn starts with the working phrase within a few seconds.
- Lower the model's thinking effort for the voice profile: `voice_profile.py --profile <name> --reasoning low`
  and `hermes -p <name> gateway restart`.
- Fewer tools means a shorter prompt: `--tools safe` cut the prompt from about 12,000 to 3,800 tokens.

## "What time is it?" gets "I can't reliably look up the time"

Hermes has no clock. Versions up to 0.1.0 did not tell it the time, so Hermes searched the web for
it, and time pages show the time only in a browser. Update the integration (it now sends Home Assistant's
local date and time on every turn; option *Tell Hermes the date and time*), and check Home Assistant's time
zone (Settings → System → General). With *Prefer handling commands locally* on in the pipeline, Home
Assistant answers the time itself.

