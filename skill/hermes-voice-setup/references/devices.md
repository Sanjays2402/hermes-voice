# Voice device: Echo Dot via echo-dot-assist

hermes_voice is only the conversation agent. The device just needs to be a Home Assistant Assist voice
satellite. The supported path in v0.1 is an Amazon Echo running **echo-dot-assist** by Gamer92000:
https://github.com/Gamer92000/echo-dot-assist (MIT). Nothing from that project is copied here.

## What it does

echo-dot-assist replaces Alexa on the Echo with its own daemon. Home Assistant discovers the Echo as an
ESPHome device, and you pick an Assist pipeline for it. Getting into the Echo relies on R0rt1z2's
unlock work (amonet, kamakiri, kaeru). Its README warns that the install can brick the Echo.

## Supported models (from its README, check there for the current list)

| Model | Year | Codename |
|---|---|---|
| Echo Dot 3rd gen (D9N29T; Fire OS 6574.1 only) | 2018 | donut |
| Echo Dot 2nd gen (RS03QR) | 2016 | biscuit |
| Echo 2nd gen (XC56PY) | 2017 | radar |

Also listed: Echo Show 5/8 (1st and 2nd gen) and Echo Input. Not supported: Dot 3 from 2019–2020
(C78MP8) and the Dot with clock (36EBT3).

## Walkthrough

1. Identify the model number on the bottom of the Echo, and check it against
   https://github.com/Gamer92000/echo-dot-assist/blob/main/devices/MODELS.md.
2. Follow the per-model guide `devices/<codename>/README.md` in that repo and its installer
   (`scripts/setup.sh`; Windows Dot 2 guide: `docs/WINDOWS.md`). Hermes Voice does not run this for you.
   Needs a Linux PC (live USB is fine, not a VM or WSL) or Windows 10/11 on Intel/AMD; macOS is not
   supported. Dot 2: no opening or soldering, just a micro-USB data cable and a button gesture. Echo 2:
   USB has to be soldered to pads inside. Keep the Echo off Wi-Fi and out of the Alexa app until it is unlocked.
3. When Home Assistant discovers the Echo (ESPHome), add it and pick the Assist pipeline whose
   conversation agent is your Hermes profile (`ha_setup.py --set-default` set this on the preferred pipeline).
   If HA runs in Docker Desktop (Mac/Windows), it does not see network discovery: add the Echo by hand
   (Settings → Devices & services → Add integration → ESPHome → the Echo's IP address, port 26053 rather than ESPHome's usual 6053, no encryption key). Give the Echo a
   fixed IP in your router so this keeps working.
   Set HA's internal URL to its LAN address (Settings → System → Network, or `speech_setup.py
   --internal-url`); the Echo downloads each spoken reply from that address.
4. Say the wake word ("Alexa" by default; see below to switch to "Hey Hermes"), then talk to your Hermes profile.

## Wake word: "Hey Hermes"

echo-dot-assist uses Amazon's "Alexa" engine by default. It can instead run a microWakeWord model, and this
project ships one for "Hey Hermes" (`hey_hermes.tflite` + `hey_hermes.json`, in this skill's `assets/wakeword/`). Tested on an Echo Dot
2nd gen. To load it:

1. Open the Echo's settings page, `http://<echo-ip>:28931/`.
2. The page asks for approval: press the **action button** (the one with the dot) on the Echo within about a
   minute.
3. Go to **Wake word**, switch the engine to **microWakeWord**, choose **your own model**, and select both
   files at once.
4. If the page shows a threshold for the model, set it to **0.92** (the value it was tuned for).
5. In Home Assistant, set the Echo's **Wake word** entity (`select.<echo>_wake_word`) to **Hey Hermes**.

Switching engines turns off "Alexa" on that Echo, so other Echos in the house stop competing with it. If it
misses you or wakes on its own, adjust the threshold (higher = fewer false wakes). The training pipeline is
in `wakeword/` in the repository. echo-dot-assist's own notes:
https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/GUIDE.md#wake-word

## Other Assist satellites

Any device that works as an Assist satellite (for example Home Assistant Voice PE) uses whichever
conversation agent its pipeline names, so it can use hermes_voice too. v0.1 has not been tested on them.
