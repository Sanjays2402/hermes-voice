"""The vision model, two ways:

- CodexVision: the user's Codex CLI (`codex exec`), images attached, JSON answer. It runs on the user's
  existing ChatGPT/Codex plan: no new account or API key. Pictures go to OpenAI.
- LocalVision: any OpenAI-compatible server with a vision model, e.g. Ollama (http://127.0.0.1:11434/v1)
  or LM Studio (http://127.0.0.1:1234/v1). Pictures stay on the user's network.

Each call sends the current snapshot (and, to compare, the previous one). Nothing else is sent.
"""

from __future__ import annotations

import base64
import contextvars
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Protocol
import urllib.error
import urllib.request

PROMPT = """You analyze still photos from a home camera for a visual memory. Do not run any commands.

Ignore people completely: never describe a person, a face, a body, or anything someone is wearing or
holding (clothes, a watch, jewellery).

List the inanimate objects you can see in the CURRENT image (the last image attached), one string each:
  "label|category|attributes|x0 y0 x1 y1|vis|same"
- label: what a person would call it, with its most telling attribute ("red screwdriver", "coffee mug").
  One thing made of parts is one object with one box (a snow globe with its base, a mug with its lid).
- category: the plain kind of thing ("screwdriver", "mug").
- attributes: only what is clearly visible (colours, material, text or markings), comma separated. If a
  colour is hard to make out, leave it out. Never guess.
- box: x0 y0 x1 y1 in 0-1000 coordinates of the CURRENT image (0 0 = top left).
- vis: f (fully visible), p (partly hidden or cut off), u (hard to make out).
- same: the id of the previously seen object it is (o12), or empty if it is not one of them.
List only loose things lying or standing on the work surface, that someone could pick up and take away.
Skip lamps, microphone stands, monitor arms and anything clamped, mounted, hanging or fixed in place; skip
walls, floor, ceiling, doors, closets, chairs and other furniture, and the table surface itself.
At most 25 objects; prefer distinct small things.

"workspace": the box "x0 y0 x1 y1" (0-1000, CURRENT image) of the work surface in view (a desk, table,
counter or shelf) including the things lying on it, or "" if there is none.
"""

