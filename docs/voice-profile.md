# A dedicated voice profile

hermes_voice talks to **one Hermes profile per Home Assistant entry**. You can point it at your main
agent, but a dedicated voice profile is recommended:

- voice turns should be short and plain-spoken (SOUL.md tuned for speech);
- it should not share the main agent's provider rate limit (a 429 during a voice turn means silence);
- every tool and MCP server on that profile can be triggered by anyone who can speak to the device.

The `hermes-voice-setup` skill (`skill/hermes-voice-setup/scripts/voice_profile.py`) does all of the
steps below. They are listed here so you can do them by hand or review what the script does.

## 1. Create the profile

```bash
hermes profile create voice --no-alias            # empty profile, configure a provider next
hermes -p voice setup                             # pick provider + model for the voice profile
```

or copy your main agent:

```bash
hermes profile create voice --no-alias --clone-from <main-profile>
```

`--clone`/`--clone-from` copies `config.yaml`, `.env`, `SOUL.md`, skills **and the long-term memory
files** (`memories/MEMORY.md`, `USER.md`). The copied `config.yaml` also carries the source's
`mcp_servers:` and its provider login. Review both before exposing the profile to a microphone.

## 2. SOUL.md for voice

Template: `skill/hermes-voice-setup/references/SOUL.voice.md`. Copy it to
`~/.hermes/profiles/voice/SOUL.md` and add the personality you want on top. hermes_voice also sends a
short "voice instruction" system message each turn (option in HA). Hermes adds client system messages
as an *ephemeral* layer on top of its own prompt; SOUL.md is not replaced.

## 3. Enable the API server

In `~/.hermes/profiles/voice/.env`:

```bash
API_SERVER_ENABLED=true
API_SERVER_KEY=<at least 16 random characters>   # required; Hermes will not start the API server without it
API_SERVER_PORT=8650                              # any free port
API_SERVER_HOST=127.0.0.1                         # 0.0.0.0 if Home Assistant runs on another machine
```

Two ways to run it (Hermes source: `gateway/platforms/api_server.py`, `website/docs/user-guide/multi-profile-gateways.md`):

| Topology | How | URL for hermes_voice |
|---|---|---|
| Standalone gateway (what the skill does) | `hermes -p voice config set gateway.standalone true` then `hermes -p voice gateway run` | `http://HOST:8650/v1` |
| Multiplexed host gateway (Hermes default) | Run the default gateway with the API server enabled; the voice profile is served under a prefix | `http://HOST:8642/p/voice/v1` |

`gateway.standalone` is described by Hermes as a temporary compatibility setting; the CLI warns that it is
"not a recognized config key", but the gateway reads it (`gateway/run.py`, `hermes_cli/gateway_multiplex_mode.py`).

## 4. Turn off `session_search`

`session_search` lets the agent read **all** past transcripts of the profile. In voice testing it
answered from the wrong conversation and added about 10 seconds to a turn. Disable it for the voice profile:

```bash
hermes -p voice config set agent.disabled_toolsets '["session_search"]'
```

`agent.disabled_toolsets` is applied last when Hermes resolves tools for a platform
(`hermes_cli/tools_config.py`, `_get_platform_tools`), and the API server resolves tools that way on
every run (`gateway/platforms/api_server.py`), so no restart is needed. Check it without a model call:

```bash
curl -s -H "Authorization: Bearer $API_SERVER_KEY" http://127.0.0.1:8650/v1/toolsets \
  | python3 -c "import json,sys;print([t for t in json.load(sys.stdin)['data'] if t['name']=='session_search'])"
# -> [{'name': 'session_search', ..., 'enabled': False}]
```

Conversation context does not depend on it: hermes_voice sends `X-Hermes-Session-Id`, and Hermes keeps
each session's history itself.

## 4b. Choose what Hermes may do by voice

A new Hermes profile has shell, file, code, browser, cron and sub-agent tools switched on. Anyone who can
speak to the speaker can use whatever the voice profile can do, so pick its tools on purpose.
`voice_profile.py --tools` sets them for the `api_server` platform (the one voice requests use) and
leaves the profile's CLI and other platforms alone:

| `--tools` | Enabled for voice | For |
|---|---|---|
| `safe` | web, x_search, memory, todo | Questions and lookups. Recommended default. |
| `home` | safe + homeassistant | Also control lights, switches and other Home Assistant devices. |
| `full` | safe + browser, terminal, file, code_execution, vision, image_gen, skills, connections, delegation, cronjob | Coding by voice: "what's the diff", "open a PR", "merge that". |
| a list, e.g. `web,memory,terminal,file` | exactly those | Anything in between. |

```bash
python3 skill/hermes-voice-setup/scripts/voice_profile.py --profile voice --tools safe
hermes -p voice gateway restart      # the gateway reads its tools at start
hermes -p voice tools list --platform api_server   # check
```

The list is exact: toolsets you leave out are disabled for voice. Run it again with another preset any
time. `full` lets a voice request run shell commands as your user; use it only where you trust everyone
in earshot, and consider a separate profile for it.

## 4c. Thinking effort

Reasoning models think before answering; for voice, less is usually better. `voice_profile.py --reasoning low`
sets `agent.reasoning_effort` for the profile (Hermes' default is medium). Restart the gateway afterwards.

## 5. Separate provider or a fallback

A voice profile cloned from your main agent uses the same provider login, so both share one rate limit.
During this project's testing the shared subscription returned HTTP 429, and without hermes_voice the
provider's error text would have been spoken. Options:

- Give the voice profile its own provider/API key: `hermes -p voice model` (or `hermes -p voice setup`).
- Add a fallback chain that Hermes tries on rate-limit/overload/connection errors:
  `hermes -p voice fallback add` (see Hermes docs, *Fallback providers*).
- Prefer a fast, cheap model for voice; latency is dominated by the model (2–6 s per plain turn in testing).

hermes_voice speaks a short configurable fallback ("I can't reach my brain right now, try again in a
minute.") when Hermes is rate limited, slow (timeout, default 30 s) or unreachable, and never reads out
raw error text.

## 6. Memory and privacy

- Long-term memory (`memories/USER.md`, `MEMORY.md`) is per profile, not per speaker. In testing, a
  codeword said in one conversation was saved and later visible to every conversation.
- The `user_key` option in hermes_voice sends `X-Hermes-Session-Key`. Hermes passes it to its memory
  provider as the gateway session key; per-user scoping applies to providers that use it (for example
  Honcho). The built-in file memory stays profile-wide.
- Device control: hermes_voice v0.1 gives Hermes no Home Assistant tools. If you want the voice agent to
  control devices, give the profile Home Assistant's MCP server (HA integration "Model Context Protocol
  Server") as an MCP server in its config, and limit what it exposes.
