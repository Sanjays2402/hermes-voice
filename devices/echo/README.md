# Echo Dot and Echo Show via echo-dot-assist

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
| Echo Show 5 1st gen (H23K37) | 2019 | checkers |

Also listed: Echo Show 5 2nd gen, Echo Show 8 (1st and 2nd gen) and Echo Input.
Tested by this project: the Echo Dot 2nd gen (from Windows) and the Echo Show 5 1st gen (from an Ubuntu
live USB). Not supported: Dot 3 from 2019–2020
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
   Echo 2 USB has to be soldered to pads inside. The Echo Show 5 needs Linux; read
   [Echo Show 5 1st gen on an Ubuntu live USB](#echo-show-5-1st-gen-on-an-ubuntu-live-usb).
   Keep the Echo off Wi-Fi and out of the Alexa app until it is unlocked.
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

## Echo Show 5 1st gen on an Ubuntu live USB

The following comes from one Echo Show 5 1st gen (`checkers`, model H23K37, bootloader build
`44072a3-20240709_170103`), installed on 2026-11-05 with echo-dot-assist's guided setup from an Ubuntu 26.04
live USB stick on a ThinkPad. The Show runs "Hey Hermes" with a Hermes profile behind it. One unit, one run:
treat the times and fixes below as what happened, not as guarantees.

### What it takes

- **A Linux PC, booted from Linux.** Unlike the Dot 2, the Show's unlock (R0rt1z2's amonet) and its install
  (the setup builds a boot image with an ARM tool under qemu) do not run on Windows or macOS. A live USB
  stick works: nothing is installed on the PC.
- **A second USB drive of 16 GB or more**, formatted ext4. The live session keeps files in RAM (about 7 GB
  here), and the setup's peak on disk was about 6.7 GB; an 8 GB stick was too small (below). FAT/exFAT
  does not work: the setup needs Linux file permissions and links.
- **Two free USB ports, or a hub**: one for the live stick (it runs the OS and must stay in), one for the
  work drive, one for the Show. Put the Show on a port of the PC itself, not on the hub.
- **A micro-USB data cable** and the Show's own power supply. No opening, no soldering on a working unit.
- **About two hours**, most of it waiting. Hands-on: a few minutes for the buttons and the Wi-Fi password.
- **The Show kept off Wi-Fi** until the setup's lockdown step. A factory reset is fine; leave it at Amazon's
  language screen. The unlock is patched in Fire OS 6575.1 (NS65751/8848): a Show that updated itself that
  far cannot be unlocked this way, and a factory reset does not undo an update.

### The four downloads

The setup asks for four files in `~/Downloads` and checks each by sha256, so names do not matter
(the browser saves the second `boot-root.zip` as `boot-root (1).zip` or similar). Direct links that worked:

