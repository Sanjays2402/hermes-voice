# Changelog

## Unreleased

- Tell Hermes the local date and time on every turn (Home Assistant's clock and time zone), so "what time
  is it?" and date questions work; option *Tell Hermes the date and time*, on by default. Before, Hermes
  searched the web for the time and could not read it.
- `ha_setup.py --set-default` also turns on *Prefer handling commands locally* in the pipeline (time,
  timers, device control answered by Home Assistant); `--no-local-commands` leaves it off.
- `ha_setup.py` sets the time zone of a Home Assistant it onboards to this computer's (`--time-zone` to
  choose); before, a new Home Assistant stayed on UTC and told the time hours off.
- Document the tested Echo Dot 2 Windows recovery, including the MediaTek driver block,
  hardware short, firebreak firmware defaults and possible interference between installers.
- Record the read-only A/B findings and the separate action-button approval for temporary debug access.

## 0.1.0 — 2026-11-04

First version.

- `hermes_voice` Home Assistant integration (HA 2026.10+):
  - config flow (gateway URL, API key, display name; validated with `GET /v1/models`), one entry per
    Hermes profile, reauth flow, options flow, diagnostics with the key redacted;
  - sends `X-Hermes-Session-Id` on every turn and only the new user message; session modes
    `per_conversation` (`ha-<conversation_id>`) and `per_device` (`ha-dev-<device>-<window>`, idle reset,
    persisted windows);
  - optional `X-Hermes-Session-Key` (`user_key` option, `{user_id}` / `{device_id}` placeholders);
  - voice cleanup of replies (markdown, code, URLs, emoji, tables), on by default;
  - streamed replies (on by default): HA starts speaking before the whole answer is written; Hermes' own
    tool calls are logged as external tool calls, and a configurable working phrase ("One moment.") is
    said when a tool starts before anything else;
  - optional voice instruction system message;
  - spoken fallback on rate limit, timeout, connection, auth and agent errors (including Hermes' HTTP 200
    replies that carry a failed run);
  - 81 tests (pytest-homeassistant-custom-component), ruff and mypy clean.
- `hermes-voice-setup` Hermes skill, installable on its own (`hermes skills install`; carries the
  integration and wake word model in `assets/`), with idempotent, `--dry-run`-capable scripts:
  `voice_profile.py` (with `--tools safe|home|full|<list>`: what the voice assistant may do, and
  `--reasoning`: model thinking effort), `ha_setup.py`, `speech_setup.py` (Wyoming Whisper/Parakeet + Piper, HA internal URL),
  `verify.py`.
- Docs: architecture, voice profile setup, troubleshooting; Echo (echo-dot-assist) device guide.
- `wakeword/`: pinned, resumable microWakeWord pipeline for "hey hermes", and a trained model
  (`wakeword/model/hey_hermes.tflite` + `.json`; 5.3 % false rejects / 0 false accepts per hour on synthetic test data;
  tested on a second-generation Echo Dot running echo-dot-assist). The bundled model is for
  non-commercial use only.
- Release files built from the committed tree by `tools/build_release.sh`: integration zip, skill zip,
  wake word zip, SHA256SUMS.

Setup updates restart the authenticated Home Assistant instance through its API.
