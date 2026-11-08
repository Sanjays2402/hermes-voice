# The Echo Show 5's camera as Hermes' eyes — 2026-11-08

**Question.** Can Hermes get pictures from the Echo Show 5's built-in camera, processed on the Mac, without
breaking the working voice setup?

**Answer.** Yes. On our Show (already unlocked and rooted by the echo-dot-assist install, see
[2026-11-05-echo-show-5.md](2026-11-05-echo-show-5.md)) the camera works through Fire OS's normal Android
camera stack. A live frame reached the Mac over USB with nothing installed or changed on the Echo. No further
unlock, flashing or root step is needed. Fire OS, hassmic and the voice integration can stay as they are. The
missing piece is a small capture app on the Echo, and installing it is the first step that changes the device.

## 1. Hardware and firmware (read over USB ADB, read-only)

| | |
|---|---|
| Device | Echo Show 5 1st gen, H23K37, `ro.product.model` AEOCH, codename checkers, SoC MediaTek mt8163 |
| Firmware | Fire OS 6574.1, `NS65741/8149`, incremental 0013222532484, Fire OS 6.0 = Android 7.1.2 (API 25), `user` build, release-keys |
| State | Bootloader unlocked (amonet), `ro.secure=0`, `ro.debuggable=1`, verified boot `yellow`; the system image is the 8149 build echo-dot-assist pins |
| Camera | 1 camera, front-facing, orientation 270, MediaTek HAL `camera.mt8163.so`, **HAL1** (device version 0x100). Sensor driver loaded at open: **OV9734** (`ov9734_mipi_raw`, a 1280×720 sensor). No flash. |
| Privacy | Shutter slider → input switch `SW_CAMERA_LENS_COVER` on `gpio-keys` (set = covered). Mute button → `amazon-gating` (`/sys/devices/platform/amazon-gating/state`, 1 = gated), which cuts the mics **and** the camera in the kernel |

The sensor differs from the OV02B10 that TECHO5 documents for checkers, so TECHO5's register-level capture
code should not be assumed to fit this unit.

## 2. USB and diagnostic interfaces

- macOS sees the Show as USB `1949:0178` "Alexa" (Lab126), 480 Mb/s.
- **ADB over USB: available, and the shell is root** (`uid=0`, context `u:r:su:s0`). It comes from
  echo-dot-assist's root boot image. There is no `su` binary; none is needed.
- ADB over Wi-Fi (port 5555): closed now. It was open during the 2026-11-05 install and is off since a reboot.
- Fastboot and TWRP: present since the unlock, but reaching them needs a reboot with buttons held. Not
  used.
- Echo services listening: 26053 (hassmic ESPHome, voice) and 28931 (echo-dot-assist settings page).
- How Hermes reaches the Show today, unchanged by this work: Home Assistant ↔ hassmic over ESPHome (26053)
  for audio and wake word, and the Hermes Screen app (home app, `org.hermesvoice.screen`) ↔ Home
  Assistant's websocket for the screen.

## 3. Camera tests

| Test | Result |
|---|---|
| `dumpsys media.camera` | 1 camera, closed, no clients; `cameraserver` running |
| Shutter switch, before and after the user opened the slider | `SW_CAMERA_LENS_COVER` set, then clear |
| Stock camera app (`com.android.camera2`, preinstalled, CAMERA permission granted by default) with the Echo muted | "Can't connect to the camera". Kernel: `Failed to enable CAM, GATING Mode is ON`, `adopt_CAMERA_HW_Open err -5`. The mute is enforced below Android. |
| Same with the mute off, shutter open | Camera opened (`Device 0 is open`, client `com.android.camera2`), OV9734 powered on, live preview. One frame captured on the Mac with `adb exec-out screencap` about 7 s after launch. The image is not kept in the repo. |
| Afterwards | App force-stopped, Hermes Screen back in front, camera closed, satellite `idle`, wake word unchanged |

