# Hermes Eye for a Hermes voice profile

`eye_mcp.py` is an MCP server (stdio) with one tool, `look`. It takes a fresh picture with the Echo Show's
camera through the Hermes Eye app ([docs/eye.md](../../docs/eye.md)) and returns it as an image. A model
with vision sees the picture in the same turn. If the shutter is closed or the mute button is on, `look`
says so in words.

## Setup

The setup skill does all of this in one step: `skill/hermes-voice-setup/scripts/eye_setup.py --profile voice`
(see [docs/eye.md](../../docs/eye.md#install)). By hand:

1. Install Hermes Eye on the Echo and make its token ([docs/eye.md](../../docs/eye.md)).
2. Copy the script into the profile and put the token in the profile's `.env` (back up both files first):

   ```bash
   cp extras/eye/eye_mcp.py ~/.hermes/profiles/<profile>/scripts/
   ```
   ```bash
   printf 'HERMES_EYE_TOKEN=%s\n' "$(cat eye_token.txt)" >> ~/.hermes/profiles/<profile>/.env
   ```

3. Add to the profile's `config.yaml`, then restart the gateway with `hermes -p <profile> gateway restart`:

   ```yaml
   mcp_servers:
     eye:
       command: /path/to/hermes-agent/venv/bin/python
       args:
         - /path/to/.hermes/profiles/<profile>/scripts/eye_mcp.py
       env:
         EYE_URL: http://<echo-ip>:28940
         EYE_TOKEN: ${HERMES_EYE_TOKEN}
   ```

The profile's model needs vision, so Hermes attaches tool images natively. Check with the snippet in the
research log ([2026-11-08-show5-camera.md](../../docs/research/2026-11-08-show5-camera.md)). On the Hermes
screen the call shows as "Looking through the camera", and the Echo's **● REC** badge is on while the
camera is open.

A picture stays in the conversation's context, and later turns of the same conversation carry it too.