| File | Where |
|---|---|
| `update-kindle-checkers-NS65741_user_8149_0013222532484.bin`, 1,372,825,620 bytes (Amazon's Fire OS 8149) | [Amazon's server](https://d1s31zyz7dcc2d.cloudfront.net/2026/9/28/4ce2e205-8886-4121-8433-0d149c9b4036/update-kindle-checkers-NS65741_user_8149_0013222532484.bin), linked from its [FTVDB record](https://ftvdb.com/echo/firmware/com.amazon.checkers.android.os/2a4c5a8f3af7da963955229bd1431411-13222532484-fire-os-6574-1-ns65741-8149-2026-09-28/) |
| `amonet-checkers-v2.0.1.zip` | [XDA attachment](https://xdaforums.com/attachments/amonet-checkers-v2-0-1-zip.6373840/) in the [checkers thread](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-show-5-1st-gen-2019-checkers.4762900/) |
| `boot-root.zip`, the Show 5's | [XDA attachment](https://xdaforums.com/attachments/boot-root-zip.6280867/), second post of the checkers thread |
| `boot-root.zip`, the Echo Dot 3's (only its policy tool is used) | [XDA attachment](https://xdaforums.com/attachments/boot-root-zip.6384590/) in the [donut thread](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-dot-3rd-gen-2018-donut.4801400/) |

XDA may ask you to sign in before it serves attachments. The checkers thread now calls **v2.0.2** current;
echo-dot-assist pins **v2.0.1** by checksum, which is still attached further down the first post. Download
v2.0.1. Check echo-dot-assist's `devices/checkers/README.md` for the current pins before you start.

### Step by step, with what happened

1. **Live USB, then the work drive.** Boot Ubuntu from the stick. Format the second drive as ext4 (the Disks
   app, or `sudo mkfs.ext4 -L echo /dev/sdX1` for the right `sdX`: check with `lsblk`), mount it, and work
   from there. This Ubuntu mounts drives under `/run/media/$USER/`, not `/media/$USER/`. Point `~/Downloads`
   at the drive (`ln -s <drive>/Downloads ~/Downloads`) so the 1.4 GB firmware does not land in RAM.
2. **Clone echo-dot-assist on the drive** and run `scripts/setup.sh checkers`. Type `yes`.
3. **Use the release build.** The setup asks *"Use it (recommended): nothing to compile"* only when GitHub
   has a release build of the commit you cloned. On 2026-11-05 the newest commit (`ae2e3ef`, a PC-side
   fix) had none yet, and the setup went straight to downloading the Android NDK (1 GB zip, several GB
   unpacked), which filled the disk: `write error (disk full?)`. What worked: check out the newest release
   tag (`git tag -l 'v*'`), here `v2026.11.05.000019-beta`. If that release lacks a fix you need, copy only
   the PC-side files over and mark them `git update-index --assume-unchanged` so the tree still counts as
   clean (that commit's fix: `scripts/lib/setup.sh` and `scripts/lib/device.sh`, a setup crash in the
   progress spinner). Or wait a few minutes after a push for GitHub to build it, then `git pull`.
4. **Downloads, USB access**: the setup picks up the files and installs its udev rules by itself.
5. **Fastboot.** With the Show on its power supply and the cable **not** in yet: unplug power, hold
   **Volume −, Volume + and Mute** together, plug power back in, and keep holding until the Amazon logo shows
   `FASTBOOT mode` in the bottom-left corner. Let go, then plug the micro-USB cable in. (echo-dot-assist's
   setup text says "both volume keys and the camera shutter"; amonet's own screen says Mute, and Mute is
   what worked. The camera shutter is a slider.) The PC sees it as USB `0bb4:0c01`.
6. **Unlock.** amonet prints `Detected: CHECKERS`, the bootloader build and the payload, then asks for
   `YES`. **Stay at the Show and watch its screen**: the guide says to follow instructions on both screens.
   Then:
   ```
   Sending payload, this might take a few attempts...
   Exploit most likely successful!
   Please wait until the process finishes and DO NOT INTERRUPT IT.
   ```
   **"Most likely successful" is not proof.** amonet prints it as soon as the Show stops answering. On the
   tested unit the first run left the bootloader locked: the Show restarted into Amazon's language screen,
   and Volume + at power-on gave the **stock recovery error screen** (Echo logo with a red triangle and
   "!", nothing on USB) instead of TWRP. Check from three-button fastboot:
   ```bash
   fastboot getvar unlock_status
   ```
   `false` means nothing was written: the Show is still stock and safe to try again. Stop the setup
   (Ctrl-C), run `scripts/setup.sh checkers` again and type `YES` again. The second run worked: the Show
   dropped off USB for a moment and came up in **TWRP's main menu** by itself. Others on the XDA thread
   report the same flakiness on this bootloader build; the author's advice is other ports or cables, and
   that **USB 2.0 ports work better**. Do not unplug anything once a run is going.
7. **System partition.** The tested Show came out of the unlock on build `0013222532996`; the setup replaced
   it with 8149 by itself. Order of events: unpacking the system image (about 10 minutes), a prompt to get
   TWRP to its main menu (TWRP's screen goes black after a while: tap it; if it asks "Keep System Read
   only?", tap **Keep Read Only**), wiping cache and data, then writing the 3.2 GB system in 49 chunks
   (**about 30 minutes** here, about one chunk a minute at first). From the wipe until the boot image is in,
   the Show cannot boot: keep power and USB in.
8. **Boot image, build, install**: unattended. The setup builds and flashes the root boot image, downloads
   the release build, checks the firmware, copies hassmic, restarts the Show twice, locks down its internet
   access and switches off Alexa's apps.
9. **Wi-Fi**: it asks for the network name and password (WPA2, the network Home Assistant is on).
10. **Kiosk Satellite** (optional, `y/N`): a Home Assistant dashboard on the screen. See
    [The screen](#the-screen) below.
11. It ends with `"Echo Show 5 xxxxxx" is running at <echo-ip>` and the next steps. **Copy the `secrets/`
    folder somewhere safe before you shut the live session down**: `update.key` signs this Echo's updates,
    and `wifi.conf` holds your Wi-Fi password. Then wipe the work drive or keep it private.

### Disk space on an 8 GB drive

Measured on the tested run: the firmware download is 1.37 GB; its `system.new.dat` unpacks to 2.57 GB;
`system.img` is 3.25 GB (about 2.6 GB of data); the unpacked firmware for the policy tool adds more. With the
repository, the peak was about 6.7 GB, on an 8 GB stick that holds 7.1 GB once formatted. It only fit because
the firmware download was moved to RAM (copied, checksum checked, replaced on the drive by a symlink) while the
system image was being unpacked. Use 16 GB. A slow stick (about 2.5 MB/s writes here) also makes every step
slower.

### After the install

- **Add it in Home Assistant**: ESPHome, `<echo-ip>`, port 26053, no encryption key (by hand when HA runs in
  Docker Desktop, which does not see discovery). Pick the Assist pipeline whose agent is your Hermes profile,
  and under **Configure** tick **Allow the device to perform Home Assistant actions** (echo-dot-assist uses it
  for Bluetooth announcements and wake word arbitration). The wake word slot comes up as "Alexa".
- **Settings page**: `http://<echo-ip>:28931/`. The Show has no action button: press **both volume keys
  together** wherever echo-dot-assist says "action button" (the login approval, debug access).
- **"Hey Hermes"**: as in [Wake word](#wake-word-hey-hermes) below, including step 5: after the upload the
  Show listens for nothing until you pick **Hey Hermes** in Home Assistant.
- Tested afterwards: "Alexa, what are your skills?" (answered by Hermes); "Alexa, what time is it?" (before
  hermes_voice sent the time, Hermes searched the web and could not tell it; now Home Assistant answers it
  locally); "Hey Hermes, how many days until Christmas?" (answered by Hermes, which now gets the date).

### The screen

hassmic shows its own screen on the Show, or another app:

- **The face** (default, nothing to set up): eyes that follow the assistant (listening, thinking, a moving
  mouth while it speaks, red when muted), the clock in Home Assistant's time zone, a **Stop** button while a
  timer rings, and what Music Assistant plays. Pull down from the top edge for the drawer: Home, Face, Music,
  Do not disturb, Screen off, volume.
- **Kiosk Satellite**: a Home Assistant dashboard while the Show stays the voice satellite. Set it up from a
  PC at `http://<echo-ip>:2324`: paste a Home Assistant long-lived token (Profile → Security), **set its
  remote admin password** (that page is open to your network), and leave its own voice assistant off. It is
  a third-party app with adb-level access that updates itself; echo-dot-assist's `docs/SECURITY.md` explains.
- The settings page's **Screen** page picks which one the Show starts with. Details: echo-dot-assist's
  [GUIDE.md](https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/GUIDE.md#the-screen-echo-show).

### Driving the setup from another computer

The tested run was driven over SSH from a Mac, with the live session's terminal shared through tmux:
`sudo apt install openssh-server`, `passwd` (a throwaway password for the live session), `ssh-copy-id
ubuntu@<thinkpad-ip>` from the other computer, then the setup inside `tmux new -s echo` (`tmux attach -t echo`
shows it on the PC itself). Installing openssh-server asked about a modified `sshd_config` in the live
image: the package maintainer's version is fine. Keep the PC awake during the long writes, for example
`sudo systemd-inhibit --what=sleep:idle:handle-lid-switch --mode=block sleep infinity` in a second window.
Over SSH, `udisksctl mount` fails (it wants a desktop session); `sudo mount /dev/sdX1 <dir>` works. Before
moving the work drive to another port, stop the setup and `adb kill-server`: the setup's adb server keeps the
drive busy.

## Hermes Screen on an Echo Show

A screen for the agent instead of hassmic's face: idle, a close-up of the avatar's eyes behind the clock and the
next scheduled job; on the wake word it zooms out to the whole avatar on the right while your words, Hermes'
tools and its answer appear on the left. Design and options: [docs/show-screen.md](../../docs/show-screen.md).
It is a small app (`devices/echo/screen-app/`, about 13 KB, network permission only) that shows the page
hermes_voice serves at `/hermes_voice/screen`.

1. **A Home Assistant user for the screen**, not an admin (Settings → People → Users → Add user, turn off
   "Administrator", "Can only log in from the local network" on). Log in as it once and create a long-lived
   token (Profile → Security). The screen needs nothing more.
2. **Build the app** on a Mac or Linux PC with a JDK and the Android SDK (`devices/echo/screen-app/build.sh`
   says what to install; it accepts Google's SDK license). Or take `hermes-screen.apk` from a release.
3. **adb to the Show**: on its settings page (`http://<echo-ip>:28931/`, log in with both volume keys),
   System → **Debug access**, approve with both volume keys again; adb is open on port 5555 for 30 minutes.
   ```bash
   adb connect <echo-ip>:5555
   adb install -r build/hermes-screen.apk
   adb shell am start -n org.hermesvoice.screen/.MainActivity \
     --es url 'http://<home-assistant>:8123/hermes_voice/screen?device=<device id>#token=<token>'
   ```
   The device id is in the Echo's device page address in Home Assistant. The token goes in the `#` part once:
   the page keeps it, and the app stores the address without it.
4. **Make it the home screen**: the settings page's **Screen** page lists the apps hassmic found when its screen
   started, so an app installed afterwards is missing until that restarts (a reboot, or `adb shell` and kill
   the screen's `app_process`, a child of `/system/hassmic/hassmic`; voice keeps running). Then pick
   **Hermes Screen**. The pull-down from the top still reaches hassmic's face and music; Home brings Hermes
   Screen back.

5. **Background work (optional)**: to show Hermes' running and finished tasks on the idle screen, run the
   read-only helper next to Hermes: `python3 skill/hermes-voice-setup/scripts/screen_activity.py --profile voice
   --install`. By default only the voice profile's tasks are shown; Hermes Voice → Configure → *Background
   work on the screen* switches to all profiles or off.

**Updating the app**: debug access again, then `adb install -r build/hermes-screen.apk` (signed with the same
key as the installed one; keep `build/screen.keystore`). The app clears its page cache on every start, and
the page reloads itself after an update of the integration. **Brightness**: hassmic does not set the
screen's brightness while another app is in front; the app does, starting at full. The overview (tap the
idle screen) has a slider that dims the whole screen, remembered by the app (down to 5 %, for a bedside
table).

To go back: pick the face on the Screen page; `adb uninstall org.hermesvoice.screen` removes the app. Everything
else Hermes Voice changes, and its undo: [docs/changes-and-undo.md](../../docs/changes-and-undo.md).

## Wake word: "Hey Hermes"

echo-dot-assist uses Amazon's "Alexa" engine by default. It can instead run a microWakeWord model, and this
project ships one for "Hey Hermes" (`hey_hermes.tflite` + `hey_hermes.json`, in [`wakeword/model/`](../../wakeword/model)). Tested on an Echo Dot
2nd gen and an Echo Show 5 1st gen. To load it:

1. Open the Echo's settings page, `http://<echo-ip>:28931/`.
2. The page asks for approval: press the **action button** (the one with the dot) on the Echo within about a
   minute. On an Echo Show, which has no action button, press **both volume keys** together.
3. Go to **Wake word**, switch the engine to **microWakeWord**, choose **your own model**, and select both
   files at once.
4. If the page shows a threshold for the model, set it to **0.92** (the value it was tuned for).
5. In Home Assistant, set the Echo's **Wake word** entity (`select.<echo>_wake_word`) to **Hey Hermes**
   (device page → Configuration → Wake word). Do not skip this: once the model is uploaded, "Alexa" is no
   longer offered and the wake word slot is left empty, so the Echo answers to nothing until you pick
   **Hey Hermes**.

Switching engines turns off "Alexa" on that Echo, so other Echos in the house stop competing with it. If it
misses you or wakes on its own, adjust the threshold (higher = fewer false wakes). The training pipeline is
in `wakeword/` in the repository. echo-dot-assist's own notes:
https://github.com/Gamer92000/echo-dot-assist/blob/main/docs/GUIDE.md#wake-word

## Other Assist satellites

Any device that works as an Assist satellite (for example Home Assistant Voice PE) uses whichever
conversation agent its pipeline names, so it can use hermes_voice too. v0.1 has not been tested on them.
