"""Shared helpers for the hermes-voice-setup scripts (stdlib only).

Every script supports --dry-run: actions that change something are printed as
"[dry-run] would ..." and skipped. Secrets are never printed.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import socket
import ssl
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DRY_RUN = False


def set_dry_run(value: bool) -> None:
    global DRY_RUN
    DRY_RUN = value


def say(msg: str) -> None:
    print(msg, flush=True)


def act(desc: str) -> bool:
    """Announce a changing action. Returns False (skip) in dry-run mode."""
    if DRY_RUN:
        say(f"[dry-run] would {desc}")
        return False
    say(f"-> {desc}")
    return True


def die(msg: str, code: int = 1) -> None:
    say(f"ERROR: {msg}")
    sys.exit(code)


def http(method: str, url: str, *, token: str | None = None, body: Any = None,
         form: dict[str, str] | None = None, timeout: float = 30) -> tuple[int, Any]:
    """Small JSON HTTP client. Returns (status, parsed body or text). Never raises on HTTP errors."""
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as err:
        raw, status = err.read(), err.code
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return 0, None
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode("utf-8", "replace")


def read_text(path: str) -> str:
    """Read a small text file (stripped)."""
    with open(path) as f:
        return f.read().strip()


def write_secret(path: str, value: str) -> None:
    """Write a secret file with 0600 permissions (directory created 0700)."""
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value)
    os.chmod(path, 0o600)


def read_env_file(path: str) -> dict[str, str]:
    """Parse KEY=VALUE lines of a .env file (last one wins)."""
    out: dict[str, str] = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class WebSocket:
    """Minimal RFC 6455 client: text frames only, enough for Home Assistant's WS API."""

    def __init__(self, url: str, timeout: float = 30) -> None:
        u = urllib.parse.urlparse(url)
        port = u.port or (443 if u.scheme == "wss" else 80)
        sock = socket.create_connection((u.hostname, port), timeout=timeout)
        if u.scheme == "wss":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
        self.sock = sock
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            resp += chunk
        if b" 101 " not in resp.split(b"\r\n", 1)[0]:
            raise ConnectionError("websocket upgrade refused")
        self._buf = resp.split(b"\r\n\r\n", 1)[1]
        self._id = 0

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("websocket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_json(self, obj: dict[str, Any]) -> None:
        payload = json.dumps(obj).encode()
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(bytes(header) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv_json(self) -> dict[str, Any]:
        message = b""
        while True:
            b1, b2 = self._recv_exact(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            data = self._recv_exact(n)
            if opcode == 0x8:
                raise ConnectionError("websocket closed by server")
            if opcode in (0x1, 0x0):
                message += data
                if b1 & 0x80:
                    return json.loads(message)

    def call(self, msg_type: str, **fields: Any) -> dict[str, Any]:
        self._id += 1
        self.send_json({"id": self._id, "type": msg_type, **fields})
        while True:
            msg = self.recv_json()
            if msg.get("id") == self._id and msg.get("type") == "result":
                return msg

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()


def ha_ws(ha_url: str, token: str) -> WebSocket:
    """Open an authenticated Home Assistant websocket."""
    ws_url = ha_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/") + "/api/websocket"
    ws = WebSocket(ws_url)
    ws.recv_json()  # auth_required
    ws.send_json({"type": "auth", "access_token": token})
    if ws.recv_json().get("type") != "auth_ok":
        ws.close()
        raise PermissionError("Home Assistant rejected the token")
    return ws
