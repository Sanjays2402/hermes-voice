"""Settings, with defaults. Overrides come from a TOML file (see config.example.toml)."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import os
from pathlib import Path
import tomllib
from urllib.parse import urlparse

CONFIG_FILE = Path(os.environ.get("VISMEM_CONFIG", "~/.config/hermes-voice/visual-memory.toml")).expanduser()


@dataclass
class Config:
    # The camera: Hermes Eye on the Echo Show (docs/eye.md).
    eye_url: str = "http://192.168.1.10:28940"
    eye_token_file: str = "~/.config/hermes-voice/secrets/eye_token"
    camera_id: str = "echo-show"

    # Where memory lives. Nothing leaves this computer except the photos sent for analysis.
    data_dir: str = "~/.local/share/hermes-visual-memory"

    # The API Hermes uses: this computer only.
    api_host: str = "127.0.0.1"
    api_port: int = 28950
    # The feed the Echo's screen reads (evidence pictures, the "memory on" chip). Only the addresses in
    # display_allow may connect; empty means the Echo's own address (from eye_url) and this computer.
    display_host: str = "0.0.0.0"
    display_port: int = 28951
    display_allow: list[str] = field(default_factory=list)
    # The camera faces the user, so its left is the user's right. Positions are said, and pictures shown,
    # as the user sees them (mirrored, like the video-mode window).
    mirror_for_user: bool = True

    # Sampling and scene stability.
    sample_seconds: float = 1.0
    stable_seconds: float = 2.5

    # Which vision model looks at pictures: "codex" (the Codex CLI on the user's ChatGPT plan), "local"
    # (any OpenAI-compatible server: Ollama http://127.0.0.1:11434/v1, LM Studio http://127.0.0.1:1234/v1)
    # or "hermes" (only as a Hermes plugin: the model Hermes routes the plugin's vision task to).
    vision: str = "codex"
    # Whether the model is shown the previous picture and its object list to compare with. Codex compares
    # well; local models tend to copy the earlier list back ("still there" for a thing that was taken), so
    # by default they (and any model Hermes picks) describe only the current picture and the matching is
    # done here. "auto" = that.
    model_compares: str = "auto"
    local_url: str = "http://127.0.0.1:11434/v1"
    local_model: str = ""
    local_quick_model: str = ""  # empty: the same as local_model
    local_api_key_file: str = ""  # most local servers need none
    local_keep_alive: str = "30m"  # Ollama: keep the model loaded this long after turning on
    # Looks happen only when asked ("what's in front of you?", "what changed?") and once when visual memory
    # is turned on. While it runs, changed views are only saved on this computer. auto_analyze = true brings
    # back automatic looks whenever the scene changes (bounded by the limits below).
    auto_analyze: bool = False
    # With a local model looks cost nothing, so it also looks whenever the desk changes and settles: "what's in
    # front of you?" is then answered from that look, without waiting for one. "auto" = only with a local
    # model; true / false. At most one such look every settled_min_interval seconds.
    look_when_settled: str = "auto"
    settled_min_interval: float = 10.0
    # Uncovering the camera (a hand, the shutter) starts the comparison at once, so "what did I take away?"
    # is answered in time. One look per cover.
    look_after_uncover: bool = True
    # After turning on, the first look (which also finds the desk) waits at most this long for a still view.
    first_look_within: float = 4.0
    # A question within this many seconds of a finished look is answered from that look.
    fresh_for: float = 15.0

    codex_bin: str = "codex"
    model: str = "gpt-6.1-sol"
    reasoning_effort: str = "low"
    # A look someone is waiting for (a question) uses this quicker model, and the answer waits at most
    # quick_wait seconds for it: the Echo stops listening for an answer 15 s after the question. If the
    # look isn't done by then, Hermes says so and the answer goes on the screen. 0 = wait for the look.
    quick_model: str = "gpt-6-luna"
    quick_wait: float = 9.0
    analysis_timeout: float = 60.0
    analysis_width: int = 960
    min_auto_interval: float = 60.0
    auto_per_hour: int = 10
    auto_per_day: int = 30
    manual_per_hour: int = 20
    # Demo mode: comparisons after the camera was obstructed run at once (still counted).
    demo_min_interval: float = 5.0

    # Image quality (measured on the Show 5's OV9734; see docs).
    unreadable_contrast: float = 10.0
    unreadable_dark: float = 20.0
    unreadable_bright: float = 245.0
    uncertain_contrast: float = 12.0
    uncertain_sharpness: float = 25.0
    unreadable_sharpness: float = 6.0
    # Detail compared with this view's recent clear frames: below obstructed_detail the view may be
    # obstructed (a hand over the lens), below uncertain_detail it is uncertain. A dull view that stays for
    # dull_accept_samples becomes the new normal.
    obstructed_detail: float = 0.2
    uncertain_detail: float = 0.45
    dull_accept_samples: int = 60

    # Change detection (fractions of the frame).
    still_fraction: float = 0.004
    change_fraction: float = 0.004
    view_shift_fraction: float = 0.55

    # Retention: pictures are deleted after this many days, observations after history_days.
    retention_days: float = 14.0
    history_days: float = 90.0
    # After a restart, observing resumes only if this is true (otherwise it waits to be turned on).
    resume_after_restart: bool = False

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser()

    @property
    def eye_token(self) -> str:
        path = Path(self.eye_token_file).expanduser()
        return path.read_text().strip() if path.exists() else ""

    @property
    def local_api_key(self) -> str:
        if not self.local_api_key_file:
            return ""
        path = Path(self.local_api_key_file).expanduser()
        return path.read_text().strip() if path.exists() else ""

    @property
    def compares(self) -> bool:
        if self.model_compares == "auto":
            return self.vision == "codex"
        return self.model_compares in ("true", "yes", "1", True)

    @property
    def settled_looks(self) -> bool:
        if self.look_when_settled == "auto":
            return self.vision == "local"
        return self.look_when_settled in ("true", "yes", "1", True)

    def models(self) -> tuple[str, str]:
        """(careful model, quick model) for the chosen vision."""
        if self.vision == "local":
            return self.local_model, self.local_quick_model or self.local_model
        return self.model, self.quick_model

    @property
    def allowed_display_clients(self) -> set[str]:
        allow = set(self.display_allow) or {urlparse(self.eye_url).hostname or ""}
        return allow | {"127.0.0.1", "::1"}


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_FILE
    cfg = Config()
    if path.exists():
        data = tomllib.loads(path.read_text())
        known = {f.name for f in fields(Config)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"{path}: unknown settings {sorted(unknown)}")
        for key, value in data.items():
            setattr(cfg, key, value)
    if cfg.vision not in ("codex", "local"):
        raise ValueError(f"{path}: vision must be \"codex\" or \"local\" (\"hermes\" is for the plugin)")
    if cfg.vision == "local" and not cfg.local_model:
        raise ValueError(f"{path}: vision = \"local\" needs local_model (e.g. \"qwen2.5vl:7b\")")
    return cfg
