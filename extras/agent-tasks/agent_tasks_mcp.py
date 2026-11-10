#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2"]
# ///
"""Read-only view of what the other Hermes agents are working on, for a voice profile (MCP, stdio).

It answers "what is Fritz working on?", "is anything blocked?", "what did Montclair finish today?" from
Hermes' own task boards (kanban) and scheduled jobs. It can only look: every command it runs is one of
`hermes kanban boards list`, `hermes kanban --board <b> list`, `hermes kanban --board <b> show <id>` and
`hermes -p <profile> cron list`, checked against an allow-list before it runs. It cannot create, assign,
comment on, block, complete, dispatch or hand off anything.

Environment:
  HERMES_BIN     the hermes command (default: hermes on PATH)
  AGENT_ALIASES  optional, e.g. "fritz=personal-coding,personal-integration,personal-codex"
                 (an agent name -> boards or assignees that count as its work)
"""

from __future__ import annotations

from datetime import datetime
import json
import os
import re
import shutil
import subprocess
import time

from mcp.server.fastmcp import FastMCP

HERMES = os.environ.get("HERMES_BIN") or shutil.which("hermes") or "hermes"
NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
STATUSES = ("running", "blocked", "review", "ready", "todo", "triage", "scheduled", "done")
ACTIVE = ("running", "blocked", "review", "ready", "todo", "triage", "scheduled")

mcp = FastMCP("agent-tasks")


def _allowed(args: list[str]) -> bool:
    """Only these read-only shapes may run."""
    if args == ["kanban", "boards", "list", "--json"]:
        return True
    if len(args) >= 5 and args[:2] == ["kanban", "--board"] and NAME.match(args[2]):
        rest = args[3:]
        if rest[0] == "list" and rest[-1] == "--json":
            flags = rest[1:-1]
            ok = {"--status": set(STATUSES), "--assignee": None}
            while flags:
                if len(flags) < 2 or flags[0] not in ok:
                    return False
                if ok[flags[0]] is not None and flags[1] not in ok[flags[0]]:
                    return False
                if ok[flags[0]] is None and not NAME.match(flags[1]):
                    return False
                flags = flags[2:]
            return True
        return (
            len(rest) == 3 and rest[0] == "show" and NAME.match(rest[1]) is not None and rest[2] == "--json"
        )
    return (
        len(args) == 4
        and args[0] == "-p"
        and NAME.match(args[1]) is not None
        and args[2:] == ["cron", "list"]
    )


def run(args: list[str], want_json: bool = True):
    if not _allowed(args):
        raise PermissionError(f"not a read-only command: {' '.join(args)}")
    proc = subprocess.run([HERMES, *args], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "failed")
    return json.loads(proc.stdout) if want_json else proc.stdout


def _aliases() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for part in os.environ.get("AGENT_ALIASES", "").split(";"):
        if "=" in part:
            name, vals = part.split("=", 1)
            out[name.strip().lower()] = {v.strip().lower() for v in vals.split(",") if v.strip()}
    return out


def _when(ts) -> str:
    if not ts:
        return ""
    t = datetime.fromtimestamp(float(ts))
    now = time.time()
    if now - float(ts) < 3600:
        return f"{int((now - float(ts)) // 60)} min ago"
    if t.date() == datetime.now().date():
        return t.strftime("today %-I:%M %p")
    return t.strftime("%b %-d %-I:%M %p")


def _boards() -> list[dict]:
    return [b for b in run(["kanban", "boards", "list", "--json"]) if not b.get("archived")]


def _belongs(agent: str, board: dict, task: dict) -> bool:
    """Is this task the agent's work? Its name in the assignee, or the board names/describes it, or an alias."""
    if not agent:
        return True
    a = agent.lower()
    names = {a} | _aliases().get(a, set())
    assignee = str(task.get("assignee") or "").lower()
    about = " ".join(str(board.get(k) or "") for k in ("slug", "name", "description")).lower()
    return any(
        n == assignee
        or n in assignee
        or n == board.get("slug")
        or n in about.split()
        or re.search(rf"\b{re.escape(n)}\b", about)
        for n in names
    )


