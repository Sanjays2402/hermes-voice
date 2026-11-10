"""Pictures from Hermes Eye (the Echo Show's camera app). One JPEG per request; no video is kept."""

from __future__ import annotations

from dataclasses import dataclass
import json
import urllib.error
import urllib.request


@dataclass
class Shot:
    jpeg: bytes | None  # None when the camera gave no picture
    reason: str = ""  # why not: "covered", "blocked", "offline", "error"


class EyeCamera:
    def __init__(self, url: str, token: str, quality: int = 85, timeout: float = 6.0) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.quality = quality
        self.timeout = timeout

    def snapshot(self) -> Shot:
        req = urllib.request.Request(
            f"{self.url}/snapshot.jpg?q={self.quality}", headers={"Authorization": f"Bearer {self.token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return Shot(resp.read())
        except urllib.error.HTTPError as err:
            if err.code == 423:  # Hermes Eye: the shutter looks closed, or the mute button cut the camera
                try:
                    status = json.loads(err.read() or b"{}")
                except ValueError:
                    status = {}
                return Shot(None, "covered" if status.get("covered") else "blocked")
            return Shot(None, f"error {err.code}")
        except (urllib.error.URLError, TimeoutError, OSError):
            return Shot(None, "offline")
