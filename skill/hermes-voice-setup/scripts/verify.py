#!/usr/bin/env python3
"""Send a text turn through Home Assistant's conversation API to the Hermes agent and report it.

Each turn is one live model call on the Hermes profile. --recall adds a two-turn memory check.
Exit code 0 = Hermes answered; 2 = HA spoke the fallback message (Hermes unreachable/limited).

Examples:
  verify.py --token-file ~/.config/hermes-voice/secrets/ha_token
  verify.py --token-file TOKEN --agent-id conversation.voice --recall
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import read_text, die, ha_ws, http, say, set_dry_run  # noqa: E402

FALLBACK_HINT = "can't reach my brain"


def find_agent(ha: str, token: str) -> str:
    ws = ha_ws(ha, token)
    try:
        reg = ws.call("config/entity_registry/list")["result"]
    finally:
        ws.close()
    agents = [e["entity_id"] for e in reg if e.get("platform") == "hermes_voice"]
    if not agents:
        die("no hermes_voice conversation entity found; run ha_setup.py first")
    if len(agents) > 1:
        say(f"several Hermes agents found {agents}; using {agents[0]} (pass --agent-id to choose)")
    return agents[0]


def turn(ha: str, token: str, agent: str, text: str, cid: str | None) -> tuple[str, str | None, float]:
    body = {"text": text, "agent_id": agent, "language": "en"}
    if cid:
        body["conversation_id"] = cid
    t0 = time.perf_counter()
    status, r = http("POST", ha + "/api/conversation/process", token=token, body=body, timeout=120)
    dt = time.perf_counter() - t0
    if status != 200 or not isinstance(r, dict):
        die(f"conversation API returned HTTP {status}")
    return r["response"]["speech"]["plain"]["speech"], r.get("conversation_id"), dt


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ha-url", default="http://localhost:8123")
    p.add_argument("--token-file", default=os.path.expanduser("~/.config/hermes-voice/secrets/ha_token"))
    p.add_argument("--agent-id", help="conversation.* entity (default: the hermes_voice entity)")
    p.add_argument("--text", default="Who are you? Answer in one sentence.")
    p.add_argument("--recall", action="store_true", help="also check that a codeword is remembered on the next turn")
    p.add_argument("--dry-run", action="store_true", help="find the agent but make no model call")
    a = p.parse_args()
    set_dry_run(a.dry_run)
    ha = a.ha_url.rstrip("/")
    if not os.path.exists(a.token_file):
        die(f"token file not found: {a.token_file}")
    token = read_text(a.token_file)
    agent = a.agent_id or find_agent(ha, token)
    say(f"agent: {agent}")
    if a.dry_run:
        say("[dry-run] would send: " + a.text)
        return
    results = []
    speech, cid, dt = turn(ha, token, agent, a.text, None)
    results.append({"text": a.text, "reply": speech, "seconds": round(dt, 2)})
    if a.recall:
        s1, cid, d1 = turn(ha, token, agent, "For this chat only, my codeword is OSPREY. Just acknowledge.", None)
        s2, _, d2 = turn(ha, token, agent, "What is my codeword?", cid)
        results += [{"text": "codeword set", "reply": s1, "seconds": round(d1, 2)},
                    {"text": "codeword ask", "reply": s2, "seconds": round(d2, 2), "recalled": "OSPREY" in s2.upper()}]
    say(json.dumps(results, indent=2, ensure_ascii=False))
    if any(FALLBACK_HINT in r["reply"].lower() for r in results):
        say("Home Assistant spoke the fallback message: check the Hermes gateway and its provider (rate limit?).")
        sys.exit(2)
    if a.recall and not results[-1].get("recalled"):
        say("Recall check failed: the codeword was not remembered across turns.")
        sys.exit(3)


if __name__ == "__main__":
    main()
