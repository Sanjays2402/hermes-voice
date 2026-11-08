#!/usr/bin/env python3
"""Hermes Eye: the Echo Show's camera for a Hermes voice profile (docs/eye.md in the repo).

Builds the camera app on this computer (signed with a key made here on the first build and kept for
updates), installs it on the Echo over USB ADB, gives it its token, and connects the `look` tool to the
profile. Run from the computer the Echo is plugged into. Idempotent, every step can be previewed with
--dry-run, and every profile file is backed up before it is changed.

Examples:
  eye_setup.py --check                                  # what is installed, what is missing
  eye_setup.py --profile voice --dry-run
  eye_setup.py --profile voice                          # build, install, connect, test
  eye_setup.py --profile voice --reinstall              # the app was signed with another key (or lost)
  eye_setup.py --uninstall                              # remove the app from the Echo

Building needs a JDK (17) and the Android SDK command-line tools with build-tools;34.0.0 and
platforms;android-25. --check prints the commands to install them. Nothing is installed without asking.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import act, die, http, read_env_file, read_text, say, set_dry_run, write_secret  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_SRC = os.path.join(SKILL_DIR, "assets", "eye", "app")
MCP_SRC = os.path.join(SKILL_DIR, "assets", "eye", "eye_mcp.py")
PACKAGE = "org.hermesvoice.eye"
PORT = 28940
STATE_DIR = os.path.expanduser("~/.config/hermes-voice")
WORK_DIR = os.path.join(STATE_DIR, "eye-build")  # the build and its signing key live here, not in the skill
TOKEN_FILE = os.path.join(STATE_DIR, "secrets", "eye_token")
BUILD_TOOLS = "34.0.0"
SDK_CANDIDATES = (
    os.environ.get("ANDROID_HOME", ""),
    os.environ.get("ANDROID_SDK_ROOT", ""),
    "/opt/homebrew/share/android-commandlinetools",
    "/usr/local/share/android-commandlinetools",
    os.path.expanduser("~/Library/Android/sdk"),
    os.path.expanduser("~/Android/Sdk"),
)
JDK_CANDIDATES = (
    os.environ.get("JAVA_HOME", ""),
    "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home",
    "/usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home",
    "/usr/lib/jvm/java-17-openjdk-amd64",
    "/usr/lib/jvm/java-17-openjdk-arm64",
    "/usr/lib/jvm/java-17-openjdk",
)


# ------------------------------------------------------------------------------------------ tools --


def find_sdk() -> str | None:
    for sdk in SDK_CANDIDATES:
        if sdk and os.path.exists(os.path.join(sdk, "build-tools", BUILD_TOOLS, "d8")) and os.path.exists(
            os.path.join(sdk, "platforms", "android-25", "android.jar")
        ):
            return sdk
    return None


def find_jdk() -> str | None:
    for jdk in JDK_CANDIDATES:
        if jdk and os.access(os.path.join(jdk, "bin", "javac"), os.X_OK):
            return jdk
    javac = shutil.which("javac")
    if javac:
        out = subprocess.run([javac, "-version"], capture_output=True, text=True)
        version = (out.stdout or out.stderr).split()[-1:] or ["0"]
        if version[0].split(".")[0].isdigit() and int(version[0].split(".")[0]) >= 17:
            return os.path.dirname(os.path.dirname(os.path.realpath(javac)))
    return None


def install_hint() -> str:
    if sys.platform == "darwin":
        return (
            "  brew install openjdk@17 android-platform-tools\n"
            "  brew install --cask android-commandlinetools\n"
            f'  yes | sdkmanager --licenses && sdkmanager "build-tools;{BUILD_TOOLS}" "platforms;android-25"'
        )
    return (
        "  sudo apt install openjdk-17-jdk-headless adb unzip   (or your distribution's equivalents)\n"
        "  Android command-line tools: https://developer.android.com/studio#command-line-tools-only\n"
        "  unzip into ~/Android/Sdk/cmdline-tools/latest, then:\n"
        f'  yes | ~/Android/Sdk/cmdline-tools/latest/bin/sdkmanager --licenses && '
        f'~/Android/Sdk/cmdline-tools/latest/bin/sdkmanager "build-tools;{BUILD_TOOLS}" "platforms;android-25"'
    )


# -------------------------------------------------------------------------------------------- adb --


class Adb:
    def __init__(self, serial: str | None) -> None:
        self.adb = shutil.which("adb") or die("adb not found. Install it:\n" + install_hint()) or "adb"
        devices = [ln.split()[0] for ln in self._run("devices").splitlines()[1:] if ln.strip().endswith("device")]
        if serial:
            if serial not in devices:
                die(f"no adb device {serial}; connected: {devices or 'none'}")
            self.serial = serial
        elif len(devices) == 1:
            self.serial = devices[0]
        else:
            die(
                "plug the Echo into this computer by USB (echo-dot-assist enables USB debugging)"
                if not devices
                else f"several adb devices ({', '.join(devices)}): pick one with --serial"
            )

    def _run(self, *args: str, stdin: bytes | None = None, check: bool = True) -> str:
        proc = subprocess.run([self.adb, *args], input=stdin, capture_output=True)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        if check and proc.returncode != 0:
            die(f"adb {' '.join(args[:2])} failed: {out.strip()[:300]}")
        return out

    def run(self, *args: str, stdin: bytes | None = None, check: bool = True) -> str:
        return self._run("-s", self.serial, *args, stdin=stdin, check=check)

    def shell(self, cmd: str, stdin: bytes | None = None, check: bool = True) -> str:
        return self.run("shell", cmd, stdin=stdin, check=check).replace("\r", "")


def echo_facts(adb: Adb) -> dict[str, str]:
    props = {
        p: adb.shell(f"getprop {p}").strip()
        for p in ("ro.product.device", "ro.build.version.name", "ro.build.version.sdk")
    }
    props["root"] = "yes" if "uid=0" in adb.shell("id") else "no"
    props["camera"] = "yes" if "Number of camera devices: 1" in adb.shell("dumpsys media.camera", check=False) else "no"
    props["installed"] = adb.shell(f"pm list packages {PACKAGE}", check=False).strip().replace("package:", "")
    ip = ""
    for line in adb.shell("ip -4 addr show wlan0", check=False).splitlines():
        line = line.strip()
        if line.startswith("inet "):
            ip = line.split()[1].split("/")[0]
    props["ip"] = ip
    return props


# ---------------------------------------------------------------------------------------- profile --


def hermes(*args: str) -> str:
    proc = subprocess.run(["hermes", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"hermes {' '.join(args[:4])} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def hermes_python(override: str | None) -> str:
    """The Python of the Hermes install (it has the mcp package the tool needs)."""
    candidates = [override] if override else []
    candidates += [os.path.expanduser("~/.hermes/hermes-agent/venv/bin/python")]
    for py in candidates:
        if py and os.access(py, os.X_OK):
            if subprocess.run([py, "-c", "import mcp.server.fastmcp"], capture_output=True).returncode == 0:
                return py
    die("could not find the Hermes Python with the mcp package; pass --python /path/to/hermes-agent/venv/bin/python")
    return ""


def backup(path: str, backup_dir: str) -> None:
    if os.path.exists(path):
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(path, os.path.join(backup_dir, os.path.basename(path)))


def connect_profile(a: argparse.Namespace, url: str, token: str) -> None:
    pdir = os.path.join(a.hermes_root, "profiles", a.profile)
    if not os.path.isdir(pdir):
        die(f"no profile {a.profile} in {a.hermes_root}/profiles")
    backup_dir = os.path.join(pdir, "hermes-voice-backups", time.strftime("%Y%m%d-%H%M%S") + "-eye")
    script = os.path.join(pdir, "scripts", "eye_mcp.py")
    if not os.path.exists(script) or read_text(script) != read_text(MCP_SRC):
        if act(f"copy the look tool to {script}"):
            backup(script, backup_dir)
            os.makedirs(os.path.dirname(script), exist_ok=True)
            shutil.copy2(MCP_SRC, script)
    env_path = os.path.join(pdir, ".env")
    if read_env_file(env_path).get("HERMES_EYE_TOKEN") != token and act(f"set HERMES_EYE_TOKEN in {env_path}"):
        backup(env_path, backup_dir)
        lines = read_text(env_path).splitlines() if os.path.exists(env_path) else []
        lines = [ln for ln in lines if ln.split("=", 1)[0].strip() != "HERMES_EYE_TOKEN"]
        lines.append(f"HERMES_EYE_TOKEN={token}")
        fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
    server = {
        "command": hermes_python(a.python),
        "args": [script],
        "env": {"EYE_URL": url, "EYE_TOKEN": "${HERMES_EYE_TOKEN}"},
    }
    # `config get` prints YAML with ${...} already filled in (and masked): compare the parts we set.
    current = hermes("-p", a.profile, "config", "get", "mcp_servers.eye")
    same = all(str(v) in current for v in (server["command"], script, url)) and "EYE_TOKEN" in current
    if not same and act(f"add the MCP server 'eye' (tool: look) to profile {a.profile}"):
        backup(os.path.join(pdir, "config.yaml"), backup_dir)
        hermes("-p", a.profile, "config", "set", "mcp_servers.eye", json.dumps(server))
        if a.restart and act(f"restart the {a.profile} gateway"):
            hermes("-p", a.profile, "gateway", "restart")
    if os.path.isdir(backup_dir):
        say(f"   backups: {backup_dir}")


# ------------------------------------------------------------------------------------------ steps --


def build(a: argparse.Namespace) -> str:
    sdk, jdk = find_sdk(), find_jdk()
    if not sdk or not jdk:
        die(
            f"building needs {'a JDK 17' if not jdk else ''}{' and ' if not jdk and not sdk else ''}"
            f"{'the Android SDK (build-tools ' + BUILD_TOOLS + ', platforms android-25)' if not sdk else ''}. "
            "Ask the user, then install with:\n" + install_hint()
        )
    apk = os.path.join(WORK_DIR, "build", "hermes-eye.apk")
    if not act(f"build Hermes Eye in {WORK_DIR} (signing key: {WORK_DIR}/build/eye.keystore, made on first build)"):
        return apk
    os.makedirs(WORK_DIR, exist_ok=True)
    # Fresh source each time; the build folder (and its key) stays, so updates install over the old app.
    for item in ("AndroidManifest.xml", "build.sh", "src"):
        path = os.path.join(WORK_DIR, item)
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)
    for item in ("AndroidManifest.xml", "build.sh", "src"):
        src = os.path.join(APP_SRC, item)
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, os.path.join(WORK_DIR, item))
    env = dict(os.environ, ANDROID_HOME=sdk, JAVA_HOME=jdk)
    proc = subprocess.run(["bash", os.path.join(WORK_DIR, "build.sh")], env=env, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(apk):
        die(f"build failed:\n{(proc.stdout + proc.stderr)[-1500:]}")
    say(f"   built {apk}")
    return apk


def install(adb: Adb, apk: str, facts: dict[str, str], reinstall: bool) -> None:
    if facts["installed"] and reinstall and act(f"uninstall the old {PACKAGE} (another signing key)"):
        adb.run("uninstall", PACKAGE, check=False)
    if not act(f"install {os.path.basename(apk)} on the Echo"):
        return
    out = adb.run("install", "-r", apk, check=False)
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in out:
        die("the app on the Echo was signed with another key (another computer, or a lost key). "
            "Run again with --reinstall: it removes the old app first (its token is given again).")
    if "Success" not in out:
        die(f"install failed: {out.strip()[:300]}")
    if act("let it use the camera and draw the REC badge and live window"):
        adb.shell(f"pm grant {PACKAGE} android.permission.CAMERA")
        adb.shell(f"appops set {PACKAGE} SYSTEM_ALERT_WINDOW allow")


def give_token(adb: Adb) -> str:
    if os.path.exists(TOKEN_FILE):
        token = read_text(TOKEN_FILE)
    else:
        token = secrets.token_urlsafe(32)
        if act(f"save a new camera token to {TOKEN_FILE} (0600)"):
            write_secret(TOKEN_FILE, token)
    data = f"/data/data/{PACKAGE}"
    if act("give the token to the app on the Echo"):
        uid = adb.shell(f"stat -c %u {data}").strip()
        if not uid.isdigit():
            die(f"could not read the app's user id ({uid[:80]})")
        adb.shell(f"mkdir -p {data}/files && cat > {data}/files/token", stdin=token.encode())
        adb.shell(
            f"chown -R {uid}:{uid} {data}/files && chmod 700 {data}/files && chmod 600 {data}/files/token"
            f" && restorecon -R {data}/files"
        )
        adb.shell(f"am startservice -n {PACKAGE}/.EyeService")
    return token


def test(url: str, token: str) -> None:
    for _ in range(10):
        status, _body = http("GET", url + "/health", timeout=3)
        if status == 200:
            break
        time.sleep(1)
    else:
        die(f"the app does not answer at {url} (same network? the Echo's firewall allows {PORT})")
    status, body = http("GET", url + "/status", token=token, timeout=5)
    if status != 200:
        die(f"/status answered {status}: is the token right?")
    say(f"   camera service {body.get('version')} answers, camera {body.get('state')}")
    status, body = http("GET", url + "/snapshot.jpg", token=token, timeout=10)
    if status == 200:
        say("   test picture taken (not saved); the REC badge shows on the Echo for ~15 s")
    elif status == 423 and isinstance(body, dict):
        why = "the shutter is closed" if body.get("covered") else "the mute button is on"
        say(f"   no test picture: {why}. Everything else is set up.")
    else:
        say(f"   test picture failed ({status}); see docs/eye.md")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", help="the voice profile that gets the look tool")
    p.add_argument("--serial", help="adb serial of the Echo, if more than one device is connected")
    p.add_argument("--echo-ip", help="the Echo's LAN address (default: read from the Echo)")
    p.add_argument("--hermes-root", default=os.path.expanduser("~/.hermes"))
    p.add_argument("--python", help="Python of the Hermes install (default ~/.hermes/hermes-agent/venv/bin/python)")
    p.add_argument("--check", action="store_true", help="show what is installed and what is missing")
    p.add_argument("--reinstall", action="store_true", help="remove an app signed with another key first")
    p.add_argument("--no-profile", action="store_true", help="only build and install the app")
    p.add_argument("--no-restart", dest="restart", action="store_false", help="don't restart the gateway")
    p.add_argument("--uninstall", action="store_true", help="remove the app from the Echo")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    set_dry_run(a.dry_run)

    if a.check:
        sdk, jdk = find_sdk(), find_jdk()
        say(f"JDK 17: {jdk or 'missing'}\nAndroid SDK: {sdk or 'missing'}\nadb: {shutil.which('adb') or 'missing'}")
        if not sdk or not jdk or not shutil.which("adb"):
            say("To install (ask the user first):\n" + install_hint())
        if shutil.which("adb"):
            facts = echo_facts(Adb(a.serial))
            say(json.dumps(facts, indent=2))
        return

    adb = Adb(a.serial)
    if a.uninstall:
        if act(f"uninstall {PACKAGE} from the Echo (app, permissions and its token)"):
            say(adb.run("uninstall", PACKAGE, check=False).strip())
        say("To remove the look tool too: hermes -p <profile> mcp remove eye (and HERMES_EYE_TOKEN from its .env)")
        return

    if not a.profile and not a.no_profile:
        die("--profile is required (or --no-profile to only install the app)")
    if a.profile == "default":
        die("refusing the default profile: use the dedicated voice profile")
    facts = echo_facts(adb)
    if facts["root"] != "yes":
        die("the Echo's adb shell is not root: this needs an Echo set up with echo-dot-assist")
    if facts["camera"] != "yes":
        die("no camera on this Echo (Hermes Eye is for the Echo Show)")
    ip = a.echo_ip or facts["ip"]
    if not ip:
        die("could not read the Echo's Wi-Fi address: pass --echo-ip")
    url = f"http://{ip}:{PORT}"
    say(f"Echo: {facts['ro.product.device']}, {facts['ro.build.version.name']}, at {ip}")

    install(adb, build(a), facts, a.reinstall)
    token = give_token(adb)
    if not a.no_profile:
        connect_profile(a, url, token)
    if not a.dry_run:
        test(url, token)
    say("Done. Ask Hermes \"what am I holding?\", or say \"video mode\". docs/eye.md has the details and the undo.")


if __name__ == "__main__":
    main()