@mcp.tool()
def agent_tasks(agent: str = "", status: str = "", board: str = "") -> str:
    """What the other Hermes agents (e.g. Fritz, Montclair) are working on, from their task boards. Read-only:
    you can see tasks but cannot start, assign, change or hand off anything, and should not offer to.
    agent: a name like "fritz" or "montclair" (empty = everyone). status: running, blocked, review, ready,
    todo or done (empty = everything not finished, plus what finished in the last day). board: a board slug.
    Answer briefly: counts, then the few tasks that matter (running, blocked, waiting for review)."""
    if status and status not in STATUSES:
        return f"Status must be one of: {', '.join(STATUSES)}."
    if agent and not NAME.match(agent):
        return "That isn't an agent name."
    try:
        boards = _boards()
    except (RuntimeError, PermissionError, subprocess.TimeoutExpired, ValueError) as err:
        return f"Couldn't read the task boards ({err})."
    if board:
        boards = [b for b in boards if b.get("slug") == board]
    found = []
    for b in boards:
        args = (
            ["kanban", "--board", b["slug"], "list"] + (["--status", status] if status else []) + ["--json"]
        )
        try:
            tasks = run(args)
        except (RuntimeError, PermissionError, subprocess.TimeoutExpired, ValueError):
            continue
        day_ago = time.time() - 86400
        for t in tasks:
            if not _belongs(agent, b, t):
                continue
            if not status and t.get("status") == "done" and float(t.get("completed_at") or 0) < day_ago:
                continue
            found.append((b, t))
    who = agent.capitalize() if agent else "the agents"
    if not found:
        return f"No {status + ' ' if status else 'open or recent '}tasks for {who} on the task boards."
    counts: dict[str, int] = {}
    for _, t in found:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    order = {
        s: i
        for i, s in enumerate(
            ("running", "blocked", "review", "ready", "todo", "triage", "scheduled", "done")
        )
    }
    found.sort(
        key=lambda bt: (
            order.get(bt[1]["status"], 9),
            -float(bt[1].get("started_at") or bt[1].get("created_at") or 0),
        )
    )
    lines = [
        f"Tasks for {who}: "
        + ", ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda x: order.get(x[0], 9)))
        + "."
    ]
    for b, t in found[:12]:
        bits = [
            f"[{t['status']}] {t['title']}",
            f"board {b['slug']}",
            f"by {t.get('assignee') or 'unassigned'}",
        ]
        if t["status"] == "blocked" and t.get("last_failure_error"):
            bits.append(f"error: {str(t['last_failure_error'])[:120]}")
        stamp = t.get("completed_at") if t["status"] == "done" else t.get("started_at") or t.get("created_at")
        if stamp:
            bits.append(_when(stamp))
        bits.append(f"id {t['id']}")
        lines.append(" - " + "; ".join(bits))
    if len(found) > 12:
        lines.append(f" ...and {len(found) - 12} more.")
    return "\n".join(lines)


@mcp.tool()
def agent_task(task_id: str, board: str = "") -> str:
    """Details of one task on an agent's board (from agent_tasks): what it is, its status, and its latest
    progress notes. Read-only. board: its board slug, if known."""
    if not NAME.match(task_id or ""):
        return "That isn't a task id."
    try:
        slugs = [board] if board and NAME.match(board) else [b["slug"] for b in _boards()]
    except (RuntimeError, PermissionError, subprocess.TimeoutExpired, ValueError) as err:
        return f"Couldn't read the task boards ({err})."
    for slug in slugs:
        try:
            d = run(["kanban", "--board", slug, "show", task_id, "--json"])
        except (RuntimeError, PermissionError, subprocess.TimeoutExpired, ValueError):
            continue
        t = d.get("task", d)
        lines = [
            f"{t.get('title')} [{t.get('status')}] on board {slug}, by {t.get('assignee') or 'unassigned'}."
        ]
        if t.get("body"):
            body = str(t["body"])
            try:  # some boards keep a JSON job spec in the body: its instruction is the readable part
                spec = json.loads(body)
                body = (
                    str(spec.get("instruction") or spec.get("goal") or body)
                    if isinstance(spec, dict)
                    else body
                )
            except ValueError:
                pass
            lines.append("About: " + " ".join(body.split())[:400])
        if d.get("latest_summary"):
            lines.append("Latest: " + " ".join(str(d["latest_summary"]).split())[:300])
        if t.get("last_failure_error"):
            lines.append("Last error: " + str(t["last_failure_error"])[:300])
        for c in (d.get("comments") or [])[-3:]:
            lines.append(
                f"Note ({_when(c.get('created_at'))}): " + " ".join(str(c.get("body", "")).split())[:200]
            )
        return "\n".join(lines)
    return f"No task {task_id} on the boards."


@mcp.tool()
def agent_schedules(agent: str) -> str:
    """The scheduled (cron) jobs of another Hermes agent, e.g. "fritz" or "montclair". Read-only."""
    if not NAME.match(agent or ""):
        return "That isn't an agent name."
    try:
        out = run(["-p", agent.lower(), "cron", "list"], want_json=False)
    except (RuntimeError, PermissionError, subprocess.TimeoutExpired) as err:
        return f"Couldn't read {agent}'s schedule ({err})."
    keep = [
        ln
        for ln in out.splitlines()
        if ln.strip()
        and "hermes update" not in ln
        and "Gateways may" not in ln
        and not ln.strip().startswith("Create one with")
    ]
    return "\n".join(keep[:30]) or f"{agent} has no scheduled jobs."


if __name__ == "__main__":
    mcp.run()
