"""Export a project: the translated pages as images, plus text and JSON.

No Qt here. Pages are numbered by their position in the project (``001.png`` is the first
page), not by their internal id, so a reordered project exports in reading order.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .project import PageData, Project

FORMATS = ("png", "jpg")
JPEG_QUALITIES = (95, 90, 80)

# progress(done, total); both count pages.
Progress = Callable[[int, int], None]


@dataclass
class ExportOptions:
    folder: Path
    fmt: str = "png"                       # "png" | "jpg"
    quality: int = 92                      # JPEG only
    include_untranslated: bool = True      # pages without a translation are copied as they are

    def __post_init__(self) -> None:
        self.folder = Path(self.folder)
        if self.fmt not in FORMATS:
            raise ValueError(f"unknown format: {self.fmt}")
        self.quality = max(1, min(100, int(self.quality)))


@dataclass
class ExportReport:
    written: List[Path] = field(default_factory=list)
    skipped: int = 0                                   # untranslated pages left out on request
    failed: List[Tuple[int, str]] = field(default_factory=list)   # (page position, message)
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed and not self.cancelled


def file_name(position: int, total: int, fmt: str) -> str:
    """``001.png`` for page 1 of 24; the number is wide enough for ``total`` pages."""
    width = max(3, len(str(total)))
    return f"{position:0{width}d}.{fmt}"


def render_page(project: Project, page_id: str, data: PageData):
    """The page with its translations typeset over it (a PIL RGB image)."""
    from ..image_io import imread
    from ..typeset import render_translated_image

    path = project.image_path(page_id)
    bgr = imread(path)
    if bgr is None:
        raise OSError(f"无法读取图片：{path.name}")
    return render_translated_image(bgr, data.blocks, data.translations()).convert("RGB")


def original_page(project: Project, page_id: str):
    from PIL import Image

    with Image.open(project.image_path(page_id)) as image:
        return image.convert("RGB")


def save_image(image, path: Path, fmt: str, quality: int) -> None:
    if fmt == "jpg":
        image.save(path, "JPEG", quality=quality, optimize=True)
    else:
        image.save(path, "PNG", optimize=False)


def export_chapter(project: Project, options: ExportOptions,
                   progress: Optional[Progress] = None,
                   cancel: Optional[threading.Event] = None) -> ExportReport:
    """Write every page to ``options.folder``; one bad page never stops the others."""
    from ..typeset import TypesetError

    report = ExportReport()
    options.folder.mkdir(parents=True, exist_ok=True)
    pages = list(project.pages)
    total = len(pages)
    for position, entry in enumerate(pages, start=1):
        if cancel is not None and cancel.is_set():
            report.cancelled = True
            break
        try:
            data = project.load_page(entry.id)
            if data is not None and data.has_translation():
                image = render_page(project, entry.id, data)
            elif options.include_untranslated:
                image = original_page(project, entry.id)
            else:
                report.skipped += 1
                image = None
            if image is not None:
                target = options.folder / file_name(position, total, options.fmt)
                save_image(image, target, options.fmt, options.quality)
                report.written.append(target)
        except TypesetError as exc:
            report.failed.append((position, str(exc)))
        except Exception as exc:  # noqa: BLE001 - one page must not stop the export
            report.failed.append((position, f"{type(exc).__name__}: {exc}"))
        if progress is not None:
            progress(position, total)
    return report


def export_page_image(project: Project, page_id: str, path: Path, quality: int = 92) -> None:
    """One page with its translation (or as it is, if it has none) to ``path`` (.png / .jpg)."""
    data = project.load_page(page_id)
    if data is not None and data.has_translation():
        image = render_page(project, page_id, data)
    else:
        image = original_page(project, page_id)
    fmt = "jpg" if path.suffix.lower() in (".jpg", ".jpeg") else "png"
    path.parent.mkdir(parents=True, exist_ok=True)
    save_image(image, path, fmt, quality)