COMPARE = """
A PREVIOUS image (the first image attached) showed these objects (id|label|attributes|box in that image):
{previous}

Match each current object to a previous one only if it is the same thing (it may have moved). For a
match, reuse the previous label unless it was clearly wrong.
For each previous object you cannot find in the current image, add a string to "missing":
  "id|gone" if its old spot is clearly visible and it is not there,
  "id|hidden" if something is now in front of or on top of that spot,
  "id|unsure" if you cannot tell.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "objects", "missing", "workspace"],
    "properties": {
        "summary": {"type": "string"},
        "workspace": {"type": "string"},
        "objects": {"type": "array", "items": {"type": "string"}},
        "missing": {"type": "array", "items": {"type": "string"}},
    },
}
SUMMARY = '"summary": one short sentence about the setting (no people).'


@dataclass
class Detection:
    label: str
    category: str
    attributes: list[str]
    box: list[int]
    vis: str = "f"
    same: int | None = None  # previous item id


@dataclass
class Analysis:
    objects: list[Detection]
    missing: dict[int, str] = field(default_factory=dict)  # previous item id -> gone/hidden/unsure
    summary: str = ""
    model: str = ""
    seconds: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    workspace: list[int] | None = None  # the desk/table in the current picture, 0-1000


class VisionError(Exception):
    pass


class Vision(Protocol):
    model: str

    def analyze(self, current: bytes, previous: bytes | None, previous_objects: list[dict]) -> Analysis: ...


def _box(text: str) -> list[int] | None:
    nums = [int(float(n)) for n in re.findall(r"-?\d+(?:\.\d+)?", text)]
    if len(nums) != 4:
        return None
    x0, y0, x1, y1 = (max(0, min(1000, n)) for n in nums)
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def parse(answer: dict) -> Analysis:
    objects = []
    for line in answer.get("objects", [])[:40]:
        parts = [p.strip() for p in str(line).split("|")]
        if len(parts) < 4 or not parts[0]:
            continue
        box = _box(parts[3])
        if box is None:
            continue
        attrs = [a.strip() for a in parts[2].split(",") if a.strip()] if len(parts) > 2 else []
        vis = parts[4][:1].lower() if len(parts) > 4 and parts[4] else "f"
        same = None
        if len(parts) > 5 and (m := re.search(r"o?(\d+)", parts[5])):
            same = int(m.group(1))
        objects.append(
            Detection(parts[0], parts[1] or parts[0], attrs, box, vis if vis in "fpu" else "u", same)
        )
    missing = {}
    for line in answer.get("missing", []):
        m = re.match(r"\s*o?(\d+)\s*\|\s*(gone|hidden|unsure)", str(line))
        if m:
            missing[int(m.group(1))] = m.group(2)
    result = Analysis(_one_per_thing(objects), missing, str(answer.get("summary", ""))[:300])
    result.workspace = _box(str(answer.get("workspace") or ""))
    return result


def _words(label: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", label.lower()))


def _tidy(label: str) -> str:
    """"green-capped-marker" -> "green capped marker" (said aloud). "van-shaped base" stays."""
    label = label.strip()
    return label.replace("-", " ").replace("_", " ") if " " not in label else label


def _one_per_thing(objects: list[Detection]) -> list[Detection]:
    """A part listed as its own thing ("snow globe" and "van-shaped snow globe base", boxes touching) is one
    thing: the shorter name, both boxes together. Two things that only share a word ("red marker",
    "blue marker") stay apart."""
    for d in objects:
        d.label, d.category = _tidy(d.label), _tidy(d.category)
    out: list[Detection] = []
    for d in sorted(objects, key=lambda d: len(_words(d.label))):
        for kept in out:
            short, long_ = _words(kept.label), _words(d.label)
            a, b = kept.box, d.box
            touching = a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]
            if short and short <= long_ and touching:
                kept.box = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                kept.same = kept.same if kept.same is not None else d.same
                break
        else:
            out.append(d)
    order = {id(d): n for n, d in enumerate(objects)}
    return sorted(out, key=lambda d: order[id(d)])


def describe_previous(previous_objects: list[dict]) -> str:
    rows = []
    for o in previous_objects:
        attrs = ", ".join(o.get("attributes") or [])
        box = " ".join(str(v) for v in o["box"])
        rows.append(f"o{o['id']}|{o['label']}|{attrs}|{box}")
    return "\n".join(rows)


def build_prompt(previous: bytes | None, previous_objects: list[dict]) -> str:
    prompt = PROMPT
    if previous is not None and previous_objects:
        prompt += COMPARE.format(previous=describe_previous(previous_objects))
    else:
        prompt += '\nThere is no previous image: "same" is always empty and "missing" is [].\n'
    return prompt + SUMMARY


def _json_in(text: str) -> dict:
    """The answer object, also when a local model wraps it in prose, a code fence or a <think> block."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except ValueError:
            pass
    raise VisionError("the model's answer was not JSON")


# Models being loaded, by (server, model): a look waits for the load before its own time limit starts.
_loading: dict[tuple[str, str], threading.Event] = {}
_loading_lock = threading.Lock()


