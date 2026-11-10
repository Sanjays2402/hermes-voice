"""The running service: the camera loop, the API Hermes uses (this computer only, with a token), and the
display feed the Echo's screen reads (only the Echo's address may connect).
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import queue
import secrets
import signal
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import annotate, queries
from .camera import EyeCamera
from .config import Config
from .memory import Memory
from .store import Store
from .vision_model import CodexVision, LocalVision

log = logging.getLogger("vismem")


def api_token(cfg: Config) -> str:
    path = cfg.data_path / "api_token"
    if not path.exists():
        cfg.data_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(secrets.token_urlsafe(24))
        path.chmod(0o600)
    return path.read_text().strip()


class Display:
    """What the Echo's screen is showing from visual memory, and the clients listening for it."""

    def __init__(self, mem: Memory) -> None:
        self.mem = mem
        self.clients: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.current: dict[str, Any] | None = None
        self.image: bytes | None = None
        mem.listeners.append(self._from_memory)

    def _from_memory(self, event: dict[str, Any]) -> None:
        if event["type"] in ("status", "visibility"):
            self.send(self.status())

    def status(self) -> dict[str, Any]:
        on = self.mem.enabled
        cam = self.mem.live.visibility.value if on else "paused"
        return {"type": "vismem", "on": on, "mode": self.mem.mode, "camera": cam}

    def send(self, event: dict[str, Any]) -> None:
        with self.lock:
            for q in self.clients:
                q.put(event)

    def show(self, image: bytes, caption: str, kind: str) -> dict[str, Any]:
        key = secrets.token_urlsafe(9)
        self.image = image
        self.current = {
            "type": "vismem_show",
            "key": key,
            "caption": caption,
            "kind": kind,
            "ts": time.time(),
        }
        self.send(self.current)
        return {"shown": True, "listeners": len(self.clients)}

    def hide(self) -> None:
        self.current, self.image = None, None
        self.send({"type": "vismem_hide"})


def make_vision(cfg: Config, quick: bool) -> Any:
    careful, fast = cfg.models()
    model = fast if quick else careful
    timeout = 30.0 if quick else cfg.analysis_timeout
    if cfg.vision == "local":
        return LocalVision(cfg.local_url, model, cfg.local_api_key, timeout, cfg.local_keep_alive)
    return CodexVision(cfg.codex_bin, model, cfg.reasoning_effort, timeout)