The only change on the Echo: the camera app's first-run location prompt was answered **Deny** on the Show
(it wants location to geotag photos). The camera app is left with no location access.

Observed side effect: Home Assistant's "mute" switch for the Show follows the hardware mute, so it is not a
software-only mute. Unmuting for the camera also turns the microphone on.

## 4. Approaches

| Approach | Verdict | Why |
|---|---|---|
| Android Camera API (Camera1 / HAL1) from an app on our rooted Fire OS | **Proven** | The stock app streamed a live preview through it |
| Stock camera app + ADB screencap | Proven, proof only | Takes over the screen, ~7 s, needs USB. Not for daily use. |
| Our own small capture app (Camera1, JPEG snapshot + MJPEG over HTTP on the LAN) | **Plausible, recommended** | Same API as the proven path. Normal APK, no root needed. Removable with `adb uninstall`. |
| ADB-only capture without an app (`screenrecord`, `cmd camera`) | Blocked | Android 7 has no shell camera command. `screenrecord` only records the screen. |
| Amazon's own camera features (Drop In, video calls, Show and Tell) | Blocked | Cloud-only, and the Alexa apps are switched off by echo-dot-assist |
| EchoLocal (camera, RTSP :8554, HA camera entity) | Plausible, not recommended | It installs on top of stock Fire OS as a full stack. It would sit alongside or replace echo-dot-assist on the same `/system`. Not documented as coexisting. Would risk the working voice setup. |
| TECHO5 (`echod`, raw ISP registers, `:8181/camera.jpg`, RTSP) | Blocked for us | It replaces Fire OS with a LineageOS-based system, and its documented sensor (OV02B10) is not our OV9734 |
| Bypassing the shutter or the mute gating | Not attempted, not proposed | Both are privacy controls. The design below honors them. |

## 5. Recommended implementation

```
Echo Show 5                         Mac Studio                                   Echo Show 5
──────────                          ──────────                                   ──────────
"Hermes Eye" app (Camera1)  ─LAN─▶  eye service                                  hassmic speaker
  GET /snapshot.jpg (on demand)      • pulls MJPEG at 2–5 fps only while needed       ▲
  GET /stream.mjpeg (low res)        • motion: frame difference (no model)            │
  GET /status (shutter, muted)       • person, hands, faces: Apple Vision              │
  token auth, LAN only               • events → Home Assistant (presence sensor) ─────┤ announce
                                     • MCP tool look(question) for Hermes  ──▶ Hermes ─┘ (Assist reply)
```

- **On the Echo: as little as possible.** A small app holds the camera, encodes JPEG with the hardware
  encoder and serves it over HTTP with a token. It reports the shutter and mute state and returns "camera
  covered" instead of a picture. Kept separate from the Hermes Screen app, so a camera crash can't take
  the screen down. It opens the camera only while someone is asking or a watch is on, which spares the
  mt8163; its load average is already high from voice and the screen.
- **On the Mac: deterministic first.** Snapshots on request. For "watching", a low-rate stream with frame
  differencing. On motion, Apple's Vision framework detects people, hands and faces, which is fast,
  on-device and free. A vision model is called only for a question or a notable event.
- **Hermes:** one MCP tool, `look(question)`, on the voice profile (the same pattern as the Mealie server).
  It grabs a fresh snapshot and returns it to the model with the question. "What am I holding?" becomes a
  voice question, answered through the existing pipeline.
- **Proactive replies** ("someone's at the counter"): the eye service raises a Home Assistant event, and an
  automation or Hermes uses `assist_satellite.announce` on the Show. No change to the voice path.
- **Privacy built in:** the shutter and the mute button always win, enforced by the kernel. The Hermes screen
  should show when the camera is in use. Frames stay on the LAN and the Mac. Nothing is stored unless asked.

Expected latency (estimate, to measure): a warm snapshot in about 0.2–0.5 s over Wi-Fi; a cold camera open
about 1 s; a vision-model answer adds the model's own time.