class LocalVision:
    """An OpenAI-compatible chat endpoint (Ollama, LM Studio, llama.cpp server, vLLM...) with a vision
    model. The answer is asked for as JSON with the same schema as Codex's."""

    def __init__(
        self, base_url: str, model: str, api_key: str = "", timeout: float = 60.0, keep_alive: str = "30m"
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.keep_alive = keep_alive

    def warm(self) -> None:
        """Load the model now, and keep it loaded, so the first question doesn't wait for it (a cold load of
        a large model took 60 s on a Mac Studio; a loaded one answers in ~5 s). Ollama only: other servers
        load models their own way, and an error here is harmless."""
        key = (self.base_url, self.model)
        with _loading_lock:
            if key in _loading and not _loading[key].is_set():
                return  # already loading
            done = _loading[key] = threading.Event()
        try:
            self._load()
        finally:
            done.set()

    def wait_loaded(self, limit: float = 180.0) -> None:
        done = _loading.get((self.base_url, self.model))
        if done is not None:
            done.wait(limit)

    def _load(self) -> None:
        root = self.base_url.removesuffix("/v1")
        body = {"model": self.model, "keep_alive": self.keep_alive}
        req = urllib.request.Request(
            root + "/api/generate", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                r.read()
        except (urllib.error.URLError, OSError, ValueError):
            return
        # The first picture after loading is slow too (18 s vs 4 s): send a tiny one now.
        tiny = (
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGDEjJR0oOjM9PDkzODdASFxOQERXRTc4UG1RV19i"
            "Z2hnPk1xeXBkeFxlZ2P/2wBDARESEhgVGC8aGi9jQjhCY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2Nj"
            "Y2NjY2NjY2P/wAARCAAQABADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUF"
            "BAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVW"
            "V1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi"
            "4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAEC"
            "AxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVm"
            "Z2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq"
            "8vP09fb3+Pn6/9oADAMBAAIRAxEAPwAooooA/9k="
        )
        try:
            self._post(
                {
                    "model": self.model,
                    "max_tokens": 1,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "ok"},
                                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + tiny}},
                            ],
                        }
                    ],
                }
            )
        except VisionError:
            pass

    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")[:300]
            raise VisionError(f"{self.base_url} answered {err.code}: {detail}") from err
        except TimeoutError as err:
            raise VisionError(f"the model took longer than {self.timeout:.0f} s") from err
        except (urllib.error.URLError, OSError) as err:
            raise VisionError(f"can't reach {self.base_url} ({err})") from err

    def analyze(self, current: bytes, previous: bytes | None, previous_objects: list[dict]) -> Analysis:
        self.wait_loaded()  # turned on with the model unloaded: loading took 30-60 s, then a look takes ~5 s
        prompt = build_prompt(previous, previous_objects)
        prompt += "\nAnswer with only a JSON object: {\"summary\": ..., \"objects\": [...], \"missing\": [...], \"workspace\": ...}."
        content: list[dict] = [{"type": "text", "text": prompt}]
        images = ([previous] if previous is not None and previous_objects else []) + [current]
        for img in images:
            url = "data:image/jpeg;base64," + base64.b64encode(img).decode()
            content.append({"type": "image_url", "image_url": {"url": url}})
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "reasoning_effort": "none",  # thinking models: answer directly (ignored by others)
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "visual_memory", "strict": True, "schema": SCHEMA},
            },
        }
        started = time.monotonic()
        try:
            reply = self._post(body)
        except VisionError as err:
            if "400" not in str(err):
                raise
            # Some servers reject json_schema or reasoning_effort: retry with plain JSON mode.
            body.pop("reasoning_effort")
            body["response_format"] = {"type": "json_object"}
            reply = self._post(body)
        seconds = time.monotonic() - started
        try:
            text = reply["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as err:
            raise VisionError("the model sent no answer") from err
        result = parse(_json_in(text))
        u = reply.get("usage") or {}
        result.model, result.seconds = self.model, seconds
        result.usage = {
            "input_tokens": int(u.get("prompt_tokens") or 0),
            "output_tokens": int(u.get("completion_tokens") or 0),
        }
        return result


class HermesVision:
    """The model Hermes routes the plugin's own `visual_memory` task to (`ctx.llm`), used when visual memory
    runs as a Hermes plugin. Hermes picks the provider, holds the credentials and logs each call; the plugin
    never sees a key. By default the task follows the user's vision model, and `auxiliary.visual_memory`
    pins another one (a local Ollama model, for example)."""

    TASK = "visual_memory"

    def __init__(self, llm: object, timeout: float = 60.0) -> None:
        self.llm = llm
        self.timeout = timeout
        self.model = "hermes"
        # Looks run on the camera loop's and the API's threads. Hermes reads the active profile from
        # context variables, so each call runs in a copy of the context the plugin started in.
        self._context = contextvars.copy_context()

    def analyze(self, current: bytes, previous: bytes | None, previous_objects: list[dict]) -> Analysis:
        prompt = build_prompt(previous, previous_objects)
        prompt += "\nAnswer with only a JSON object: {\"summary\": ..., \"objects\": [...], \"missing\": [...], \"workspace\": ...}."
        images = ([previous] if previous is not None and previous_objects else []) + [current]
        blocks = [{"type": "image", "data": img, "mime_type": "image/jpeg"} for img in images]
        started = time.monotonic()
        try:
            result = self._context.copy().run(
                self.llm.complete_structured,  # type: ignore[attr-defined]
                instructions=prompt,
                input=blocks,
                json_mode=True,
                schema_name="visual_memory",
                temperature=0,
                timeout=self.timeout,
                task=self.TASK,
                purpose="visual-memory.look",
            )
        except Exception as err:  # provider errors, timeouts, no vision model configured
            raise VisionError(f"Hermes' vision model failed: {str(err)[:300]}") from err
        seconds = time.monotonic() - started
        answer = result.parsed if isinstance(getattr(result, "parsed", None), dict) else _json_in(result.text or "")
        analysis = parse(answer)
        usage = getattr(result, "usage", None)
        analysis.model = f"{getattr(result, 'provider', '') or 'hermes'}/{getattr(result, 'model', '') or '?'}"
        analysis.seconds = seconds
        analysis.usage = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }
        return analysis


