# Echo Dot via echo-dot-assist

hermes_voice is only the conversation agent. The device just needs to be a Home Assistant Assist voice
satellite. The supported path in v0.1 is an Amazon Echo running **echo-dot-assist** by Gamer92000:
https://github.com/Gamer92000/echo-dot-assist (MIT). Nothing from that project is copied here.

## What it does

echo-dot-assist keeps Fire OS and replaces the Alexa client with its own daemon. Home Assistant discovers the Echo as an
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
   supported. The Dot 2's normal path uses its micro-USB socket and a button gesture. The tested unit
   required opening the case and a hardware short after an unlock stalled. Read
   [Echo Dot 2 recovery on Windows](#echo-dot-2-recovery-on-windows) before recovering a stalled install.
   Echo 2 USB has to be soldered to pads inside. Keep the Echo off Wi-Fi and out of the Alexa app until it is unlocked.
3. When Home Assistant discovers the Echo (ESPHome), add it and pick the Assist pipeline whose
   conversation agent is your Hermes profile (`ha_setup.py --set-default` set this on the preferred pipeline).
   If HA runs in Docker Desktop (Mac/Windows), it does not see network discovery: add the Echo by hand
   (Settings → Devices & services → Add integration → ESPHome → the Echo's IP address, port 26053 rather than ESPHome's usual 6053, no encryption key). Give the Echo a
   fixed IP in your router so this keeps working.
   Set HA's internal URL to its LAN address (Settings → System → Network, or `speech_setup.py
   --internal-url`); the Echo downloads each spoken reply from that address.
4. Say the wake word ("Alexa" by default; see below to switch to "Hey Hermes"), then talk to your Hermes profile.

## Echo Dot 2 recovery on Windows

The following observations come from one second-generation Echo Dot (`biscuit`, RS03QR),
unlocked on Windows on 2026-11-04 and inspected through read-only ADB on 2026-11-05.
The assistant works on this unit. The recovery sequence below records what happened during an
interrupted install. A complete fresh install using this recovery path has not been independently repeated.

### Driver and recovery stall

The tested echo-dot-assist installer pinned firebreak 0.8.0. It completed the amonet unlock,
then hung at `[2/7] waiting for v2 recovery to start`. The Echo went solid blue, briefly appeared
as a MediaTek preloader, and stopped responding to the green-ring fastboot gesture.

Windows Memory Integrity blocked the MediaTek `usb2ser.sys` driver with a Code Integrity
“not compatible with hypervisor enforcement” error, `0xC0000220`. On this PC, temporarily
turning off Memory Integrity and restarting allowed the driver to load. Memory Integrity was
turned back on after recovery. Check the driver error before changing this Windows setting.

### Hardware short used on the tested unit

Recovering USB access required opening the Echo and forcing MediaTek bootrom mode.
Tools used were a Torx T8 screwdriver, plastic pry tool, uncoated metal paperclip and
micro-USB data cable. The board was marked `30-002148 REV 01`.

With power disconnected, the rubber base and screws were removed and the two boards were
opened gently on their connecting ribbon. The removable shield cover was lifted while leaving
the soldered shield frame in place. The short point was the tiny part directly next to C52
on the R60 side, beside the eMMC. It was shorted to the shield frame, which is ground.
C52 itself was not the short point.

The short was held before connecting USB to the PC and released only when firebreak said
“The short may come off now”. This took three attempts on the tested unit. No soldering was
needed, but opening the case and shorting the wrong point can damage the Echo.

Use the upstream board photos to identify the point before attempting this.
[R0rt1z2's biscuit unlock thread](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-dot-2nd-gen-2016-biscuit.4761416/)
and [Andy Goetz's board photographs](https://andygoetz.org/blog/2022/05/echo-dot-v2-dumping-emmc/)
show the hardware. firebreak's [short instructions](https://github.com/bboe/firebreak/blob/0a11b197ebadb3b24fc865215d5b4b7a0233ddb6/README.md)
describe the tool interaction.

The successful short used firebreak from commit
`0a11b197ebadb3b24fc865215d5b4b7a0233ddb6`, which reported version 0.11.0,
with `firebreak short --verbose`. It was launched through
`uvx --from git+https://github.com/bboe/firebreak` at the time.
For reproduction, pin that commit rather than relying on a moving main branch.
The run omitted `--device biscuit`, so it did not display the model-specific point instructions.

### Firmware mismatch and overlapping installers

The subsequent `firebreak unlock fireos6 --factory-reset --verbose` attempted build 8149 and
failed after its first OTA command with
`installing Fire OS 6 8149 left the active slot 'a', where it was 'a'`.
It returned in approximately one second. A rerun detected rooted build 8146 and exited.

At the audited firebreak commit, 8149 is the deliberate factory-reset default. The tested
echo-dot-assist copy pins the 8146 firmware artifact. Both use the display ID `NS65741`,
so checking that ID alone does not distinguish the builds.

The unchanged-slot error means firebreak did not observe the expected switch after TWRP
installed the inactive slot. The retained recovery log does not identify the cause.
An early TWRP failure or a command that never properly started is consistent with the short
return. Interference between installers is another unconfirmed possibility because the old
setup run was still waiting in a second window.

If an old installer is still waiting for the device, stop that waiting run before manual
recovery. A write already in progress must be allowed to finish. Both versions use
`/sdcard/update.zip` and can issue wipes, installs and reboots once recovery appears.
The factory-reset command erases data and writes firmware. It is not a diagnostic or a
recommended retry for an already-working satellite.

The rooted-system message on a rerun is an early exit when firebreak sees its target state.
The archived Windows setup also prints “both slots” after checking root access and the
running firmware ID. Neither message verifies both slot contents or a working fallback.
[Pinned install checks](https://github.com/bboe/firebreak/blob/0a11b197ebadb3b24fc865215d5b4b7a0233ddb6/firebreak/fireos.py#L208),
[build defaults](https://github.com/bboe/firebreak/blob/0a11b197ebadb3b24fc865215d5b4b7a0233ddb6/firebreak/devices/biscuit.py#L90)
and [already-rooted exit](https://github.com/bboe/firebreak/blob/0a11b197ebadb3b24fc865215d5b4b7a0233ddb6/firebreak/route.py#L534)
explain these behaviors.

### What the read-only slot check established

| Observation on the tested unit | Slot A | Slot B |
|---|---|---|
| System build | 8146, incremental 0013222531716 | 8146, incremental 0013222531716 |
| Boot priority | 15 | 0 |
| Remaining tries | 0 | 0 |
| Marked successful | Yes | No |
| Android boot image | Matching full-partition hash across both slots | Matching full-partition hash across both slots |
| Satellite startup hook, `/system/etc/init/hassmic.rc` | Present | Absent |

The unit boots A successfully. Zero remaining tries is normal for a slot already marked
successful. B's priority zero excludes it from automatic boot selection. Its build properties
are readable, but it was not boot-tested and lacks the satellite startup hook.

The echo-dot-assist system installer targets the active slot. Matching builds and boot images
therefore do not establish a second Hermes Voice installation. Keep the working slot selected.
Preparing a fallback requires reviewing its system contents and satellite installation, plus
a separately approved boot test and rollback plan. These observations do not establish the
slot state on another Echo.

### Temporary debug access

The settings page login needs the Echo's action button. Opening **System → Debug access**
requires a separate action-button approval, even from a browser already logged in.
It opens root ADB on TCP 5555 for 30 minutes.

During this inspection the Mac could not reach the Echo directly, while the existing Home
Assistant container could. A host connection timeout alone does not prove the Echo is offline.
Check reachability from the relevant machine before changing device settings or networking.
The running Fire OS image had no `bcbtool`, `bootctl` or `debugfs`. Boot-control bytes and
selected system files were read off-device without installing tools or mounting filesystems
on the Echo. A live boot test would require a reboot and separate approval.

## Wake word: "Hey Hermes"

echo-dot-assist uses Amazon's "Alexa" engine by default. It can instead run a microWakeWord model, and this
project ships one for "Hey Hermes" (`hey_hermes.tflite` + `hey_hermes.json`, in [`wakeword/model/`](../../wakeword/model)). Tested on an Echo Dot
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