## 6. Fire OS and the voice integration

Both can stay intact. The recommended path needs no unlock, flash, reboot or root change. It adds one
ordinary app, removable with `adb uninstall`. The camera and the microphone are separate devices; the only
link is the mute button, which turns both off.

## 7. Next steps (each needs the user's go-ahead: the first one installs an app on the Echo)

1. Build the "Hermes Eye" APK (Camera1 → JPEG/MJPEG HTTP server, shutter and mute status, token). Install
   over USB ADB, grant CAMERA, start it as a foreground service. Undo: `adb uninstall`.
2. Measure: snapshot latency, frame rate at 640×480 and 1280×720, CPU and temperature on the Echo, any
   effect on wake-word response.
3. The Mac eye service: snapshot proxy, motion, Apple Vision person/hand detection, events to Home
   Assistant.
4. The MCP tool `look` for the voice profile. Then a "camera on" indicator on the Hermes screen.
5. Optional: a Home Assistant camera entity (generic camera on the snapshot URL) for dashboards and
   recordings.

## 8. Built: Hermes Eye (same day, user's go-ahead)

**What.** `devices/echo/eye-app`: a 21 KB APK built with the Screen app's Gradle-free toolchain. One
foreground service, Camera1 (`Camera.open(0)`), and a preview into an off-screen `SurfaceTexture` with
callback buffers. Without a GL context the preview still delivers frames on this HAL. The app copies a
frame only while a request waits, encodes JPEG with `YuvImage`, and serves HTTP on TCP 28940 with a token
(Bearer, or Basic with the token as the password). Details and API: [../eye.md](../eye.md).

**Evidence (2026-11-08, Echo on Wi-Fi, Mac on the LAN).**
- `/health` without a token: 200. `/status` without a token or with a wrong one: 401.
- First snapshot 1.17 s (the camera opens). The next ones took 0.10 and 0.10 s end to end
  (`X-Eye-Ms` 81 and 70). 1280×720, 85 KB, 15 fps range. Upright and not mirrored, so no rotation.
- MJPEG at 5 fps: 50 frames in 10 s, 2.5 MB. Per `top`: the app at 6% CPU, cameraserver at 10%. Thermal
  zone 0 at 38 °C.
- The camera closes after 15 s idle (`dumpsys media.camera`: "Device 0 is closed").
- Shutter: auto-exposure keeps a covered lens at brightness ~45, so a brightness threshold did not work.
  The luma standard deviation drops from ~60 to ~3, so `contrast < 10` = covered, and a snapshot then
  answers 423 `covered`.
- Mute: the kernel logs `gating_work_func: forcefully gating the cam sensor`. Frames stop with no error
  callback from the HAL (first build: requests timed out and the camera stayed "open"). Fixed: a timeout
  closes the camera and reports `blocked`. An app can't read `amazon-gating/state` (SELinux; `muted`
  shows null), so the state is "blocked", not "muted". After unmute, the next request reopened the camera
  normally.
- Indicator: an overlay window (`TYPE_SYSTEM_ERROR`, appop SYSTEM_ALERT_WINDOW granted over ADB). On
  screenshots, the corner had 240 red pixels with the camera open and 0 after the idle close. At the user's
  request, the dot became a camcorder-style "● REC" badge tucked into the top-right corner.
- The voice side was untouched throughout: satellite `idle`, wake word "Hey Hermes", ESPHome port up.

**Device changes (all undone by `adb uninstall org.hermesvoice.eye`).** The app; CAMERA granted; overlay
appop allowed; token file in the app's data folder. No firewall, firmware or system change.
- Final run (v0.2.3): with the shutter closed, snapshots answered 423 `covered`, and 200 again once it
  was opened. After mute, the first request timed out into `blocked` (5 s). Then each request failed fast
  with "startPreview failed" → `blocked` (~2 s). The camera closed, the REC badge was off (0 red pixels in
  the corner), and the satellite stayed `idle`. The Echo was left muted, at the user's choice.

