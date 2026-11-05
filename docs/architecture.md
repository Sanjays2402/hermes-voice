# Architecture

```
voice device ──audio──▶ HA Assist pipeline ──text──▶ hermes_voice ──HTTP──▶ Hermes profile API server
   (Echo /                (STT: Whisper etc.)        (this repo)            (one profile = one URL)
    echo-dot-assist)      (TTS: Piper etc.) ◀──text── fallback/cleanup ◀── reply
```

## Components

| Part | Owner | Role |
|---|---|---|
| Voice device | echo-dot-assist (or any Assist satellite) | Wake word, microphone, speaker; streams audio to HA |
| Assist pipeline | Home Assistant | Speech-to-text, chooses the conversation agent, text-to-speech |
| `hermes_voice` | this repo | Conversation agent: session mapping, voice cleanup, error fallback |
| Hermes API server | Hermes Agent | Runs the profile's agent (SOUL.md, memory, tools, provider) for each turn |

## One turn

1. HA calls `HermesConversationEntity._async_handle_message(user_input, chat_log)`
   (`custom_components/hermes_voice/conversation.py`).
2. `SessionResolver` (`session.py`) picks the Hermes session id:
   - `per_conversation`: `ha-<chat_log.conversation_id>`;
   - `per_device`: `ha-dev-<key>-<window start>`, key = device id, else `user-<HA user id>`, else
     `conv-<conversation id>`; a new window starts after `idle_reset_minutes` without turns. Windows are
     stored with HA's `Store` helper (`.storage/hermes_voice.device_windows.<entry_id>`).
3. `HermesClient.async_chat_stream` (`client.py`) POSTs `/v1/chat/completions` with `stream: true` (or
   `async_chat` without streaming when the *Stream replies* option is off), with
   `Authorization: Bearer <API_SERVER_KEY>`, `X-Hermes-Session-Id`, optional `X-Hermes-Session-Key`, and
   `messages = [system: voice instruction + local date and time (+ HA extra_system_prompt)] + [user: new text]`.
   The time line ("Current local date and time: Thursday, November 5, 2026, 3:47 PM (America/Chicago).")
   comes from Home Assistant's clock and time zone; Hermes has no clock of its own. Option *Tell Hermes the
   date and time* turns it off.
   Hermes loads the session's history from its own database when the header is present
   (hermes-agent `gateway/platforms/api_server_openai_routes.py`), so earlier turns are not resent.
4. Errors are classified (`HermesRateLimitError`, `HermesTimeoutError`, `HermesConnectionError`,
   `HermesAuthError`, `HermesAgentError`). Hermes can return HTTP 200 with the provider's error text as the
   "answer"; the client treats `finish_reason: "error"`, `hermes.failed`, `X-Hermes-Completed: false` with a
   rate-limit `X-Hermes-Error`, and HTTP 502 `agent_incomplete` as failures. Any failure → the configured
   spoken fallback; details go to the HA log only.
5. The reply is cleaned for speech (`cleanup.py`: markdown, code, URLs → "link", emoji, tables) and added
   to HA's chat log; HA speaks it. When streaming, `VoiceChunker` passes it on sentence by sentence through
   `ChatLog.async_add_delta_content_stream`. Hermes' `hermes.tool.progress` events become external tool
   calls in the chat log (Hermes runs the tool, not HA). If a tool starts before Hermes said anything, the
   *working phrase* ("One moment.") is added first. Text followed by a tool call makes HA's Assist pipeline
   start streaming text-to-speech at once (`assist_pipeline/default_pipeline.py`); otherwise it starts once
   the reply passes 60 characters. Short replies are spoken when complete.

## Why the session header matters

Without `X-Hermes-Session-Id`, Hermes is stateless per request and names the session after
`sha256(system prompt + first user message)`, so two conversations that open with the same sentence share
one transcript, and HA must resend the whole history every turn (it drops it after 5 minutes idle).
With the header, each HA conversation (or device window) is its own Hermes session and Hermes owns the history.

## Profile selection

One config entry per gateway URL (unique id = normalized URL). Standalone gateways: one port per
profile. Multiplexed host gateway: `/p/<profile>/v1`. The `model` field does not select a profile; the
integration sends the model id the gateway advertises on `/v1/models`.

## Not in v0.1

- Home Assistant device control from Hermes (use HA's MCP server on the Hermes side instead).
- A native Hermes gateway platform adapter (instead of the OpenAI-compatible API server).
