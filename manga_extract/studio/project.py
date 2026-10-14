"""Project folder model: pages, per-page recognition results, edits.

A project is a folder::

    <project>/
    ├─ project.json      name, target language, ordered page list
    ├─ pages/001.png …   originals, copied in at import time
    └─ data/001.json …   per page: text blocks with source / translation

Everything here is plain Python (no Qt), so it can be unit-tested on its own.
All writes are atomic (temp file + ``os.replace``) so a crash mid-save never
leaves a half-written JSON behind.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = 1
PROJECT_FILE = "project.json"
PAGES_DIR = "pages"
DATA_DIR = "data"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

# Page states, in the order they are reached.
STATUS_PENDING = "pending"        # imported, not recognised yet
STATUS_RECOGNIZED = "recognized"  # text extracted, nothing translated
STATUS_TRANSLATED = "translated"  # every non-empty block has a translation
STATUS_REVIEWED = "reviewed"      # every non-empty block is ticked as reviewed


class ProjectError(RuntimeError):
    """Raised for unusable project folders or bad arguments."""


def natural_key(name: str) -> List[Any]:
    """Sort key so ``2.png`` comes before ``10.png``."""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", name)]


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Per-page data
# ---------------------------------------------------------------------------

@dataclass
class BlockData:
    """One text block of a page, in reading order."""

    bbox: List[int]
    vertical: bool = False
    language: str = "unknown"
    lines: List[Dict[str, Any]] = field(default_factory=list)  # [{polygon, text}]
    source: str = ""            # authoritative original text (OCR or hand-edited)
    translation: str = ""
    reviewed: bool = False
    source_edited: bool = False  # True once the user changed the OCR text
    translation_edited: bool = False  # True once the user typed the translation by hand

    @property
    def xyxy(self) -> List[int]:
        """Alias so a block can be handed to ``typeset.render_translated_image``."""
        return self.bbox

    @property
    def has_text(self) -> bool:
        return bool(self.source.strip())

    def set_source(self, text: str) -> bool:
        """Replace the original text; returns True when it actually changed."""
        text = text.strip()
        if text == self.source:
            return False
        self.source = text
        self.source_edited = True
        return True

    def set_translation(self, text: str) -> bool:
        text = text.strip()
        if text == self.translation:
            return False
        self.translation = text
        return True

    def edit_translation(self, text: str) -> bool:
        """The user typed this translation: automatic translation must not overwrite it."""
        changed = self.set_translation(text)
        self.translation_edited = bool(self.translation)   # clearing it hands the block back
        return changed

    def apply_machine_translation(self, text: str) -> bool:
        changed = self.set_translation(text)
        self.translation_edited = False
        return changed

    def wants_machine_translation(self, force: bool = False) -> bool:
        """Reviewed and hand-edited blocks are protected unless the user asks explicitly."""
        if not self.has_text:
            return False
        return force or not (self.reviewed or self.translation_edited)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bbox": [int(v) for v in self.bbox],
            "vertical": bool(self.vertical),
            "language": self.language,
            "lines": self.lines,
            "source": self.source,
            "translation": self.translation,
            "reviewed": bool(self.reviewed),
            "source_edited": bool(self.source_edited),
            "translation_edited": bool(self.translation_edited),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BlockData":
        # Files written by ``PageResult.to_dict()`` carry "text", not "source".
        source = data.get("source")
        if source is None:
            source = data.get("text", "")
        return cls(
            bbox=[int(v) for v in data.get("bbox", [0, 0, 0, 0])],
            vertical=bool(data.get("vertical", False)),
            language=str(data.get("language", "unknown")),
            lines=list(data.get("lines", [])),
            source=str(source).strip(),
            translation=str(data.get("translation", "")).strip(),
            reviewed=bool(data.get("reviewed", False)),
            source_edited=bool(data.get("source_edited", False)),
            translation_edited=bool(data.get("translation_edited", False)),
        )


@dataclass
class PageData:
    """Recognition result plus edits for one page."""

    width: int
    height: int
    blocks: List[BlockData] = field(default_factory=list)

    @classmethod
    def from_result(cls, result: Any) -> "PageData":
        """Build from a ``pipeline.PageResult`` (duck-typed, no import needed)."""
        blocks = []
        for block in result.blocks:
            lines = [{"polygon": line.polygon, "text": line.text} for line in block.lines]
            blocks.append(BlockData(
                bbox=[int(v) for v in block.xyxy],
                vertical=bool(block.vertical),
                language=block.language,
                lines=lines,
                source=block.text.strip(),
            ))
        return cls(width=int(result.width), height=int(result.height), blocks=blocks)

    def translations(self) -> List[str]:
        """One translation per block (empty string when untranslated)."""
        return [block.translation for block in self.blocks]

    def machine_items(self, only_missing: bool = False) -> List[tuple]:
        """``(block index, source)`` pairs that automatic translation may fill in."""
        return [(index, block.source) for index, block in enumerate(self.blocks)
                if block.wants_machine_translation()
                and not (only_missing and block.translation)]

    def has_translation(self) -> bool:
        """True when at least one block has a translation (so an overlay is worth drawing)."""
        return any(block.translation for block in self.blocks)

    def fully_translated(self) -> bool:
        """True when every block that has text also has a translation."""
        return all(block.translation for block in self.blocks if block.has_text)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "width": self.width,
            "height": self.height,
            "blocks": [block.to_dict() for block in self.blocks],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PageData":
        return cls(
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            blocks=[BlockData.from_dict(b) for b in data.get("blocks", [])],
        )

    def status(self) -> str:
        texty = [block for block in self.blocks if block.has_text]
        if texty and all(block.reviewed for block in texty):
            return STATUS_REVIEWED
        if texty and all(block.translation for block in texty):
            return STATUS_TRANSLATED
        return STATUS_RECOGNIZED


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

@dataclass
class PageEntry:
    """One page in the project's ordered list."""

    id: str        # stable identifier, also the file stem ("001")
    file: str      # file name inside pages/
    origin: str = ""  # where it came from (original file name / URL / "截图")

    def to_dict(self) -> Dict[str, str]:
        return {"id": self.id, "file": self.file, "origin": self.origin}


