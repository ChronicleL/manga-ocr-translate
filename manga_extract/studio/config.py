"""Per-machine settings of the studio window (theme, recent projects, size)."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List

MAX_RECENT = 8
THEMES = ("auto", "light", "dark")


def settings_root() -> Path:
    """Writable folder for config files (next to the exe when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    """Read-only bundled resources such as ``models/`` (the PyInstaller bundle when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


STUDIO_CONFIG_PATH = settings_root() / "studio_config.json"
ENGINE_CONFIG_PATH = settings_root() / "engine_config.json"   # shared with the older GUI
TRANSLATE_CONFIG_PATH = settings_root() / "translate_config.json"  # shared with the older GUI
DETECTOR_MODEL_PATH = resource_root() / "models" / "comic-text-detector.onnx"


@dataclass
class StudioConfig:
    theme: str = "auto"                       # auto | light | dark
    recent_projects: List[str] = field(default_factory=list)
    window_geometry: str = ""                 # base64 of QMainWindow.saveGeometry()
    splitter_sizes: List[int] = field(default_factory=list)
    auto_recognize: bool = True               # queue new pages for recognition on import
    show_boxes: bool = True                   # outline text blocks on the canvas
    last_url: str = ""                        # last chapter URL typed in the web-import dialog
    last_export_folder: str = ""              # where the last chapter export went

    def remember_project(self, folder: str | Path) -> None:
        folder = str(Path(folder))
        self.recent_projects = [folder] + [p for p in self.recent_projects if p != folder]
        del self.recent_projects[MAX_RECENT:]

    def forget_project(self, folder: str | Path) -> None:
        self.recent_projects = [p for p in self.recent_projects if p != str(Path(folder))]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> "StudioConfig":
        """Build from untrusted JSON, ignoring unknown keys and bad values."""
        config = cls()
        if not isinstance(data, dict):
            return config
        if data.get("theme") in THEMES:
            config.theme = data["theme"]
        recent = data.get("recent_projects")
        if isinstance(recent, list):
            config.recent_projects = [str(p) for p in recent if isinstance(p, str)][:MAX_RECENT]
        if isinstance(data.get("window_geometry"), str):
            config.window_geometry = data["window_geometry"]
        sizes = data.get("splitter_sizes")
        if isinstance(sizes, list) and all(isinstance(s, int) and s >= 0 for s in sizes):
            config.splitter_sizes = sizes
        for name in ("auto_recognize", "show_boxes"):
            if isinstance(data.get(name), bool):
                setattr(config, name, data[name])
        for name in ("last_url", "last_export_folder"):
            if isinstance(data.get(name), str):
                setattr(config, name, data[name])
        return config

    def save(self, path: str | Path = STUDIO_CONFIG_PATH) -> None:
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path = STUDIO_CONFIG_PATH) -> "StudioConfig":
        path = Path(path)
        if not path.is_file():
            return cls()
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return cls()