## 9. Built: `look`, Hermes' tool for the camera (same day)

**Evidence first.** Hermes Agent caches an MCP `ImageContent` block and, when the main model takes images,
attaches it natively to the tool result (`tools/mcp_tool_content.py`, `_mcp_result_with_native_images`,
gated by `_should_use_native_vision_fast_path`). For the voice profile (openai-codex, gpt-6.1-sol,
`image_input_mode: auto`), `_native_tool_result_images` returned True. So `look` can return the JPEG
itself, with no second vision call. The profile's disabled `vision` toolset is a separate thing and
doesn't block this.

**Built.** `extras/eye/eye_mcp.py`: FastMCP, one tool `look(prompt)`. `prompt` is one of the argument
names Hermes uses for the tool's on-screen label. It returns a line of text and `Image(format="jpeg")`.
A 423 from the app becomes words: covered → "shutter is closed", otherwise → "mute button is on". The
voice profile got `mcp_servers.eye` and `HERMES_EYE_TOKEN` (backed up first). The screen shows the tool
as "Looking through the camera".

**Tests (Assist pipeline, Echo Show device).**
- Muted: Hermes called `look` (3.4 s in) and said "I can't see anything because your Echo's mute button is on.
  Unmute it, then ask me to look again." 6.5 s total.
- Unmuted, the user holding scissors: "What am I holding?" → `look` at 2.4 s, the picture back at 3.5 s,
  "You're holding a pair of scissors with large, light turquoise handles." 5.8 s total. The screen showed the
  chip "Looking through the camera · Identify what the user is holding" and the REC badge.
- Screen fix found during the look test: the page reveals a reply at speaking pace from the moment the
  satellite turns "responding". A turn with no speech, like these pipeline text tests, left the reply area
  blank. Now the whole reply shows if speech hasn't started 3 s after the reply is complete. Retest:
  "What do you see?" → "I see someone wearing glasses and a red shirt, seated at a desk…" was shown in full.

## 10. Built: video mode (same day, the user's design)

**User's design.** The live picture in the bottom right "so it feels more like a video call". It starts
with "video mode", or naturally with "look at this" or "what do you see". It stops with "stop video", and
after a few non-visual questions in a row the screen should ask whether the camera is still needed.
Otherwise it ends after 5 minutes. Hermes looks only when relevant (the user's choice over every turn).

**Built.**
- Hermes Eye 0.4.x: a hardware-accelerated overlay window (224×126 dp, rounded, mirrored `TextureView`) that
  the preview draws into. `/video?on=1|keep|0`, from loopback without a token, with CORS open for the
  screen page. It ends by itself on keep-alive expiry (60 s), a covered lens (3 s), or stalled frames
  (mute).
- `video.py` plus hub methods: whole-utterance commands answered locally, on when Hermes' tool is `look`,
  a system note while on, a nudge after 3 replies without `look`, off after 300 s idle. Websocket
  `hermes_voice/screen/video` for the buttons and for the Echo ending video.
- The screen page drives the Echo over loopback and keeps it alive every 20 s. The card is "Done with the
  camera? Turn it off / Keep it on".

**Tests on the Show.** "What am I holding?" turned video on and the window appeared.

The first run found a race: turning video on reopened the camera while `look`'s snapshot waited, so the
snapshot got 423 and `look` blamed the mute button. Fixed two ways:
- The camera app now moves the running preview into the window (`stopPreview`, `setPreviewTexture`,
  `startPreview`) without closing the camera. Log: "preview moved to the live window".
- `look` names the mute only when the state is `blocked`.

Retest: "I can see you, but what you're holding is below the camera."

Three non-visual questions brought up the card, and a tap on "Turn it off" ended video. The camera closed,
the window and badge were gone, and the log showed "video off: asked". "Stop video" by voice answered "Camera's
off." with no Hermes call. 165 unit tests, including `tests/test_video.py`.