class Project:
    """An opened project folder."""

    def __init__(self, folder: Path, name: str, target_lang: str,
                 pages: List[PageEntry], created: str) -> None:
        self.folder = folder
        self.name = name
        self.target_lang = target_lang
        self.pages = pages
        self.created = created

    # -- construction -------------------------------------------------------
    @classmethod
    def create(cls, folder: str | Path, name: Optional[str] = None,
               target_lang: str = "zh-CN") -> "Project":
        folder = Path(folder)
        if (folder / PROJECT_FILE).exists():
            raise ProjectError(f"该文件夹已经是一个项目：{folder}")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / PAGES_DIR).mkdir(exist_ok=True)
        (folder / DATA_DIR).mkdir(exist_ok=True)
        project = cls(folder, name or folder.name, target_lang, [],
                      time.strftime("%Y-%m-%dT%H:%M:%S"))
        project.save()
        return project

    @classmethod
    def open(cls, folder: str | Path) -> "Project":
        folder = Path(folder)
        meta = folder / PROJECT_FILE
        if not meta.is_file():
            raise ProjectError(f"不是项目文件夹（缺少 {PROJECT_FILE}）：{folder}")
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"项目文件损坏：{exc}") from exc
        pages = [PageEntry(id=str(p["id"]), file=str(p["file"]), origin=str(p.get("origin", "")))
                 for p in data.get("pages", []) if "id" in p and "file" in p]
        (folder / PAGES_DIR).mkdir(exist_ok=True)
        (folder / DATA_DIR).mkdir(exist_ok=True)
        return cls(folder, str(data.get("name", folder.name)),
                   str(data.get("target_lang", "zh-CN")), pages,
                   str(data.get("created", "")))

    def save(self) -> None:
        _atomic_write_json(self.folder / PROJECT_FILE, {
            "version": SCHEMA_VERSION,
            "name": self.name,
            "target_lang": self.target_lang,
            "created": self.created,
            "pages": [page.to_dict() for page in self.pages],
        })

    # -- lookup -------------------------------------------------------------
    def entry(self, page_id: str) -> PageEntry:
        for page in self.pages:
            if page.id == page_id:
                return page
        raise ProjectError(f"没有这一页：{page_id}")

    def index_of(self, page_id: str) -> int:
        for index, page in enumerate(self.pages):
            if page.id == page_id:
                return index
        raise ProjectError(f"没有这一页：{page_id}")

    def image_path(self, page_id: str) -> Path:
        return self.folder / PAGES_DIR / self.entry(page_id).file

    def data_path(self, page_id: str) -> Path:
        return self.folder / DATA_DIR / f"{page_id}.json"

    # -- importing pages ----------------------------------------------------
    def _next_id(self) -> str:
        numbers = [int(p.id) for p in self.pages if p.id.isdigit()]
        # Also look at files on disk so ids of removed pages are never reused
        # while a stray data file could still be around.
        for existing in (self.folder / DATA_DIR).glob("*.json"):
            if existing.stem.isdigit():
                numbers.append(int(existing.stem))
        number = max(numbers, default=0) + 1
        return f"{number:03d}"

    def add_image_files(self, paths: Iterable[str | Path]) -> List[PageEntry]:
        """Copy image files (or whole folders of them) into the project."""
        files: List[Path] = []
        for raw in list(paths):
            path = Path(raw)
            if path.is_dir():
                # Files inside a folder are ordered naturally (2 before 10).
                found = [p for p in path.iterdir()
                         if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
                files.extend(sorted(found, key=lambda p: natural_key(p.name)))
            elif path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                files.append(path)
        added = []
        for path in files:
            added.append(self._add_bytes(path.read_bytes(), path.suffix.lower(), path.name))
        if added:
            self.save()
        return added

    def add_image_bytes(self, data: bytes, suffix: str = ".png",
                        origin: str = "") -> PageEntry:
        """Add one in-memory image (clipboard, screenshot, web download)."""
        entry = self._add_bytes(data, suffix.lower(), origin)
        self.save()
        return entry

    def add_image_blobs(self, blobs: Iterable[tuple]) -> List[PageEntry]:
        """Add several in-memory images, ``(data, suffix, origin)`` each, in the given order.

        ``project.json`` is written once at the end, not once per image (a chapter fetched from
        the web can be a hundred pages).
        """
        added = [self._add_bytes(data, str(suffix).lower(), origin) for data, suffix, origin in blobs]
        if added:
            self.save()
        return added

    def _add_bytes(self, data: bytes, suffix: str, origin: str) -> PageEntry:
        if suffix not in IMAGE_SUFFIXES:
            suffix = ".png"
        page_id = self._next_id()
        file_name = f"{page_id}{suffix}"
        target = self.folder / PAGES_DIR / file_name
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        entry = PageEntry(id=page_id, file=file_name, origin=origin)
        self.pages.append(entry)
        return entry

    # -- page management ----------------------------------------------------
    def remove_page(self, page_id: str) -> None:
        entry = self.entry(page_id)
        self.pages.remove(entry)
        for path in (self.folder / PAGES_DIR / entry.file, self.data_path(page_id)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self.save()

    def reorder(self, page_ids: List[str]) -> None:
        """Apply a new page order (must contain exactly the current pages)."""
        if sorted(page_ids) != sorted(page.id for page in self.pages):
            raise ProjectError("页面顺序与当前项目不一致")
        by_id = {page.id: page for page in self.pages}
        self.pages = [by_id[page_id] for page_id in page_ids]
        self.save()

    def move_page(self, page_id: str, new_index: int) -> None:
        entry = self.entry(page_id)
        self.pages.remove(entry)
        new_index = max(0, min(new_index, len(self.pages)))
        self.pages.insert(new_index, entry)
        self.save()

    # -- per-page data ------------------------------------------------------
    def load_page(self, page_id: str) -> Optional[PageData]:
        """The saved result for a page, or None when it was never recognised."""
        path = self.data_path(page_id)
        if not path.is_file():
            return None
        try:
            return PageData.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def save_page(self, page_id: str, data: PageData) -> None:
        self.entry(page_id)  # validates the id
        _atomic_write_json(self.data_path(page_id), data.to_dict())

    def clear_page_data(self, page_id: str) -> None:
        try:
            self.data_path(page_id).unlink()
        except FileNotFoundError:
            pass

    def page_status(self, page_id: str) -> str:
        data = self.load_page(page_id)
        return STATUS_PENDING if data is None else data.status()

    def status_counts(self) -> Dict[str, int]:
        counts = {STATUS_PENDING: 0, STATUS_RECOGNIZED: 0,
                  STATUS_TRANSLATED: 0, STATUS_REVIEWED: 0}
        for page in self.pages:
            counts[self.page_status(page.id)] += 1
        return counts

    # -- export -------------------------------------------------------------
    def export_json(self, path: str | Path) -> int:
        """Write the whole project (pages, blocks, texts, flags) as JSON; returns the page count."""
        pages = []
        for index, page in enumerate(self.pages, start=1):
            data = self.load_page(page.id)
            pages.append({
                "index": index,
                "origin": page.origin,
                "recognized": data is not None,
                "width": data.width if data else 0,
                "height": data.height if data else 0,
                "blocks": [block.to_dict() for block in data.blocks] if data else [],
            })
        _atomic_write_json(Path(path), {
            "version": SCHEMA_VERSION, "name": self.name,
            "target_lang": self.target_lang, "pages": pages,
        })
        return len(pages)

    def export_text(self, path: str | Path, with_translation: bool = True) -> int:
        """Write all pages' text to a .txt file; returns the number of blocks."""
        chunks: List[str] = []
        total = 0
        for index, page in enumerate(self.pages, start=1):
            data = self.load_page(page.id)
            if data is None:
                continue
            lines = [f"=== 第 {index} 页（{page.origin or page.file}）==="]
            for block in data.blocks:
                if not block.has_text:
                    continue
                total += 1
                lines.append(block.source)
                if with_translation and block.translation:
                    lines.append(block.translation)
                lines.append("")
            chunks.append("\n".join(lines))
        Path(path).write_text("\n".join(chunks) + "\n", encoding="utf-8")
        return total