class Service:
    def __init__(self, cfg: Config, vision: Any = None, camera: Any = None) -> None:
        self.cfg = cfg
        self.store = Store(cfg.data_path)
        self.vision = vision or make_vision(cfg, quick=False)
        quick = None if vision else make_vision(cfg, quick=True)
        self.mem = Memory(cfg, self.store, self.vision, quick_vision=quick)
        self._show_next_until = 0.0  # a question is waiting for a look: show its pictures when it is done
        self.mem.listeners.append(self._after_look)
        self.camera = camera or EyeCamera(cfg.eye_url, cfg.eye_token)
        self.display = Display(self.mem)
        self.token = api_token(cfg)
        self.stop = threading.Event()
        if self.mem.enabled and not cfg.resume_after_restart:
            self.mem.set_enabled(False)  # never resume watching silently after a restart

    def _after_look(self, event: dict[str, Any]) -> None:
        if event.get("type") == "status" and event.get("on"):
            # Turned on: get a local model loaded before the first question.
            for v in {id(x): x for x in (self.mem.quick_vision, self.vision)}.values():
                if hasattr(v, "warm"):
                    threading.Thread(target=v.warm, daemon=True, name="vismem-warm").start()
        if event.get("trigger") == "first" and event.get("scene"):
            # Show what it's watching: the desk outlined, the things on it boxed.
            img = annotate.watching(self.store, event["scene"], self.mem.workspace, self.cfg.mirror_for_user)
            if img:
                self.display.show(img, queries.scene(self.mem, "now").get("brief", ""), "scene")
            return
        if event.get("type") == "compared" and time.time() < self._show_next_until:
            self._show_next_until = 0.0
            self.show("comparison", ident=event.get("scene"))

    def changed(self, fresh: bool, show: bool) -> dict[str, Any]:
        """What changed, said briefly, with the pictures on the Echo (now, or when the look finishes)."""
        r = queries.what_changed(self.mem, fresh=fresh, wait=self.cfg.quick_wait or 120.0)
        if show and r.get("pending"):
            self._show_next_until = time.time() + 90
        elif show and r.get("scene"):
            s = self.show("comparison", ident=r["scene"])
            if s.get("say"):
                r["say"] += " " + s["say"]
        return r

    # The camera loop

    def observe(self) -> None:
        while not self.stop.is_set():
            started = time.monotonic()
            if self.mem.enabled:
                try:
                    self.mem.step(self.camera.snapshot())
                except Exception:
                    log.exception("observe step failed")
            self.stop.wait(max(0.05, self.cfg.sample_seconds - (time.monotonic() - started)))

    def housekeeping(self) -> None:
        while not self.stop.wait(3600):
            now = time.time()
            try:
                self.store.forget(before=now - self.cfg.retention_days * 86400, images_only=True)
                self.store.forget(before=now - self.cfg.history_days * 86400)
            except Exception:
                log.exception("retention failed")

    # What the API does

    def show(self, what: str, query: str = "", ident: int | None = None) -> dict[str, Any]:
        mirror = self.cfg.mirror_for_user
        if what == "comparison":
            sid = ident or int(self.store.get("last_comparison") or 0)
            img = annotate.comparison(self.store, sid, mirror) if sid else None
            caption = queries.what_changed(self.mem, fresh=False)["say"] if img else ""
        elif what == "item":
            hits = queries.find(self.mem, query)["items"] if query else []
            item = hits[0] if hits else (self.store.item(ident) if ident else None)
            img = annotate.item_evidence(self.store, item, mirror) if item else None
            caption = queries.describe_item(item, time.time()) if item else ""
        else:
            sid = ident or self.mem.baseline_id
            img = annotate.scene_picture(self.store, sid, mirror) if sid else None
            caption = "The last reliable view."
        if img is None:
            return {"shown": False, "say": "I have no stored picture for that."}
        out = self.display.show(img, caption, what)
        if not out["listeners"]:
            out["say"] = (
                "The Echo's screen isn't connected to visual memory right now, so I couldn't show it."
            )
        return out

    def forget(self, scope: str, days: float | None = None) -> dict[str, Any]:
        if scope == "all":
            self.display.hide()
            gone = self.store.forget()
            self.mem._load_baseline()
            return {**gone, "say": "Deleted all visual memory: every observation and picture."}
        if scope == "images":
            self.display.hide()
            gone = self.store.forget(images_only=True)
            self.mem._load_baseline()
            return {**gone, "say": "Deleted all stored pictures. The text observations are kept."}
        if scope == "older" and days is not None:
            gone = self.store.forget(before=time.time() - days * 86400)
            return {**gone, "say": f"Deleted visual memory older than {days:g} days."}
        if scope == "session":
            sid = self.mem.session_id
            if not sid:
                return {"say": "There's no session to delete."}
            was_on = self.mem.enabled
            self.mem.set_enabled(False)
            gone = self.store.forget_session(sid)
            self.mem._load_baseline()
            if was_on:
                self.mem.set_enabled(True)
            return {**gone, "say": "Deleted this session's observations and pictures."}
        return {"say": "Say what to delete: all, images, session, or older than some days."}

    def usage(self, days: float = 1.0) -> dict[str, Any]:
        rows = self.store.usage(time.time() - days * 86400)
        ok = [r for r in rows if r["ok"]]
        secs = sorted(r["seconds"] for r in ok)
        return {
            "calls": len(rows),
            "failures": len(rows) - len(ok),
            "by_trigger": {
                t: sum(1 for r in rows if r["trigger"] == t) for t in {r["trigger"] for r in rows}
            },
            "median_seconds": secs[len(secs) // 2] if secs else None,
            "input_tokens": sum(r["input_tokens"] or 0 for r in rows),
            "cached_tokens": sum(r["cached_tokens"] or 0 for r in rows),
            "output_tokens": sum(r["output_tokens"] or 0 for r in rows),
            "cost": "on the user's ChatGPT/Codex plan (counts toward its usage limits; no per-call charge)",
            "recent": rows[-10:],
        }

    # Servers

    def serve(self) -> None:
        api = ThreadingHTTPServer((self.cfg.api_host, self.cfg.api_port), _handler(self, display=False))
        disp = ThreadingHTTPServer(
            (self.cfg.display_host, self.cfg.display_port), _handler(self, display=True)
        )
        api.daemon_threads = disp.daemon_threads = True
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (api, disp)]
        threads += [
            threading.Thread(target=self.observe, daemon=True, name="vismem-observe"),
            threading.Thread(target=self.housekeeping, daemon=True),
        ]
        for t in threads:
            t.start()
        log.info(
            "visual memory: API http://%s:%s, display feed :%s (allowed: %s)",
            self.cfg.api_host,
            self.cfg.api_port,
            self.cfg.display_port,
            sorted(self.cfg.allowed_display_clients),
        )

        def _stop(*_: Any) -> None:
            self.stop.set()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        self.stop.wait()
        api.shutdown()
        disp.shutdown()
        self.store.close()


