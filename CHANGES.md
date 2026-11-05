# Changelog

## 0.1.0 — unreleased

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
  - 71 tests (pytest-homeassistant-custom-component), ruff and mypy clean.
- `hermes-voice-setup` Hermes skill, installable on its own (`hermes skills install`; carries the
  integration and wake word model in `assets/`), with idempotent, `--dry-run`-capable scripts:
  `voice_profile.py` (with `--tools safe|home|full|<list>`: what the voice assistant may do, and
  `--reasoning`: model thinking effort), `ha_setup.py`, `speech_setup.py` (Wyoming Whisper/Parakeet + Piper, HA internal URL),
  `verify.py`.
- Docs: architecture, voice profile setup, troubleshooting; Echo (echo-dot-assist) device guide.
- `wakeword/`: pinned, resumable microWakeWord pipeline for "hey hermes", and a trained model
  (`wakeword/model/hey_hermes.tflite` + `.json`; 5.3 % false rejects / 0 false accepts per hour on synthetic test data;
  in use on an Echo Dot 2nd gen).
- Release files built from the committed tree by `tools/build_release.sh`: integration zip, skill zip,
  wake word zip, SHA256SUMS.
