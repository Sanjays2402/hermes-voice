# Hermes Eye: the Echo Show's camera for Hermes

Hermes Eye is a small app for an Echo Show running echo-dot-assist. It lets a vision service on your
network take pictures with the Show's camera. It has no screen of its own. It opens the camera only while
something asks for a picture or a stream, and closes it 15 seconds after the last request. While the
camera is open, a **● REC** badge shows in the top-right corner of the screen, over every app.

Source: [`devices/echo/eye-app`](../devices/echo/eye-app). Research and measurements:
[research/2026-11-08-show5-camera.md](research/2026-11-08-show5-camera.md). Tested on an Echo Show 5 1st gen
(checkers, Fire OS 6574.1 build 8149).

## Privacy

- **The shutter.** With the shutter closed, the camera sees a flat grey. A snapshot then answers
  `423` with `"covered": true` instead of a picture.
- **The mute button.** It cuts power to the sensor in the kernel, and no app can override it. The
  app reports `"state": "blocked"`. Muting also turns the microphones off.
- **The badge** is drawn by the same app that holds the camera, so it is on exactly while the camera is open.
- **Nothing is stored** on the Echo. Every request except `/health` needs the token. The app answers on the
  LAN only, on TCP 28940; echo-dot-assist's firewall already allows that range.

## Install

The easy way is to ask your Hermes agent to "set up the Hermes camera". The setup skill runs
`skill/hermes-voice-setup/scripts/eye_setup.py`, which does everything below. You can also run it yourself
from the computer the Echo is plugged into:

```bash
python3 skill/hermes-voice-setup/scripts/eye_setup.py --check
```
```bash
python3 skill/hermes-voice-setup/scripts/eye_setup.py --profile voice
```

It builds the app on your computer, signed with a key made there on the first build
(`~/.config/hermes-voice/eye-build/`). Nobody else's key or prebuilt app is involved. If you lose the key,
run it again with `--reinstall`. That removes the old app first and gives the new one a token again.

### By hand (over USB ADB, from the machine that set up the Echo)

```bash
devices/echo/eye-app/build.sh
```
```bash
adb install devices/echo/eye-app/build/hermes-eye.apk
```
```bash
adb shell pm grant org.hermesvoice.eye android.permission.CAMERA
```
```bash
adb shell appops set org.hermesvoice.eye SYSTEM_ALERT_WINDOW allow
```

Make a token and give it to the app. The ADB shell on an echo-dot-assist Echo is root. Keep the token
file private.

```bash
umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > eye_token.txt
```
```bash
U=$(adb shell stat -c %u /data/data/org.hermesvoice.eye | tr -d '\r'); adb shell "mkdir -p /data/data/org.hermesvoice.eye/files && cat > /data/data/org.hermesvoice.eye/files/token" < eye_token.txt; adb shell "chown -R $U:$U /data/data/org.hermesvoice.eye/files && chmod 600 /data/data/org.hermesvoice.eye/files/token && restorecon -R /data/data/org.hermesvoice.eye/files"
```
```bash
adb shell am startservice -n org.hermesvoice.eye/.EyeService
```

After this it starts by itself at every boot. To update it, rebuild and run `adb install -r`; the token
stays. To remove it, run `adb uninstall org.hermesvoice.eye`.

## API

Send `Authorization: Bearer <token>`, or HTTP Basic with the token as the password (that's what Home
Assistant's generic camera uses).

| Request | Answer |
|---|---|
| `GET /health` | `ok` (no token) |
| `GET /status` | JSON: `state` (closed, open, blocked), `width`, `height`, `fps_range`, `brightness`, `contrast`, `covered`, `dark`, `streams`, `error` |
| `GET /snapshot.jpg?q=85` | A fresh 1280×720 JPEG, with `X-Eye-Brightness`, `X-Eye-Contrast`, `X-Eye-State` and `X-Eye-Ms` headers. `423` with the status JSON when the shutter is closed or the camera is cut off. |
| `GET /stream.mjpeg?fps=5` | MJPEG (1–15 fps) while the connection is open. It ends if the camera is cut off. |

```bash
curl -s -H "Authorization: Bearer $(cat eye_token.txt)" http://<echo-ip>:28940/snapshot.jpg -o look.jpg
```

## Measured on an Echo Show 5 1st gen

| | |
|---|---|
| First picture (the camera opens) | about 1.1–1.2 s |
| Next pictures | about 0.07–0.11 s from the Echo, about 0.1 s end to end over Wi-Fi |
| Stream at 5 fps, 1280×720 | 50 frames in 10 s, about 250 KB/s. The app used ~6% CPU and the camera service ~10%. 38 °C. |
| Shutter closed | brightness ~45, contrast ~3 (open: ~84 and ~60) |
| Camera's own size | 1280×720 at 15 fps (OV9734 sensor), upright, not mirrored |

## Hermes can look

[`extras/eye`](../extras/eye) adds one tool, `look`, to a Hermes profile. Ask "What am I holding?" and
Hermes takes a picture and answers from it. Measured through the Echo Show's Assist pipeline: "You're
holding a pair of scissors with large, light turquoise handles." It took 5.8 s, about 1.1 s of that the
camera. With the Echo muted, Hermes answered "I can't see anything because your Echo's mute button is on."

## Video mode

Talking to Hermes on camera, like a video call. Your live picture shows in a rounded window in the
bottom-right corner (mirrored), the REC badge is on, and the Hermes screen carries on as usual.

- **On:** say "video mode", "start video" or "let's video chat", or ask anything that makes Hermes look
  ("look at this", "what am I holding?").
- **While on:** Hermes is told that video mode is on, and looks whenever a question might involve what it
  sees.
- **Off:** say "stop video", "turn off the camera" or "we're done with the camera". It also ends by itself
  after 5 minutes without talking, when the shutter is closed for 3 seconds, when the mute button cuts the
  camera, or if the screen stops checking in (60 s).
- **The nudge:** after three replies in a row that didn't use the camera, the screen asks "Done with the
  camera?" with **Turn it off** and **Keep it on** buttons. "Keep the camera on" by voice works too.

Home Assistant (`video.py`) decides when video mode is on. The Hermes screen page on the Echo tells the
Hermes Eye app over loopback (`/video?on=1|keep|0`, no token from the Echo itself). The app draws the window
and owns the camera, so a snapshot taken during video mode is quick (~0.07 s).