class CodexVision:
    """`codex exec` with images: --ephemeral (no saved session), read-only sandbox, schema-checked answer."""

    def __init__(self, codex_bin: str, model: str, effort: str = "low", timeout: float = 60.0) -> None:
        self.codex_bin = codex_bin
        self.model = model
        self.effort = effort
        self.timeout = timeout

    def analyze(self, current: bytes, previous: bytes | None, previous_objects: list[dict]) -> Analysis:
        prompt = build_prompt(previous, previous_objects)
        with tempfile.TemporaryDirectory(prefix="vismem-") as tmp:
            d = Path(tmp)
            (d / "schema.json").write_text(json.dumps(SCHEMA))
            images = []
            if previous is not None and previous_objects:
                (d / "previous.jpg").write_bytes(previous)
                images.append("previous.jpg")
            (d / "current.jpg").write_bytes(current)
            images.append("current.jpg")
            cmd = [
                self.codex_bin,
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "-s",
                "read-only",
                "-m",
                self.model,
                "-c",
                f'model_reasoning_effort="{self.effort}"',
                "--output-schema",
                "schema.json",
                "--json",
                "-o",
                "answer.json",
            ]
            for img in images:
                cmd += ["-i", img]
            cmd.append("-")  # the prompt comes on stdin (-i takes several files)
            started = time.monotonic()
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=d,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    env={**os.environ, "NO_COLOR": "1"},
                )
            except subprocess.TimeoutExpired as err:
                raise VisionError(f"the model took longer than {self.timeout:.0f} s") from err
            except FileNotFoundError as err:
                raise VisionError(f"{self.codex_bin} not found") from err
            seconds = time.monotonic() - started
            usage: dict[str, int] = {}
            error = ""
            for line in proc.stdout.splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "turn.completed":
                    usage = {k: int(v) for k, v in (ev.get("usage") or {}).items() if isinstance(v, int)}
                elif ev.get("type") in ("turn.failed", "error"):
                    error = str(ev.get("error") or ev.get("message"))[:300]
            answer_file = d / "answer.json"
            if proc.returncode != 0 or not answer_file.exists():
                raise VisionError(error or proc.stderr.strip()[-300:] or f"codex exited {proc.returncode}")
            try:
                answer = json.loads(answer_file.read_text())
            except ValueError as err:
                raise VisionError("the model's answer was not JSON") from err
        result = parse(answer)
        result.model, result.seconds, result.usage = self.model, seconds, usage
        return result
