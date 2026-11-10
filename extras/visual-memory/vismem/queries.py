"""Answers from memory: what changed, where something was seen, what was there at a time, status.

Each answer is a dict with the facts and a `say` text written for Hermes to speak from. The wording keeps
three cases apart, from the stored evidence (not from the model's phrasing):
  visible               "It's on the left."
  possibly_obstructed   "I last saw it on the left, but the view may be obstructed now."
  missing               "It was on the left, but I don't see it there anymore."
"""

from __future__ import annotations

from datetime import datetime, timedelta
import time
from typing import Any

from .memory import BELIEVED_PRESENT, Memory, colours, words
from .vision_local import Visibility

STOP = {
    "the",
    "a",
    "an",
    "my",
    "our",
    "your",
    "where",
    "is",
    "are",
    "was",
    "were",
    "did",
    "do",
    "you",
    "see",
    "saw",
    "last",
    "it",
    "its",
    "there",
    "here",
    "what",
    "which",
    "of",
    "on",
    "in",
    "at",
    "to",
    "me",
    "show",
    "still",
    "find",
    "any",
    "some",
    "thing",
    "object",
    "this",
    "that",
    "desk",
    "table",
    "hermes",
    "please",
    "have",
    "has",
}


def when(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return "never"
    now = now or time.time()
    t, n = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    clock = t.strftime("%-I:%M %p")
    if now - ts < 90:
        return "just now"
    if now - ts < 3600:
        return f"{max(2, round((now - ts) / 60))} minutes ago ({clock})"
    if t.date() == n.date():
        return f"today at {clock}"
    if t.date() == (n - timedelta(days=1)).date():
        return f"yesterday at {clock}"
    return t.strftime(f"%A %B %-d at {clock}")


def _attrs(item: dict) -> str:
    a = item.get("attributes") or []
    return f" ({', '.join(a)})" if a else ""


def describe_item(item: dict, now: float) -> str:
    """One sentence about where an item is, keeping visible / obstructed / gone apart."""
    label, at = item["label"], item.get("last_position") or "in view"
    seen = when(item["last_seen"], now)
    near = item.get("last_near") or []
    beside = f", next to the {' and the '.join(near)}" if near else ""
    st = item["status"]
    if st == "visible":
        verified = item.get("last_verified") or item["last_seen"]
        return f"The {label} is {at}{beside} (seen {seen}; the view looked unchanged {when(verified, now)})."
    if st == "unconfirmed":
        return (
            f"The {label} was last picked out {at}{beside} {seen}; it wasn't picked out in the latest look, "
            f"but its spot looked unchanged, so it's very likely still there."
        )
    if st == "possibly_obstructed":
        return (
            f"I last saw the {label} {at}{beside} {seen}, but the view of that spot may be obstructed now "
            f"(something is in front of it). It may still be there."
        )
    if st == "earlier":
        return (
            f"I last saw the {label} {at}{beside} {seen}, before visual memory was last turned on; I haven't "
            f"seen it since."
        )
    if st == "missing":
        return (
            f"The {label} was {at}{beside} {seen}, but I don't see it there anymore, and that spot is in "
            f"clear view. It may have been moved or taken away; I can't tell where it went."
        )
    return (
        f"I last saw the {label} {at}{beside} {seen}. The picture since then wasn't clear enough to say "
        f"whether it's still there."
    )


def camera_note(mem: Memory) -> str:
    lv = mem.live
    if not mem.enabled:
        return "Visual memory is off (not observing), so this is from earlier observations."
    if lv.visibility is Visibility.UNREADABLE:
        return (
            f"The camera may be obstructed right now (since {when(lv.since)}), so this is from before that."
        )
    if lv.visibility is Visibility.UNCERTAIN:
        return "The picture is not very clear right now."
    return ""


def status(mem: Memory) -> dict[str, Any]:
    now = mem.clock()
    st, lv = mem.store, mem.live
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    calls = st.usage(today)
    base = st.scene(mem.baseline_id)
    counts: dict[str, int] = {}
    for i in st.items():
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    hour = [u for u in calls if u["ts"] > now - 3600 and u["trigger"] != "asked"]
    out = {
        "on": mem.enabled,
        "mode": mem.mode,
        "session": mem.session_id,
        "camera": lv.visibility.value if mem.enabled else "paused",
        "camera_why": lv.why,
        "since": lv.since,
        "steady": lv.steady,
        "analyzing": lv.analyzing,
        "deferred": lv.deferred,
        "last_error": lv.last_error,
        "samples": lv.samples,
        "brightness": round(lv.brightness, 1),
        "contrast": round(lv.contrast, 1),
        "sharpness": round(lv.sharpness, 1),
        "baseline": base and {"scene": base["id"], "ts": base["ts"]},
        "items": counts,
        "model_calls_today": len(calls),
        "model_failures_today": sum(1 for u in calls if not u["ok"]),
        "auto_calls_last_hour": len(hour),
        "tokens_today": {
            "input": sum(u["input_tokens"] or 0 for u in calls),
            "cached": sum(u["cached_tokens"] or 0 for u in calls),
            "output": sum(u["output_tokens"] or 0 for u in calls),
        },
        "limits": {
            "auto_per_hour": mem.cfg.auto_per_hour,
            "auto_per_day": mem.cfg.auto_per_day,
            "manual_per_hour": mem.cfg.manual_per_hour,
            "min_auto_interval": mem.cfg.min_auto_interval,
        },
        "model": mem.vision.model,
        "retention_days": mem.cfg.retention_days,
    }
    if not mem.enabled:
        say = "Visual memory is off: I'm not observing."
    else:
        cam = {
            "clear": "the view is clear",
            "uncertain": "the picture isn't very clear",
            "unreadable": "the camera may be obstructed",
        }[lv.visibility.value]
        say = f"Visual memory is on and {cam}."
        if mem.first_pending:
            say += " I'm taking my first look."
        elif mem.workspace:
            say += " I'm watching your desk (the work surface in view), not the rest of the room."
        else:
            say += " I'm watching the whole view."
    if base:
        say += f" The last reliable scene is from {when(base['ts'], now)}."
    say += f" Model analyses today: {len(calls)}."
    out["say"] = say
    return out


def refresh(mem: Memory, wait: float | None = None) -> str:
    """Before answering about the present: if the current steady view differs from memory and wasn't analysed
    (e.g. automatic looks are at their limit), take a look now. Free when nothing changed (a local check)."""
    if not mem.enabled:
        return ""
    r = mem.analyze_now(wait=wait if wait is not None else (mem.cfg.quick_wait or 120.0))
    if r["state"] == "busy":
        return "I'm still looking at the latest view; this is from the last look I finished."
    if r["state"] == "limited":
        return f"I didn't take a fresh look ({r['why']})."
    return ""


def _a(label: str) -> str:
    """'a mug', 'an orange', 'keys' (no article for plurals)."""
    if label.endswith("s") and not label.endswith("ss"):
        return label
    return ("an " if label[:1].lower() in "aeiou" else "a ") + label


def _list(labels: list[str]) -> str:
    things = [_a(x) for x in labels]
    return things[0] if len(things) == 1 else ", ".join(things[:-1]) + " and " + things[-1]


def _names(labels: list[str]) -> str:
    labels = [f"the {x}" for x in labels[:3]]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def brief(changes: list[dict]) -> str:
    """The few words to say out loud; the pictures and the details are on the screen. When something is
    gone ("what did I take away?"), moves and guesses stay on the screen."""
    by: dict[str, list[str]] = {}
    for c in changes:
        d = c["detail"] or {}
        if c["kind"] == "moved" and d.get("from") == d.get("to"):
            continue  # nudged, not moved: it is still where it was
        by.setdefault(c["kind"], []).append(_tidy(d.get("label", "something")))
    if "unchanged" in by:
        return "Nothing changed."
    parts = []
    if by.get("missing"):
        gone = by["missing"]
        plural = len(gone) > 1 or (gone[0].endswith("s") and not gone[0].endswith("ss"))
        parts.append(f"{_names(gone)} {'are' if plural else 'is'} gone".capitalize())
    new = by.get("added", []) + by.get("returned", [])
    if new:
        parts.append("New: " + ", ".join(_a(x) for x in new[:3]))
    if by.get("moved") and not by.get("missing"):
        parts.append(f"{_names(by['moved'])} moved".capitalize())
    if not parts and by.get("possibly_obstructed"):
        parts.append(f"{_names(by['possibly_obstructed'])} may just be hidden".capitalize())
    return ". ".join(parts) + "." if parts else "I don't see any clear changes."


def _tidy(label: str) -> str:
    return label.replace("-", " ") if " " not in label else label


def what_changed(mem: Memory, fresh: bool = True, wait: float = 22.0) -> dict[str, Any]:
    if fresh and mem.enabled:
        r = mem.analyze_now(wait=wait)
        if r["state"] == "busy":
            say = "I'm still comparing what I see now; the pictures will come up on the screen in a moment."
            return {"pending": True, "scene": None, "changes": [], "brief": say, "say": say}
        if r.get("trigger") == "first" or (r["state"] == "busy" and mem.first_pending):
            # No earlier look to compare with (the first one failed or hasn't finished): say what's there now,
            # never call everything "new".
            now_brief = describe_now(mem) if r["state"] == "done" else ""
            say = "I didn't get a look before, so I can't tell what changed."
            say += f" {now_brief}" if now_brief else " I'm taking my first look now."
            return {"scene": None, "changes": [], "brief": say, "say": say}
        note = f"I didn't take a fresh look ({r['why']})." if r["state"] == "limited" else ""
        base = mem.store.scene(mem.baseline_id)
        last = mem.store.get("last_comparison")
        last_scene = mem.store.scene(int(last)) if last else None
        if r["state"] == "same" and base and (last_scene is None or last_scene["ts"] < base["ts"]):
            # Looks the same as the last look, and nothing was compared since: say so, not an old comparison.
            say = "Nothing changed."
            return {
                "scene": None,
                "changes": [],
                "brief": say,
                "say": f"Nothing has changed since I looked {when(base['ts'], mem.clock())}.",
            }
        if r["state"] == "unreadable":
            say = "I can't see right now: " + (camera_note(mem) or "the camera view is blocked.")
            return {"scene": None, "changes": [], "brief": say, "say": say}
    else:
        note = ""
    st, now = mem.store, mem.clock()
    sid = st.get("last_comparison")
    scene = st.scene(int(sid)) if sid else None
    cam = camera_note(mem)
    if scene is None:
        say = "I have no comparison yet: I need a reliable view first, and then a changed one."
        if mem.baseline_id:
            say = "Nothing has changed since I started observing."
        return {"scene": None, "changes": [], "brief": say, "say": " ".join(x for x in (say, cam, note) if x)}
    prev = st.scene(scene["prev"])
    changes = st.changes(scene["id"])
    stayed = []
    if prev and scene["kind"] == "model":
        before = {o["item"] for o in st.observations(prev["id"])}
        moved_or_new = {c["item"] for c in changes}
        stayed = [
            o["label"]
            for o in st.observations(scene["id"])
            if o["item"] in before and o["item"] not in moved_or_new
        ]
    lines = []
    for c in changes:
        d, k = c["detail"] or {}, c["kind"]
        label = d.get("label", "something")
        if k == "unchanged":
            lines.append("Nothing changed: the view looks the same as before.")
        elif k == "added":
            lines.append(f"New: {_a(label)}, {d.get('to')}.")
        elif k == "returned":
            lines.append(f"The {label} is back, {d.get('to')}.")
        elif k == "moved":
            lines.append(f"The {label} moved from {d.get('from') or 'where it was'} to {d.get('to')}.")
        elif k == "missing":
            lines.append(
                f"The {label} is no longer visible {d.get('at')}; that spot is in clear view now "
                f"(not seeing it doesn't prove it was taken away)."
            )
        elif k == "possibly_obstructed":
            lines.append(
                f"The {label} was {d.get('at')}; I can't see it now, but the view there may be "
                f"obstructed, so it may still be there."
            )
        elif k == "unconfirmed":
            lines.append(f"The {label} wasn't picked out this time, but its spot looks unchanged.")
        elif k == "uncertain":
            lines.append(f"I can't tell about the {label}: the picture wasn't clear enough.")
        elif k == "view_changed":
            lines.append("The camera's view itself shifted, so positions can't be compared reliably.")
    head = f"Comparing {when(prev['ts'], now) if prev else 'earlier'} with {when(scene['ts'], now)}"
    if scene["after_obstruction"]:
        head += " (after the camera's view was interrupted)"
    if not any(c["kind"] != "unconfirmed" for c in changes) and scene["kind"] == "model":
        lines.insert(0, "I found no clear changes.")
    if stayed:
        lines.append(f"Still there: {', '.join(stayed)}.")
    say = head + ": " + " ".join(lines)
    return {
        "scene": scene["id"],
        "prev": prev and prev["id"],
        "ts": scene["ts"],
        "kind": scene["kind"],
        "changes": changes,
        "stayed": stayed,
        "brief": brief(changes),
        "say": " ".join(x for x in (say, cam, note) if x),
    }


def find(mem: Memory, query: str, limit: int = 4, fresh: bool = False) -> dict[str, Any]:
    note = refresh(mem) if fresh else ""
    now = mem.clock()
    q = words(query) - STOP
    qcol = colours(query, [])
    nouns = q - qcol
    scored = []
    for item in mem.store.items():
        have = (
            words(item["label"])
            | words(item.get("category") or "")
            | words(" ".join(item.get("attributes") or []))
        )
        if nouns and not (nouns & have):
            continue
        icol = colours(item["label"], item.get("attributes") or [])
        colour = "match" if qcol & icol else "unknown" if not icol else "different" if qcol else "n/a"
        score = len(nouns & have) * 2 + (
            3 if colour == "match" else 1 if colour in ("unknown", "n/a") else -3
        )
        score += 0.5 if item["status"] in BELIEVED_PRESENT else 0
        scored.append((score, item["last_seen"], item, colour))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    hits = [{**item, "colour": colour} for _, _, item, colour in scored[:limit]]
    if not hits:
        say = f"I haven't seen anything like a {query} in what I've observed."
        return {"query": query, "items": [], "say": " ".join(x for x in (say, camera_note(mem)) if x)}
    parts = []
    exact = [h for h in hits if h["colour"] in ("match", "n/a")]
    unknown = [h for h in hits if h["colour"] == "unknown"]
    other = [h for h in hits if h["colour"] == "different"]
    for h in exact[:2]:
        parts.append(describe_item(h, now))
    if qcol and not exact and unknown:
        parts.append(f"I haven't seen one I could tell was {' or '.join(sorted(qcol))}.")
    for h in unknown[:2]:
        if qcol:
            parts.append(
                f"There was a {h['label']} whose colour I couldn't make out: " + describe_item(h, now)
            )
        elif h not in exact:
            parts.append(describe_item(h, now))
    if qcol and not exact:
        for h in other[:2]:
            parts.append(
                f"I did see a {h['label']}{_attrs(h)}, which doesn't look {' or '.join(sorted(qcol))}: "
                + describe_item(h, now)
            )
    say = " ".join(x for x in (*parts, camera_note(mem), note) if x)
    return {"query": query, "items": hits, "say": say}


def _scene_text(mem: Memory, scene: dict, now: float) -> tuple[list[dict], str]:
    obs = mem.store.observations(scene["id"])
    if not obs:
        return obs, f"At {when(scene['ts'], now)} I saw no distinct objects."
    things = [f"a {o['label']} {o['position']}" for o in obs]
    return obs, f"At {when(scene['ts'], now)} I saw: " + "; ".join(things) + "."


def scene(mem: Memory, which: str = "now", fresh: bool = False) -> dict[str, Any]:
    """'now' (the latest reliable scene and what's believed there), 'before_obstruction', or a time."""
    which = (which or "now").strip().lower()
    note = ""
    if fresh and which in ("now", "current", "latest"):
        note = refresh(mem)
    st, now = mem.store, mem.clock()
    target = None
    if which in ("now", "current", "latest", "last_reliable"):
        target = st.scene(mem.baseline_id)
        if target:
            items = st.items(BELIEVED_PRESENT)
            lines = [describe_item(i, now) for i in items]
            seen = [i["label"] for i in items if i["status"] == "visible"]
            short = f"I see {_list(seen)}." if seen else "I don't see any distinct objects."
            gone = st.items(("missing",))
            say = (
                f"The last reliable view is from {when(target['ts'], now)}. "
                + " ".join(lines)
                + (f" No longer seen: {', '.join(g['label'] for g in gone)}." if gone else "")
            )
            return {
                "scene": target["id"],
                "items": items,
                "brief": short if len(seen) <= 6 else f"I see {len(seen)} things, including {_list(seen[:4])}.",
                "say": " ".join(x for x in (say, camera_note(mem), note) if x),
            }
    elif which.startswith("before"):
        ev = st.one(
            "SELECT ts FROM events WHERE kind='visibility' AND detail LIKE '%\"to\": \"unreadable\"%' "
            "AND detail NOT LIKE '%paused%' ORDER BY ts DESC LIMIT 1"
        )
        if ev:
            target = st.one(
                "SELECT * FROM scenes WHERE kind='model' AND ts<=? ORDER BY ts DESC LIMIT 1", (ev["ts"],)
            )
    else:
        t = parse_time(which, now)
        if t is not None:
            target = st.one(
                "SELECT * FROM scenes WHERE kind='model' AND ts<=? ORDER BY ts DESC LIMIT 1", (t,)
            )
    if note.startswith("I'm still looking"):
        # An older view would be the wrong answer: the look that's running is the one to wait for.
        say = "I'm still looking. Ask me again in a few seconds."
        return {"scene": None, "brief": say, "say": say}
    if not target:
        return {"scene": None, "say": "I have no reliable view from then."}
    obs, say = _scene_text(mem, target, now)
    return {"scene": target["id"], "observations": obs, "say": say}


def parse_time(text: str, now: float) -> float | None:
    """ISO times, 'today', 'yesterday', '3h' / '2d' ago, 'earlier' (an hour ago)."""
    text = text.strip().lower()
    n = datetime.fromtimestamp(now)
    midnight = n.replace(hour=0, minute=0, second=0, microsecond=0)
    if text in ("today",):
        return midnight.timestamp()
    if text in ("yesterday",):
        return (midnight - timedelta(days=1)).timestamp()
    if text in ("earlier",):
        return now - 3600
    if text[:-1].isdigit() and text[-1] in "mhd":
        return now - int(text[:-1]) * {"m": 60, "h": 3600, "d": 86400}[text[-1]]
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.timestamp() if dt.tzinfo else dt.replace(tzinfo=None).timestamp()


def history(mem: Memory, query: str = "", since: str = "", until: str = "") -> dict[str, Any]:
    st, now = mem.store, mem.clock()
    t0 = parse_time(since, now) if since else now - 7 * 86400
    t1 = parse_time(until, now) if until else now + 1
    if since.strip().lower() == "yesterday" and not until:
        t1 = parse_time("today", now)
    t0, t1 = t0 or 0.0, t1 or now + 1
    rows = st.q(
        "SELECT o.*, s.ts FROM observations o JOIN scenes s ON s.id=o.scene WHERE s.ts BETWEEN ? AND ? "
        "ORDER BY s.ts",
        (t0, t1),
    )
    q = words(query) - STOP
    qcol = colours(query, [])
    nouns = q - qcol
    hits: dict[int | None, dict] = {}
    for r in rows:
        have = words(r["label"]) | words(r.get("category") or "") | words(" ".join(r.get("attributes") or []))
        if nouns and not (nouns & have):
            continue
        rc = colours(r["label"], r.get("attributes") or [])
        if qcol and rc and not (qcol & rc):
            continue
        h = hits.setdefault(
            r["item"],
            {
                "label": r["label"],
                "first": r["ts"],
                "last": r["ts"],
                "times": 0,
                "positions": [],
                "colour_known": bool(rc),
            },
        )
        h["last"], h["times"] = r["ts"], h["times"] + 1
        if r["position"] not in h["positions"]:
            h["positions"].append(r["position"])
    span = f"between {when(t0, now)} and {when(min(t1, now), now)}"
    if not hits:
        what = f"a {query}" if query else "anything"
        say = f"I didn't see {what} {span}."
        n_scenes = st.one(
            "SELECT COUNT(*) AS n FROM scenes WHERE kind='model' AND ts BETWEEN ? AND ?", (t0, t1)
        )
        if not n_scenes or not n_scenes["n"]:
            say = f"I wasn't observing {span}, so I can't say."
        return {"items": [], "say": say}
    parts = []
    for h in list(hits.values())[:8]:
        extra = "" if h["colour_known"] or not qcol else " (I couldn't make out its colour)"
        parts.append(
            f"a {h['label']}{extra} {' then '.join(h['positions'][:3])}, first seen {when(h['first'], now)}"
            f", last {when(h['last'], now)}"
        )
    return {"items": list(hits.values()), "say": f"{span.capitalize()} I saw " + "; ".join(parts) + "."}


def describe_now(mem: Memory) -> str:
    return scene(mem, "now").get("brief", "")