def _handler(svc: Service, display: bool) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("%s " + fmt, self.client_address[0], *args)

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if display:
                self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, code: int = 200) -> None:
            self._send(code, json.dumps(data, default=str).encode())

        def _allowed(self) -> bool:
            if display:
                return self.client_address[0] in svc.cfg.allowed_display_clients
            if self.path.startswith("/health"):
                return True
            auth = self.headers.get("Authorization", "")
            return secrets.compare_digest(auth, f"Bearer {svc.token}")

        def _body(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n))
            except ValueError:
                return {}

        def do_GET(self) -> None:
            if not self._allowed():
                self._json({"error": "not allowed"}, 403)
                return
            u = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(u.query).items()}
            try:
                if display:
                    self._display_get(u.path)
                else:
                    self._api_get(u.path, q)
            except BrokenPipeError:
                pass
            except Exception as err:
                log.exception("GET %s", u.path)
                self._json({"error": str(err)[:200]}, 500)

        def do_POST(self) -> None:
            if display or not self._allowed():
                self._json({"error": "not allowed"}, 403)
                return
            u = urlparse(self.path)
            b = self._body()
            try:
                if u.path == "/session":
                    self._json(svc.mem.set_enabled(bool(b.get("on")), b.get("mode"), b.get("area")))
                elif u.path == "/show":
                    self._json(svc.show(b.get("what", "comparison"), b.get("query", ""), b.get("id")))
                elif u.path == "/hide":
                    svc.display.hide()
                    self._json({"hidden": True})
                elif u.path == "/forget":
                    self._json(svc.forget(b.get("scope", ""), b.get("days")))
                else:
                    self._json({"error": "unknown"}, 404)
            except Exception as err:
                log.exception("POST %s", u.path)
                self._json({"error": str(err)[:200]}, 500)

        def _api_get(self, path: str, q: dict[str, str]) -> None:
            m = svc.mem
            if path == "/health":
                self._json({"ok": True})
            elif path == "/status":
                self._json(queries.status(m))
            elif path == "/changed":
                self._json(svc.changed(q.get("fresh", "1") == "1", q.get("show", "0") == "1"))
            elif path == "/find":
                self._json(queries.find(m, q.get("q", ""), fresh=q.get("fresh", "1") == "1"))
            elif path == "/scene":
                self._json(queries.scene(m, q.get("which", "now"), fresh=q.get("fresh", "1") == "1"))
            elif path == "/history":
                self._json(queries.history(m, q.get("q", ""), q.get("since", ""), q.get("until", "")))
            elif path == "/usage":
                self._json(svc.usage(float(q.get("days", "1"))))
            elif path.startswith("/evidence/") and path.endswith(".jpg"):
                kind, _, ident = path[len("/evidence/") : -4].partition("/")
                n = int(ident) if ident.isdigit() else 0
                mirror = svc.cfg.mirror_for_user
                img = (
                    annotate.comparison(svc.store, n, mirror)
                    if kind == "comparison"
                    else annotate.item_evidence(svc.store, svc.store.item(n) or {}, mirror)
                    if kind == "item"
                    else annotate.scene_picture(svc.store, n, mirror)
                    if kind == "scene"
                    else None
                )
                if img:
                    self._send(200, img, "image/jpeg")
                else:
                    self._json({"error": "no picture"}, 404)
            else:
                self._json({"error": "unknown"}, 404)

        def _display_get(self, path: str) -> None:
            d = svc.display
            if path == "/display/events":
                self._events()
            elif (
                path.startswith("/display/image/")
                and d.current
                and d.image
                and path == f"/display/image/{d.current['key']}.jpg"
            ):
                self._send(200, d.image, "image/jpeg")
            else:
                self._json({"error": "unknown"}, 404)

        def _events(self) -> None:
            d = svc.display
            q: queue.Queue = queue.Queue()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with d.lock:
                d.clients.append(q)
            try:
                first = [d.status()] + ([d.current] if d.current else [])
                for ev in first:
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
                while not svc.stop.is_set():
                    try:
                        ev = q.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with d.lock:
                    if q in d.clients:
                        d.clients.remove(q)
                self.close_connection = True

    return Handler
