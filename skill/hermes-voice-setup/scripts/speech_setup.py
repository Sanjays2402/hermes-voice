#!/usr/bin/env python3
"""Add speech-to-text (Whisper) and text-to-speech (Piper) to Home Assistant's Assist pipeline.

Idempotent: running containers, existing Wyoming entries and pipeline settings are reused. Supports --dry-run.

  speech_setup.py --docker --data-dir ~/hermes-voice/speech --set-default --internal-url http://192.168.1.10:8123
  speech_setup.py --whisper-host 192.168.1.20 --piper-host 192.168.1.20 --set-default   # servers run elsewhere

With --docker it runs rhasspy/wyoming-whisper and rhasspy/wyoming-piper (pinned versions below), published on
127.0.0.1 only, with their models stored in --data-dir. An existing container is reused as is; to change its
model or settings, remove it (`docker rm -f hv-whisper`) and run again. Home Assistant reaches them at --wyoming-host
(default host.docker.internal, for Home Assistant running in Docker on the same machine).
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import act, die, ha_ws, http, read_text, say, set_dry_run  # noqa: E402

WHISPER_IMAGE = "rhasspy/wyoming-whisper:3.8.1"
PIPER_IMAGE = "rhasspy/wyoming-piper:2.5.2"


def docker_container(name: str, image: str, port: int, data_dir: str, args: list[str]) -> None:
    state = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}} {{.Config.Image}}", name],
                           capture_output=True, text=True)
    if state.returncode == 0:
        running, current = state.stdout.split()
        if current != image:
            say(f"container {name} exists with {current} (wanted {image}); leaving it as is")
        if running == "true":
            say(f"container {name} already running")
        elif act(f"start existing container {name}"):
            subprocess.run(["docker", "start", name], check=True, capture_output=True)
        return
    if not act(f"run container {name} ({image}) on 127.0.0.1:{port}, data in {data_dir}"):
        return
    os.makedirs(data_dir, exist_ok=True)
    subprocess.run(["docker", "run", "-d", "--name", name, "--restart", "unless-stopped",
                    "-p", f"127.0.0.1:{port}:{port}", "-v", f"{data_dir}:/data", image, *args],
                   check=True, capture_output=True)


def wait_port(host: str, port: int, seconds: int = 300) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(2)
    return False


def wyoming_entities(ha: str, token: str) -> list[str]:
    ws = ha_ws(ha, token)
    reg = ws.call("config/entity_registry/list")["result"]
    ws.close()
    return [e["entity_id"] for e in reg if e.get("platform") == "wyoming"]


def add_wyoming(ha: str, token: str, host: str, port: int, kind: str, force: bool) -> None:
    # Home Assistant's Wyoming flow does not reject a host:port it already has, so check first.
    existing = [e for e in wyoming_entities(ha, token) if e.startswith(kind + ".")]
    if existing and not force:
        say(f"Wyoming {kind} already set up in Home Assistant ({', '.join(existing)}); not adding {host}:{port}")
        return
    if not act(f"add Wyoming integration for {host}:{port}"):
        return
    status, flow = http("POST", ha + "/api/config/config_entries/flow", token=token,
                        body={"handler": "wyoming", "show_advanced_options": False})
    if status != 200:
        die(f"could not start Wyoming config flow: HTTP {status} {flow}")
    status, res = http("POST", f"{ha}/api/config/config_entries/flow/{flow['flow_id']}", token=token,
                       body={"host": host, "port": port})
    if res.get("type") == "create_entry":
        say(f"   added '{res.get('title')}'")
    elif res.get("type") == "abort" and res.get("reason") == "already_configured":
        say(f"   {host}:{port} already configured")
    else:
        die(f"Wyoming setup for {host}:{port} failed: {res.get('errors') or res.get('reason') or res}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ha-url", default="http://127.0.0.1:8123")
    p.add_argument("--token-file", help="HA long-lived token (else: secrets-dir/ha_token)")
    p.add_argument("--secrets-dir", default=os.path.expanduser("~/.config/hermes-voice/secrets"))
    p.add_argument("--docker", action="store_true", help="run the Whisper and Piper containers here")
    p.add_argument("--data-dir", default=os.path.expanduser("~/hermes-voice/speech"),
                   help="where the containers keep their models (with --docker)")
    p.add_argument("--whisper-model", help="speech-to-text model; default: the image's choice for the language "
                   "(Parakeet via sherpa-onnx for English, about 10x faster than faster-whisper on ARM)")
    p.add_argument("--cpu-threads", type=int, default=8, help="speech-to-text CPU threads")
    p.add_argument("--piper-voice", default="en_US-lessac-medium")
    p.add_argument("--language", default="en", help="speech-to-text language")
    p.add_argument("--wyoming-host", default="host.docker.internal",
                   help="host name Home Assistant uses to reach both servers")
    p.add_argument("--whisper-host", help="override --wyoming-host for Whisper")
    p.add_argument("--piper-host", help="override --wyoming-host for Piper")
    p.add_argument("--whisper-port", type=int, default=10300)
    p.add_argument("--piper-port", type=int, default=10200)
    p.add_argument("--set-default", action="store_true", help="use them in the preferred Assist pipeline")
    p.add_argument("--force-add", action="store_true",
                   help="add the Wyoming entries even if Home Assistant already has Wyoming stt/tts")
    p.add_argument("--internal-url", help="set Home Assistant's internal URL (e.g. http://192.168.1.10:8123). "
                   "Voice devices download each spoken reply from it; HA in Docker otherwise hands out its "
                   "container address, which the device cannot reach")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    set_dry_run(a.dry_run)
    ha = a.ha_url.rstrip("/")
    token = read_text(a.token_file or os.path.join(a.secrets_dir, "ha_token")).strip()

    # 1. servers
    if a.docker:
        data = os.path.abspath(os.path.expanduser(a.data_dir))
        docker_container("hv-whisper", WHISPER_IMAGE, a.whisper_port, os.path.join(data, "whisper-data"),
                         ["--language", a.language, "--cpu-threads", str(a.cpu_threads)]
                         + (["--model", a.whisper_model] if a.whisper_model else []))
        docker_container("hv-piper", PIPER_IMAGE, a.piper_port, os.path.join(data, "piper-data"),
                         ["--voice", a.piper_voice])
        if not a.dry_run:
            for port in (a.whisper_port, a.piper_port):
                if not wait_port("127.0.0.1", port):
                    die(f"nothing listening on 127.0.0.1:{port}; check `docker logs` of the container")

    # 2. Wyoming entries in Home Assistant
    add_wyoming(ha, token, a.whisper_host or a.wyoming_host, a.whisper_port, "stt", a.force_add)
    add_wyoming(ha, token, a.piper_host or a.wyoming_host, a.piper_port, "tts", a.force_add)
    if a.dry_run:
        say("dry run: skipping pipeline update")
        return

    # 3. pipeline
    ws = ha_ws(ha, token)
    if a.internal_url:
        current = ws.call("get_config")["result"].get("internal_url")
        if current == a.internal_url.rstrip("/"):
            say(f"internal URL already {current}")
        elif act(f"set Home Assistant internal URL to {a.internal_url} (was {current})"):
            res = ws.call("config/core/update", internal_url=a.internal_url.rstrip("/"))
            if not res.get("success"):
                die(f"could not set internal URL: {res.get('error')}")
    stt = tts = None
    for _ in range(15):
        reg = ws.call("config/entity_registry/list")["result"]
        wy = [e["entity_id"] for e in reg if e.get("platform") == "wyoming"]
        stt = next((e for e in sorted(wy) if e.startswith("stt.")), None)
        tts = next((e for e in sorted(wy) if e.startswith("tts.")), None)
        if stt and tts:
            break
        time.sleep(2)
    if not (stt and tts):
        die(f"Wyoming entities not found (stt={stt}, tts={tts})")
    say(f"speech-to-text: {stt}   text-to-speech: {tts}")
    if a.set_default:
        pl = ws.call("assist_pipeline/pipeline/list")["result"]
        pref = next(x for x in pl["pipelines"] if x["id"] == pl["preferred_pipeline"])
        want = {"stt_engine": stt, "stt_language": a.language, "tts_engine": tts,
                "tts_language": "_".join(a.piper_voice.split("-")[0].split("_")[:2]), "tts_voice": a.piper_voice}
        if all(pref.get(k) == v for k, v in want.items()):
            say(f"Assist pipeline '{pref['name']}' already uses them")
        elif act(f"set speech-to-text and text-to-speech on Assist pipeline '{pref['name']}'"):
            upd = {k: v for k, v in pref.items() if k != "id"} | want
            res = ws.call("assist_pipeline/pipeline/update", pipeline_id=pref["id"], **upd)
            if not res.get("success"):
                die(f"pipeline update failed: {res.get('error')}")
            say("   done")
    ws.close()


if __name__ == "__main__":
    main()
