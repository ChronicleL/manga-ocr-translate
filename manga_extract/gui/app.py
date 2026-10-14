"""Floating, always-on-top window for extracting manga text from the screen.

Workflow: press **截取**, drag a rectangle over the picture shown in the
browser, and the recognised Japanese text is appended to the log below like a
chat message. Captures from the clipboard and image files are supported too.
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional

import cv2
import customtkinter as ctk
import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageGrab, ImageTk

from ..detector import ComicTextDetector, available_devices
from ..pipeline import ExtractedLine, MangaExtractor, PageResult
from ..recognizer import MangaOcrRecognizer
from ..runtime import DEVICE_LABELS, OCR_BATCH_CHOICES, EngineConfig
from ..translation import (
    TARGET_LANGUAGES,
    WEB_ENGINES,
    TranslationConfig,
    TranslationError,
)
from ..typeset import TypesetError, build_translations_for_blocks, render_translated_image
from ..visualize import draw_result
from .capture import enable_dpi_awareness, select_region

# Global CTk appearance (must be set before any CTk widget is created).
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


def _resource_root() -> Path:
    """Directory holding read-only bundled resources (``models/``)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


def _settings_root() -> Path:
    """Writable directory for the user's config files."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


RESOURCE_ROOT = _resource_root()
DEFAULT_DETECTOR = RESOURCE_ROOT / "models" / "comic-text-detector.onnx"
CONFIG_PATH = _settings_root() / "translate_config.json"
ENGINE_CONFIG_PATH = _settings_root() / "engine_config.json"

CARD_BG = "#ffffff"
CARD_BORDER = "#e2e8f0"
THUMB_SIZE = 132
FILE_TYPES = [
    ("图片", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"),
    ("所有文件", "*.*"),
]

AI_MODEL_PRESETS = [
    "deepseek-chat",
    "deepseek-reasoner",
    "gpt-4o-mini",
    "qwen-plus",
    "glm-4-flash",
    "moonshot-v1-8k",
    "llama3.1",
]

TEST_SENTENCE = "おい、この魔剣は重すぎるだろ…！とても振れる気がしないんだが。"

THINKING_HELP = (
    "很多推理模型（如 Qwen3 系列）默认会先「思考」再输出，翻译这种\n"
    "简单任务会白白多花几倍时间。\n\n"
    "勾选后请求里会带上 enable_thinking=false。同一个模型、同一句话\n"
    "实测：不勾 6.9 秒（思考 364 字），勾上 1.5 秒。\n\n"
    "注意：这是 Qwen / 通义等端点的参数。OpenAI 等不接受未知字段的\n"
    "端点可能报 400，遇到就取消勾选。"
)

TEMPERATURE_HELP = (
    "「随机性」即接口参数 temperature，决定模型输出的发散程度。\n\n"
    "・0：每次输出几乎完全一样，最死板、最听话\n"
    "・0.2 ~ 0.4：推荐翻译使用，措辞稳定、不跑偏（默认 0.3）\n"
    "・0.7 ~ 1.0：更灵活多变，同一句每次翻法都不同，可能自由发挥\n"
    "・大于 1：容易语无伦次；超出 0 ~ 2 会被服务端拒绝\n\n"
    "想做风格化改写可以调到 0.6 ~ 0.8。\n"
    "留空或填错不会报错，会自动回退到 0.3。"
)

DEVICE_HELP = (
    "推理后端。检测走 ONNX Runtime，OCR 走 PyTorch。\n\n"
    "・自动：只要装了带 GPU 支持的 onnxruntime 且显卡可用，就走 GPU，\n"
    "  否则退回 CPU。默认值。\n"
    "・仅 CPU：强制纯 CPU，兼容性最好。\n"
    "・CUDA / DirectML / OpenVINO：需要对应版本的 onnxruntime\n"
    "  （onnxruntime-gpu / onnxruntime-directml / onnxruntime-openvino），\n"
    "  没装的话下拉里不会出现。\n\n"
    "注意：本模型检测输入固定 1024×1024，纯 CPU 单页检测通常要十几秒，\n"
    "换成 GPU 是最有效的提速手段。"
)

PERF_HELP = (
    "纯 CPU 推理本身较慢（检测固定 1024×1024，最耗时）。\n"
    "・执行后端：装了 GPU 版 onnxruntime 且显卡可用时，选「自动」即可走 GPU。\n"
    "・CPU 线程：0 为自动；检测与 OCR 共用该线程数（填物理核心数通常最稳）。\n"
    "・OCR 批处理：把整页文本行凑成一批做一次前向，纯 CPU 上 4~8 一般最快。\n"
    "改动在点「保存」后会自动重新加载模型。"
)

# Shared kwargs for secondary (ghost/outlined) CTkButtons.
_BTN_SECONDARY: dict = dict(
    fg_color="transparent",
    border_width=1,
    border_color=("#c8c8c8", "#4a4a4a"),
    hover_color=("#ebebeb", "#3d3d3d"),
    text_color=("gray10", "gray90"),
    corner_radius=6,
)


def _parse_threads(value: str) -> int:
    try:
        return max(0, int(str(value).strip()))
    except ValueError:
        return 0


def _parse_batch(value: str) -> int:
    try:
        return max(1, int(str(value).strip()))
    except ValueError:
        return 1


# ---------------------------------------------------------------------------
# Tooltip
# ---------------------------------------------------------------------------

class HoverTip:
    """Show a small tooltip while the pointer rests on ``widget``."""

    def __init__(self, widget: tk.Widget, text: str, wraplength: int = 340) -> None:
        self.widget = widget
        self.text = text
        self.wraplength = wraplength
        self.tip: Optional[ctk.CTkToplevel] = None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def show(self, _event: object = None) -> None:
        if self.tip is not None or not self.widget.winfo_exists():
            return
        tip = ctk.CTkToplevel(self.widget)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        ctk.CTkLabel(
            tip, text=self.text, justify="left", wraplength=self.wraplength,
            fg_color="#1e293b", text_color="#f1f5f9", corner_radius=6,
            padx=10, pady=8, font=ctk.CTkFont(size=11),
        ).pack(padx=2, pady=2)
        tip.update_idletasks()
        x = max(0, self.widget.winfo_rootx() + self.widget.winfo_width()
                - tip.winfo_reqwidth())
        y = self.widget.winfo_rooty() - tip.winfo_reqheight() - 6
        if y < 0:
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip.geometry(f"+{x}+{y}")
        self.tip = tip

    def hide(self, _event: object = None) -> None:
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class MangaExtractApp:
    """The floating window and its capture/extract workflow."""

    def __init__(
        self,
        root: tk.Tk,
        model_path: str | Path = DEFAULT_DETECTOR,
        hf_endpoint: Optional[str] = None,
        topmost: bool = True,
        preload: bool = True,
    ) -> None:
        self.root = root
        self.model_path = Path(model_path)
        self.hf_endpoint = hf_endpoint or os.environ.get("HF_ENDPOINT")

        self.extractor: Optional[MangaExtractor] = None
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self._busy = False
        self._pending: Optional[Dict] = None
        self._cards: List[Dict] = []
        self._thumb_refs: List[ImageTk.PhotoImage] = []
        self._text_labels: list = []
        self._placeholder: Optional[ctk.CTkLabel] = None
        self._not_ready = "正在加载模型…"

        self.config = TranslationConfig.load(CONFIG_PATH)
        self.engine = EngineConfig.load(ENGINE_CONFIG_PATH)
        self._mode = tk.StringVar(value=self.config.mode)
        self._translating = False
        self._translating_cards: List[Dict] = []
        self._exporting_overlays = False

        root.title("漫画文字提取")
        root.geometry("480x680+80+80")
        root.minsize(380, 320)
        root.attributes("-topmost", bool(topmost))
        self._topmost = tk.BooleanVar(value=bool(topmost))

        self._build_ui()
        self._set_status("正在加载模型…" if preload else "模型未加载")
        if preload:
            threading.Thread(target=self._load_models, daemon=True).start()
        root.after(80, self._poll)

    # -- UI construction -----------------------------------------------------

    def _build_ui(self) -> None:
        outer = ctk.CTkFrame(self.root, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        # --- Row 1: Primary capture actions ---
        bar = ctk.CTkFrame(outer, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 6))

        self.btn_capture = ctk.CTkButton(
            bar, text="截取", width=76, state="disabled",
            command=self.on_capture, corner_radius=6,
        )
        self.btn_capture.pack(side="left")

        self.btn_clip = ctk.CTkButton(
            bar, text="剪贴板", width=92, state="disabled",
            command=self.on_clipboard, **_BTN_SECONDARY,
        )
        self.btn_clip.pack(side="left", padx=(6, 0))

        self.btn_open = ctk.CTkButton(
            bar, text="打开图片", width=108, state="disabled",
            command=self.on_open_file, **_BTN_SECONDARY,
        )
        self.btn_open.pack(side="left", padx=(6, 0))

        ctk.CTkCheckBox(
            bar, text="置顶", variable=self._topmost,
            command=self._toggle_topmost, width=60,
        ).pack(side="right")

        # --- Row 2: Secondary actions ---
        bar2 = ctk.CTkFrame(outer, fg_color="transparent")
        bar2.pack(fill="x", pady=(0, 6))

        for text, width, cmd in [
            ("复制全部", 90, self.on_copy_all),
            ("保存…", 75, self.on_save),
            ("导出叠图…", 100, self.on_export_overlays),
            ("清空", 65, self.on_clear),
            ("翻译设置", 90, self.open_settings),
        ]:
            ctk.CTkButton(bar2, text=text, width=width, command=cmd,
                          **_BTN_SECONDARY).pack(side="left", padx=(0, 6))

        # --- Row 3: Translation mode ---
        bar3 = ctk.CTkFrame(outer, fg_color="transparent")
        bar3.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(bar3, text="翻译：", fg_color="transparent").pack(side="left")
        self._web_radio = ctk.CTkRadioButton(
            bar3, text="普通", value="web", variable=self._mode,
            command=self._on_mode_change,
        )
        self._web_radio.pack(side="left", padx=(4, 0))
        ctk.CTkRadioButton(
            bar3, text="AI", value="ai", variable=self._mode,
            command=self._on_mode_change,
        ).pack(side="left", padx=(10, 0))
        ctk.CTkButton(
            bar3, text="翻译全部", width=92, command=self.on_translate_all,
            **_BTN_SECONDARY,
        ).pack(side="right")

        # --- Log: scrollable card list ---
        self.log = ctk.CTkScrollableFrame(outer, fg_color="#f8fafc")
        self.log.pack(fill="both", expand=True)
        self.log.bind("<Configure>", self._on_log_configure)

        self._add_placeholder()

        # --- Status bar (two zones) ---
        status_bar = ctk.CTkFrame(self.root, fg_color="#e2e8f0", corner_radius=0, height=28)
        status_bar.pack(fill="x", side="bottom")
        status_bar.pack_propagate(False)
        self.status = ctk.CTkLabel(
            status_bar, text="", anchor="w", fg_color="transparent",
            text_color="#374151", font=ctk.CTkFont(size=11),
        )
        self.status.pack(side="left", fill="x", expand=True, padx=10)
        self._backend_label = ctk.CTkLabel(
            status_bar, text="", anchor="e", fg_color="transparent",
            text_color="#6b7280", font=ctk.CTkFont(size=11),
        )
        self._backend_label.pack(side="right", padx=10)

        self._refresh_mode_labels()

        # Windows: CTkScrollableFrame does not automatically receive <MouseWheel>
        # events generated on child widgets.  Bind directly on the internal canvas
        # so any wheel event anywhere in the log area scrolls the list.
        self.root.bind_all("<MouseWheel>", self._on_global_mousewheel, add="+")

    def _on_global_mousewheel(self, event: tk.Event) -> None:
        """Route wheel events to the log scroll frame when the pointer is over it."""
        try:
            canvas = self.log._parent_canvas  # type: ignore[attr-defined]
        except AttributeError:
            return
        # Only scroll the log when the pointer is actually inside the log area;
        # leave other scrollable widgets (settings dialog comboboxes, textboxes)
        # free to handle their own wheel events.
        cx, cy = canvas.winfo_rootx(), canvas.winfo_rooty()
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        px, py = event.x_root, event.y_root
        if cx <= px <= cx + cw and cy <= py <= cy + ch:
            canvas.yview_scroll(int(-event.delta / 120), "units")

    def _add_placeholder(self) -> None:
        label = ctk.CTkLabel(
            self.log,
            justify="left",
            anchor="nw",
            text_color="#94a3b8",
            fg_color="transparent",
            text=(
                "点「截取」后拖动鼠标框选图片区域，\n"
                "识别结果会一条条显示在这里。\n\n"
                "也可以用「剪贴板」粘贴 (Win+Shift+S) 截的图，\n"
                "或用「打开图片」选择本地文件。\n\n"
                "每个结果里的「翻译」按钮可翻译成中文，\n"
                "译文以「原文一句 / 译文一句」排列。"
            ),
            font=ctk.CTkFont(size=12),
        )
        label.pack(fill="x", padx=14, pady=14)
        self._placeholder = label

    # -- enable / disable input buttons ------------------------------------

    def _set_capture_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.btn_capture.configure(state=state)
        for button in (self.btn_clip, self.btn_open):
            button.configure(state=state)

    # -- model loading -------------------------------------------------------

    def _load_models(self) -> None:
        try:
            if not self.model_path.is_file():
                raise FileNotFoundError(f"找不到检测模型：{self.model_path}")
            detector = ComicTextDetector(
                self.model_path, device=self.engine.device, threads=self.engine.threads
            )
            recognizer = MangaOcrRecognizer(
                hf_endpoint=self.hf_endpoint,
                device=self.engine.device,
                threads=self.engine.threads,
                batch_size=self.engine.ocr_batch,
            )
            self.extractor = MangaExtractor(detector=detector, recognizer=recognizer)
            backend = detector.providers[0].replace("ExecutionProvider", "")
            if backend == "CPU":
                backend += "（未启用 GPU 加速，检测会慢很多）"
            self.queue.put(("ready", f"检测 {backend}　·　OCR {recognizer.device}"))
        except Exception:
            self.queue.put(("fatal", traceback.format_exc()))

    def reload_models(self) -> None:
        """Re-create the detector/recognizer after a performance change."""
        self.extractor = None
        self._not_ready = "正在加载模型…"
        self._set_capture_enabled(False)
        self._set_status("正在加载模型…")
        threading.Thread(target=self._load_models, daemon=True).start()

    # -- event queue ---------------------------------------------------------

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "ready":
                    self._on_ready(payload or "")
                elif kind == "progress":
                    self._on_progress(*payload)
                elif kind == "result":
                    self._on_result(*payload)
                elif kind == "error":
                    self._on_error(payload)
                elif kind == "fatal":
                    self._on_fatal(payload)
                elif kind == "overlays_done":
                    self._on_overlays_done(*payload)
                elif kind == "translated":
                    self._on_translated(*payload)
                elif kind == "translation_done":
                    self._finish_translation(f"翻译完成：{payload} 句")
                elif kind == "translation_error":
                    self._finish_translation("翻译失败")
                    messagebox.showerror("翻译失败", payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _on_ready(self, info: str = "") -> None:
        self._set_capture_enabled(True)
        self._set_status("就绪　·　点「截取」框选图片区域")
        if info:
            self._backend_label.configure(text=info)

    def _on_progress(self, stage: str, done: int, total: int) -> None:
        if stage == "detect":
            if total == 0:
                self._set_status("检测完成，未发现文字")
            else:
                self._set_status(f"检测完成：{done} 块 / {total} 行　OCR 识别中…")
        elif stage == "ocr":
            self._set_status(f"OCR 识别中…　{done} / {total} 行")

    def _on_fatal(self, message: str) -> None:
        self._set_status("模型加载失败")
        messagebox.showerror("模型加载失败", message)

    # -- capture / input -----------------------------------------------------

    def _can_run(self) -> bool:
        if self.extractor is None:
            self._set_status(self._not_ready)
            return False
        if self._busy:
            self._set_status("上一张还在识别中…")
            return False
        return True

    def on_capture(self) -> None:
        if not self._can_run():
            return
        self.root.withdraw()
        self.root.update_idletasks()
        time.sleep(0.12)
        image: Optional[Image.Image] = None
        try:
            image = select_region(self.root)
        except Exception:
            self._set_status("截取失败")
            messagebox.showerror("截取失败", traceback.format_exc())
        finally:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", self._topmost.get())
        if image is None:
            self._set_status("已取消截取")
            return
        self._start_extract(image)

    def on_clipboard(self) -> None:
        if not self._can_run():
            return
        try:
            data = ImageGrab.grabclipboard()
        except Exception as exc:
            self._set_status(f"读取剪贴板失败：{exc}")
            return
        if isinstance(data, Image.Image):
            self._start_extract(data.convert("RGB"))
        elif isinstance(data, list) and data:
            self.on_open_path(data[0])
        else:
            self._set_status("剪贴板里没有图片")

    def on_open_file(self) -> None:
        if not self._can_run():
            return
        path = filedialog.askopenfilename(title="选择图片", filetypes=FILE_TYPES)
        if path:
            self.on_open_path(path)

    def on_open_path(self, path: str) -> None:
        try:
            image = Image.open(path).convert("RGB")
        except Exception as exc:
            self._set_status(f"无法打开图片：{exc}")
            return
        self._start_extract(image)

    # -- extraction ----------------------------------------------------------

    def _start_extract(self, image: Image.Image) -> None:
        self._busy = True
        self._set_status("识别中…")
        self._pending = self._add_pending_card(image)
        threading.Thread(target=self._run_extract, args=(image,), daemon=True).start()

    def _run_extract(self, image: Image.Image) -> None:
        extractor = self.extractor
        try:
            if extractor is None:
                raise RuntimeError("模型尚未加载完成")
            bgr = _pil_to_bgr(image)

            def on_progress(stage: str, done: int, total: int) -> None:
                self.queue.put(("progress", (stage, done, total)))

            result = extractor.extract_page(bgr, on_progress=on_progress)
            vis = draw_result(bgr, result)
            annotated = Image.fromarray(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB))
            self.queue.put(("result", (image, result, annotated)))
        except Exception:
            self.queue.put(("error", traceback.format_exc()))

    def _on_result(self, image: Image.Image, result: PageResult, annotated: Image.Image) -> None:
        self._busy = False
        card = self._pending
        self._pending = None
        if card is None:
            return

        text = result.text.strip()
        card["text"] = text
        card["annotated"] = annotated
        card["result"] = result
        card["image"] = image

        self._set_thumbnail(card, annotated)
        card["copy_btn"].configure(state="normal")
        card["translate_btn"].configure(state="normal")
        card["detail_btn"].configure(state="normal")
        self._refresh_card(card)
        self._cards.append(card)
        self._scroll_to_bottom()
        self._set_status(f"完成：{len(result.blocks)} 个文本块，{len(text)} 字")

    def _on_error(self, message: str) -> None:
        self._busy = False
        card = self._pending
        self._pending = None
        if card is not None:
            card["text_label"].configure(text="识别失败")
        self._set_status("识别失败")
        messagebox.showerror("识别失败", message)

    # -- log cards -----------------------------------------------------------

    def _add_pending_card(self, image: Image.Image) -> Dict:
        if self._placeholder is not None:
            self._placeholder.destroy()
            self._placeholder = None

        frame = ctk.CTkFrame(
            self.log, fg_color=CARD_BG,
            border_width=1, border_color=CARD_BORDER, corner_radius=8,
        )
        frame.pack(fill="x", padx=4, pady=(0, 8))

        # Thumbnail uses tk.Label for direct PhotoImage compatibility.
        thumb_label = tk.Label(frame, bg=CARD_BG)
        thumb_label.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(10, 8), pady=10)
        self._set_thumbnail_on(thumb_label, image, THUMB_SIZE)

        text_label = ctk.CTkLabel(
            frame, text="识别中…", anchor="nw", justify="left",
            fg_color="transparent", text_color="#374151",
            wraplength=200, font=ctk.CTkFont(size=12),
        )
        text_label.grid(row=0, column=1, sticky="nwe", pady=(10, 4), padx=(0, 8))
        self._text_labels.append(text_label)

        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.grid(row=1, column=1, sticky="sw", pady=(0, 10))
        card: Dict = {}

        copy_btn = ctk.CTkButton(
            buttons, text="复制", width=56, state="disabled",
            command=lambda: self._copy_text(self._card_display(card)),
            **_BTN_SECONDARY,
        )
        copy_btn.pack(side="left")

        translate_btn = ctk.CTkButton(
            buttons, text="翻译", width=68, state="disabled",
            command=lambda: self.on_translate_card(card),
            **_BTN_SECONDARY,
        )
        translate_btn.pack(side="left", padx=(6, 0))

        overlay_btn = ctk.CTkButton(
            buttons, text="叠图", width=56, state="disabled",
            command=lambda: self._show_overlay(card),
            **_BTN_SECONDARY,
        )
        overlay_btn.pack(side="left", padx=(6, 0))

        # toggle_btn is packed later only after translation exists.
        toggle_btn = ctk.CTkButton(
            buttons, text="显示原文", width=90,
            command=lambda: self._toggle_card_view(card),
            **_BTN_SECONDARY,
        )

        detail_btn = ctk.CTkButton(
            buttons, text="详情", width=56, state="disabled",
            command=lambda: self._show_detail(card),
            **_BTN_SECONDARY,
        )
        detail_btn.pack(side="left", padx=(6, 0))

        frame.columnconfigure(1, weight=1)
        card.update({
            "frame": frame,
            "thumb_label": thumb_label,
            "text_label": text_label,
            "buttons": buttons,
            "copy_btn": copy_btn,
            "translate_btn": translate_btn,
            "overlay_btn": overlay_btn,
            "toggle_btn": toggle_btn,
            "detail_btn": detail_btn,
            "text": "",
            "pairs": None,
            "block_pairs": None,
            "overlay": None,
            "show_original": False,
            "annotated": image,
            "result": None,
            "image": image,
        })
        self._scroll_to_bottom()
        return card

    def _set_thumbnail(self, card: Dict, image: Image.Image) -> None:
        self._set_thumbnail_on(card["thumb_label"], image, THUMB_SIZE)

    def _set_thumbnail_on(self, label: tk.Label, image: Image.Image, size: int) -> None:
        thumb = image.copy()
        thumb.thumbnail((size, size))
        photo = ImageTk.PhotoImage(thumb)
        self._thumb_refs.append(photo)
        label.configure(image=photo)

    def _show_detail(self, card: Dict) -> ctk.CTkToplevel:
        result: Optional[PageResult] = card.get("result")
        annotated: Image.Image = card.get("annotated", card["image"])
        win = ctk.CTkToplevel(self.root)
        win.title("识别详情")
        win.attributes("-topmost", True)
        win.geometry("760x680")

        actions = ctk.CTkFrame(win, fg_color="transparent")
        actions.pack(side="bottom", fill="x", pady=(0, 10))
        ctk.CTkButton(
            actions, text="编辑…", width=90,
            command=lambda: self._open_editor(card, on_saved=render),
            **_BTN_SECONDARY,
        ).pack(side="left", padx=10)
        ctk.CTkButton(actions, text="关闭", width=90, command=win.destroy,
                      **_BTN_SECONDARY).pack(side="right", padx=10)

        preview = annotated.copy()
        preview.thumbnail((720, 420))
        photo = ImageTk.PhotoImage(preview)
        img_label = tk.Label(win, image=photo, bg="#f8fafc")
        img_label.photo = photo  # type: ignore[attr-defined]
        img_label.pack(fill="x", padx=10, pady=10)

        text_box = ctk.CTkTextbox(win, wrap="word", height=280,
                                   font=ctk.CTkFont(size=12))
        text_box.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        def render() -> None:
            text_box.configure(state="normal")
            text_box.delete("1.0", "end")
            text_box.insert("1.0", _format_detail(card.get("result"), card.get("pairs")))
            text_box.configure(state="disabled")

        render()
        return win

    def _open_editor(self, card: Dict,
                     on_saved: Optional[Callable[[], None]] = None) -> Optional[ctk.CTkToplevel]:
        result: Optional[PageResult] = card.get("result")
        if result is None:
            self._set_status("还没有可编辑的结果")
            return None

        win = ctk.CTkToplevel(self.root)
        win.title("手动校对")
        win.attributes("-topmost", True)
        win.geometry("780x640")
        win.transient(self.root)

        actions = ctk.CTkFrame(win, fg_color="transparent")
        actions.pack(side="bottom", fill="x", pady=(10, 10))
        ctk.CTkButton(actions, text="保存修改", width=100,
                      command=lambda: save()).pack(side="right", padx=(0, 12))
        ctk.CTkButton(actions, text="取消", width=90, command=win.destroy,
                      **_BTN_SECONDARY).pack(side="right")

        ctk.CTkLabel(
            win, justify="left", text_color="#666", fg_color="transparent",
            wraplength=740, font=ctk.CTkFont(size=11),
            text="直接修改识别结果（原文）或译文。保存后卡片显示、复制、保存、译文叠图都会用修改后的内容；"
                 "改过原文的文本块会合并成一行。",
        ).pack(fill="x", padx=14, pady=(10, 6))

        area = ctk.CTkScrollableFrame(win, fg_color="transparent")
        area.pack(fill="both", expand=True, padx=12)

        targets = {index: target for index, _src, target in (card.get("block_pairs") or [])}
        rows: List[tuple] = []
        for index, block in enumerate(result.blocks):
            x1, y1, x2, y2 = block.xyxy
            direction = "竖排" if block.vertical else "横排"

            box = ctk.CTkFrame(area, fg_color="#f8fafc", border_width=1,
                                border_color="#e2e8f0", corner_radius=6)
            box.pack(fill="x", pady=(0, 6))

            header = ctk.CTkLabel(
                box, text=f"#{index}　{direction}　({x1},{y1})-({x2},{y2})",
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color="transparent", text_color="#374151", anchor="w",
            )
            header.pack(fill="x", padx=10, pady=(6, 2))

            inner = ctk.CTkFrame(box, fg_color="transparent")
            inner.pack(fill="x", padx=10, pady=(0, 8))

            ctk.CTkLabel(inner, text="原文", fg_color="transparent",
                         width=40, anchor="nw").grid(row=0, column=0, sticky="nw")
            h_src = max(40, _editor_height(block.text) * 20)
            src_box = ctk.CTkTextbox(inner, height=h_src, wrap="word",
                                      font=ctk.CTkFont(size=12))
            src_box.grid(row=0, column=1, sticky="we", padx=(8, 0))
            if block.text:
                src_box.insert("1.0", block.text)

            tgt_box = None
            if index in targets:
                ctk.CTkLabel(inner, text="译文", fg_color="transparent",
                             width=40, anchor="nw").grid(row=1, column=0,
                                                         sticky="nw", pady=(6, 0))
                h_tgt = max(40, _editor_height(targets[index]) * 20)
                tgt_box = ctk.CTkTextbox(inner, height=h_tgt, wrap="word",
                                          font=ctk.CTkFont(size=12))
                tgt_box.grid(row=1, column=1, sticky="we", padx=(8, 0), pady=(6, 0))
                tgt_box.insert("1.0", targets[index])

            inner.columnconfigure(1, weight=1)
            rows.append((index, block, src_box, tgt_box))

        def save() -> None:
            for _index, block, src_box, _tgt in rows:
                text = src_box.get("1.0", "end").strip()
                if text != block.text:
                    _rewrite_block_text(block, text)

            if card.get("block_pairs"):
                card["block_pairs"] = [
                    (index, block.text, tgt_box.get("1.0", "end").strip())
                    for index, block, _src, tgt_box in rows
                    if tgt_box is not None and block.text
                ]
                card["pairs"] = [(src, tgt) for _index, src, tgt in card["block_pairs"]]
                card["overlay"] = None

            card["text"] = result.text.strip()
            self._refresh_card(card)
            if on_saved is not None:
                on_saved()
            self._set_status("已保存手工修改")
            win.destroy()

        return win

    # -- translation ---------------------------------------------------------

    @staticmethod
    def _engine_label(code: str) -> str:
        for label, value in WEB_ENGINES.items():
            if value == code:
                return label
        return code

    @staticmethod
    def _engine_code(label: str) -> str:
        return WEB_ENGINES.get(label, "google")

    @staticmethod
    def _lang_label(code: str) -> str:
        for label, value in TARGET_LANGUAGES.items():
            if value == code:
                return label
        return code

    @staticmethod
    def _lang_code(label: str) -> str:
        return TARGET_LANGUAGES.get(label, "zh-CN")

    @staticmethod
    def _device_code(label: str) -> str:
        for code, text in DEVICE_LABELS.items():
            if text == label:
                return code
        return "auto"

    def _refresh_mode_labels(self) -> None:
        self._web_radio.configure(text=f"普通·{self._engine_label(self.config.web_engine)}")

    def _save_config(self) -> None:
        try:
            self.config.save(CONFIG_PATH)
        except OSError as exc:
            self._set_status(f"配置保存失败：{exc}")

    def _on_mode_change(self) -> None:
        self.config.mode = self._mode.get()
        self._save_config()
        self._refresh_mode_labels()
        self._set_status("翻译方式：AI 翻译" if self.config.mode == "ai" else
                         f"翻译方式：普通翻译（{self._engine_label(self.config.web_engine)}）")

    def _can_translate(self) -> bool:
        if self._busy or self._translating:
            self._set_status("正在忙，稍等一下…")
            return False
        try:
            self.config.build_translator()
        except TranslationError as exc:
            self._set_status(str(exc))
            messagebox.showwarning("翻译未配置", f"{exc}\n\n请点「翻译设置」进行配置。")
            return False
        return True

    def on_translate_card(self, card: Dict) -> None:
        if not self._can_translate():
            return
        self._start_translation([card])

    def on_translate_all(self) -> None:
        cards = [c for c in self._cards if c.get("result") is not None and c.get("text")]
        if not cards:
            self._set_status("还没有可翻译的结果")
            return
        if not self._can_translate():
            return
        self._start_translation(cards)

    def _start_translation(self, cards: List[Dict]) -> None:
        self._translating = True
        self._translating_cards = list(cards)
        label = "AI" if self.config.mode == "ai" else self._engine_label(self.config.web_engine)
        self._set_status(f"翻译中…（{label}）")
        for card in cards:
            card["translate_btn"].configure(text="翻译中…", state="disabled")
        threading.Thread(target=self._run_translation, args=(cards,), daemon=True).start()

    def _run_translation(self, cards: List[Dict]) -> None:
        try:
            translator = self.config.build_translator()
            jobs: List[tuple] = []
            sources: List[str] = []
            for card in cards:
                entries = [
                    (index, block.text.strip())
                    for index, block in enumerate(card["result"].blocks)
                    if block.text and block.text.strip()
                ]
                jobs.append((card, entries))
                sources.extend(text for _, text in entries)
            if not sources:
                self.queue.put(("translation_error", "这些结果里没有可翻译的文字"))
                return

            targets = translator.translate(sources)
            index = 0
            for card, entries in jobs:
                pairs: List[tuple] = []
                block_pairs: List[tuple] = []
                for block_index, source in entries:
                    target = targets[index] if index < len(targets) else ""
                    index += 1
                    pairs.append((source, target))
                    block_pairs.append((block_index, source, target))
                self.queue.put(("translated", (card, pairs, block_pairs)))
            self.queue.put(("translation_done", len(sources)))
        except TranslationError as exc:
            self.queue.put(("translation_error", str(exc)))
        except Exception:
            self.queue.put(("translation_error", traceback.format_exc()))

    def _on_translated(self, card: Dict, pairs: List[tuple], block_pairs: List[tuple]) -> None:
        if not card["frame"].winfo_exists():
            return
        card["pairs"] = pairs
        card["block_pairs"] = block_pairs
        card["overlay"] = None
        card["show_original"] = False
        card["translate_btn"].configure(text="重新翻译", state="normal")
        card["overlay_btn"].configure(state="normal")
        self._refresh_card(card)
        self._scroll_to_bottom()

    def _finish_translation(self, message: str) -> None:
        self._translating = False
        for card in self._translating_cards:
            if card["frame"].winfo_exists():
                card["translate_btn"].configure(
                    text="重新翻译" if card.get("pairs") else "翻译", state="normal")
        self._translating_cards = []
        self._set_status(message)

    def _card_display(self, card: Dict) -> str:
        pairs = card.get("pairs")
        if pairs and not card.get("show_original"):
            return "\n\n".join(f"{src}\n{tgt}" for src, tgt in pairs)
        return card.get("text", "")

    def _refresh_card(self, card: Dict) -> None:
        if not card["frame"].winfo_exists():
            return
        display = self._card_display(card) or "（没有识别到文字）"
        card["text_label"].configure(text=display)
        if card.get("pairs"):
            card["toggle_btn"].configure(
                text="显示原文" if not card.get("show_original") else "显示译文")
            if not card["toggle_btn"].winfo_ismapped():
                card["toggle_btn"].pack(
                    side="left", padx=(6, 0), before=card["detail_btn"])

    def _toggle_card_view(self, card: Dict) -> None:
        card["show_original"] = not card.get("show_original", False)
        self._refresh_card(card)

    # -- translated-image overlay -------------------------------------------

    def _show_overlay(self, card: Dict) -> Optional[ctk.CTkToplevel]:
        result = card.get("result")
        block_pairs = card.get("block_pairs") or []
        if result is None or not block_pairs:
            self._set_status("请先翻译，再生成叠图")
            return None
        try:
            if card.get("overlay") is None:
                translations = build_translations_for_blocks(result.blocks, block_pairs)
                preview = render_translated_image(
                    _pil_to_bgr(card["image"]), result.blocks, translations
                )
                card["overlay"] = preview
            preview = card["overlay"]
        except TypesetError as exc:
            self._set_status(str(exc))
            messagebox.showerror("叠图失败", str(exc))
            return None
        except Exception:
            self._set_status("叠图失败")
            messagebox.showerror("叠图失败", traceback.format_exc())
            return None
        return self._open_image_window("译文叠图预览", preview, default_name="translated.png")

    def _open_image_window(self, title: str, image: Image.Image, default_name: str) -> ctk.CTkToplevel:
        win = ctk.CTkToplevel(self.root)
        win.title(title)
        win.attributes("-topmost", True)

        iw, ih = image.size
        scale = min(960 / max(iw, 1), 700 / max(ih, 1), 1.0)
        win.geometry(f"{max(440, int(iw * scale) + 20)}x{max(340, int(ih * scale) + 56)}")

        img_area = ctk.CTkFrame(win, fg_color="#2b2b2b", corner_radius=0)
        img_area.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        img_label = tk.Label(img_area, bg="#2b2b2b")
        img_label.pack(fill="both", expand=True)

        _resize_job: List[Optional[str]] = [None]

        def _do_resize() -> None:
            if not img_area.winfo_exists():
                return
            w, h = img_area.winfo_width(), img_area.winfo_height()
            if w < 20 or h < 20:
                return
            display = image.copy()
            display.thumbnail((w, h), Image.LANCZOS)
            photo = ImageTk.PhotoImage(display)
            img_label.configure(image=photo)
            img_label.photo = photo  # type: ignore[attr-defined]
            _resize_job[0] = None

        def _on_resize(_event: object = None) -> None:
            if _resize_job[0]:
                win.after_cancel(_resize_job[0])
            _resize_job[0] = win.after(80, _do_resize)

        img_area.bind("<Configure>", _on_resize)

        def save() -> None:
            path = filedialog.asksaveasfilename(
                parent=win, title="保存图片", defaultextension=".png", initialfile=default_name,
                filetypes=[("PNG 图片", "*.png"), ("JPEG 图片", "*.jpg"), ("所有文件", "*.*")],
            )
            if not path:
                return
            try:
                image.convert("RGB").save(path)
            except OSError as exc:
                messagebox.showerror("保存失败", str(exc), parent=win)
                return
            self._set_status(f"已保存到 {path}")

        actions = ctk.CTkFrame(win, fg_color="transparent")
        actions.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(actions, text="保存图片", width=100, command=save).pack(side="left", padx=8)
        ctk.CTkButton(actions, text="关闭", width=90, command=win.destroy,
                      **_BTN_SECONDARY).pack(side="right", padx=8)
        return win

    # -- settings dialog -----------------------------------------------------

    def open_settings(self) -> None:
        self.config.ensure_profiles()

        dlg = ctk.CTkToplevel(self.root)
        dlg.title("翻译设置")
        dlg.attributes("-topmost", True)
        dlg.geometry("640x700")
        dlg.minsize(560, 440)
        dlg.transient(self.root)

        # Fixed action bar at the bottom.
        actions = ctk.CTkFrame(dlg, fg_color="transparent")
        actions.pack(fill="x", side="bottom", padx=12, pady=12)

        # Scrollable content area.
        body = ctk.CTkScrollableFrame(dlg, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(12, 0))

        # ── helper: labelled section frame ───────────────────────────────
        def section(parent: ctk.CTkScrollableFrame, title: str) -> ctk.CTkFrame:
            card = ctk.CTkFrame(parent, fg_color="#f8fafc",
                                border_width=1, border_color="#e2e8f0", corner_radius=8)
            card.pack(fill="x", pady=(0, 10))
            ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=13, weight="bold"),
                         fg_color="transparent", text_color="#1e293b",
                         anchor="w").pack(fill="x", padx=12, pady=(10, 4))
            sep = ctk.CTkFrame(card, height=1, fg_color="#e2e8f0", corner_radius=0)
            sep.pack(fill="x", padx=12, pady=(0, 8))
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=12, pady=(0, 12))
            return inner

        def row_label(parent: ctk.CTkFrame, text: str, r: int) -> None:
            ctk.CTkLabel(parent, text=text, fg_color="transparent",
                         text_color="#374151", anchor="w", width=80).grid(
                row=r, column=0, sticky="w", pady=(0, 8))

        # ── 普通翻译 ─────────────────────────────────────────────────────
        web_inner = section(body, "普通翻译（网页通道）")
        web_inner.columnconfigure(1, weight=1)

        row_label(web_inner, "翻译引擎", 0)
        engine_var = tk.StringVar(value=self._engine_label(self.config.web_engine))
        ctk.CTkComboBox(web_inner, variable=engine_var,
                        values=list(WEB_ENGINES.keys()),
                        state="readonly", width=200).grid(
            row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 8))

        row_label(web_inner, "目标语言", 1)
        lang_var = tk.StringVar(value=self._lang_label(self.config.target_lang))
        ctk.CTkComboBox(web_inner, variable=lang_var,
                        values=list(TARGET_LANGUAGES.keys()),
                        state="readonly", width=200).grid(
            row=1, column=1, sticky="w", padx=(8, 0), pady=(0, 8))

        web_result = tk.StringVar(value="")
        ctk.CTkButton(web_inner, text="测试机翻", width=100,
                      command=lambda: run_test("web")).grid(
            row=0, column=2, rowspan=2, padx=(14, 0))
        ctk.CTkLabel(web_inner, textvariable=web_result, text_color="#16a34a",
                     fg_color="transparent", wraplength=500,
                     justify="left").grid(row=2, column=0, columnspan=3,
                                          sticky="w", pady=(0, 4))

        # ── AI 翻译 ──────────────────────────────────────────────────────
        ai_inner = section(body, "AI 翻译（OpenAI 兼容接口）")
        ai_inner.columnconfigure(1, weight=1)

        profile_var = tk.StringVar()
        name_var = tk.StringVar(value=self.config.ai.name)
        base_var = tk.StringVar(value=self.config.ai.base_url)
        key_var = tk.StringVar(value=self.config.ai.api_key)
        model_var = tk.StringVar(value=self.config.ai.model)
        temp_var = tk.StringVar(value=str(self.config.ai.temperature))
        think_var = tk.BooleanVar(value=self.config.ai.disable_thinking)
        ai_result = tk.StringVar(value="")
        state = {"index": 0}

        row_label(ai_inner, "接口配置", 0)
        profile_box = ctk.CTkComboBox(ai_inner, variable=profile_var,
                                       state="readonly", width=220)
        profile_box.grid(row=0, column=1, sticky="we", padx=(8, 0), pady=(0, 8))

        prof_btns = ctk.CTkFrame(ai_inner, fg_color="transparent")
        prof_btns.grid(row=0, column=2, sticky="w", padx=(8, 0), pady=(0, 8))
        ctk.CTkButton(prof_btns, text="新增", width=60,
                      command=lambda: add_profile(), **_BTN_SECONDARY).pack(side="left")
        ctk.CTkButton(prof_btns, text="删除", width=60,
                      command=lambda: delete_profile(),
                      **_BTN_SECONDARY).pack(side="left", padx=(6, 0))

        for r, (lbl, var, is_key) in enumerate([
            ("名称", name_var, False),
            ("接口地址", base_var, False),
            ("API Key", key_var, True),
        ], start=1):
            row_label(ai_inner, lbl, r)
            ent = ctk.CTkEntry(ai_inner, textvariable=var, width=280,
                                show="•" if is_key else "")
            ent.grid(row=r, column=1, sticky="we", padx=(8, 0), pady=(0, 8))
            if is_key:
                show_key = tk.BooleanVar(value=False)
                ctk.CTkCheckBox(ai_inner, text="显示", variable=show_key, width=70,
                                command=lambda e=ent, v=show_key: e.configure(
                                    show="" if v.get() else "•")).grid(
                    row=r, column=2, sticky="w", padx=(8, 0), pady=(0, 8))

        row_label(ai_inner, "模型", 4)
        ctk.CTkComboBox(ai_inner, variable=model_var, values=AI_MODEL_PRESETS,
                        width=280).grid(row=4, column=1, sticky="we",
                                        padx=(8, 0), pady=(0, 8))

        row_label(ai_inner, "随机性", 5)
        temp_row = ctk.CTkFrame(ai_inner, fg_color="transparent")
        temp_row.grid(row=5, column=1, columnspan=2, sticky="w",
                      padx=(8, 0), pady=(0, 8))
        ctk.CTkEntry(temp_row, textvariable=temp_var, width=90).pack(side="left")
        think_check = ctk.CTkCheckBox(temp_row, text="关闭思考模式（更快）",
                                       variable=think_var)
        think_check.pack(side="left", padx=(14, 0))
        HoverTip(think_check, THINKING_HELP)
        temp_help = ctk.CTkButton(temp_row, text="?", width=30,
                                   cursor="question_arrow", **_BTN_SECONDARY)
        temp_help.pack(side="left", padx=(8, 0))
        temp_help.configure(command=HoverTip(temp_help, TEMPERATURE_HELP).show)

        row_label(ai_inner, "提示词", 6)
        prompt_text = ctk.CTkTextbox(ai_inner, height=80, wrap="word",
                                      font=ctk.CTkFont(size=12))
        prompt_text.grid(row=6, column=1, sticky="we", padx=(8, 0), pady=(0, 4))
        if self.config.ai.prompt:
            prompt_text.insert("1.0", self.config.ai.prompt)
        ctk.CTkLabel(ai_inner, text="追加给 AI 的风格要求，例如「用轻松吐槽的语气，保留拟声词」",
                     text_color="#94a3b8", fg_color="transparent",
                     anchor="w").grid(row=7, column=1, sticky="w", padx=(8, 0))
        ctk.CTkButton(ai_inner, text="测试 AI", width=100,
                      command=lambda: run_test("ai")).grid(
            row=8, column=1, sticky="w", pady=(10, 0))
        ctk.CTkLabel(ai_inner, textvariable=ai_result, text_color="#16a34a",
                     fg_color="transparent", wraplength=500,
                     justify="left").grid(row=9, column=0, columnspan=3,
                                          sticky="w", pady=(8, 0))

        # ── 性能 ─────────────────────────────────────────────────────────
        perf_inner = section(body, "性能（修改后自动重新加载模型）")
        perf_inner.columnconfigure(1, weight=1)

        devices = available_devices()
        device_var = tk.StringVar(value=DEVICE_LABELS.get(self.engine.device,
                                                           self.engine.device))
        row_label(perf_inner, "执行后端", 0)
        dev_row = ctk.CTkFrame(perf_inner, fg_color="transparent")
        dev_row.grid(row=0, column=1, columnspan=2, sticky="w",
                     padx=(8, 0), pady=(0, 8))
        ctk.CTkComboBox(dev_row, variable=device_var,
                        values=[DEVICE_LABELS.get(n, n) for n in devices],
                        state="readonly", width=220).pack(side="left")
        dev_help = ctk.CTkButton(dev_row, text="?", width=30, cursor="question_arrow",
                                  **_BTN_SECONDARY)
        dev_help.pack(side="left", padx=(8, 0))
        dev_help.configure(command=HoverTip(dev_help, DEVICE_HELP).show)

        threads_var = tk.StringVar(value=str(self.engine.threads))
        row_label(perf_inner, "CPU 线程", 1)
        ctk.CTkEntry(perf_inner, textvariable=threads_var, width=90).grid(
            row=1, column=1, sticky="w", padx=(8, 0), pady=(0, 8))

        batch_var = tk.StringVar(value=str(self.engine.ocr_batch))
        row_label(perf_inner, "OCR 批处理", 2)
        ctk.CTkComboBox(perf_inner, variable=batch_var,
                        values=[str(n) for n in OCR_BATCH_CHOICES],
                        state="readonly", width=90).grid(
            row=2, column=1, sticky="w", padx=(8, 0), pady=(0, 8))

        ctk.CTkLabel(perf_inner, text=PERF_HELP, text_color="#94a3b8",
                     fg_color="transparent", wraplength=520,
                     justify="left", anchor="w").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(0, 4))

        # ── profile helpers ───────────────────────────────────────────────
        def temperature() -> float:
            try:
                return float(temp_var.get())
            except ValueError:
                return 0.3

        def refresh_profile_box(select: int) -> None:
            names = [p.name or f"配置 {i + 1}"
                     for i, p in enumerate(self.config.ai_profiles)]
            profile_box.configure(values=names)
            profile_box.set(names[select])

        def load_profile(index: int) -> None:
            profile = self.config.ai_profiles[index]
            state["index"] = index
            self.config.ai = profile
            name_var.set(profile.name)
            base_var.set(profile.base_url)
            key_var.set(profile.api_key)
            model_var.set(profile.model)
            temp_var.set(str(profile.temperature))
            think_var.set(profile.disable_thinking)
            prompt_text.delete("1.0", "end")
            if profile.prompt:
                prompt_text.insert("1.0", profile.prompt)
            refresh_profile_box(index)

        def store_profile() -> None:
            index = state["index"]
            profile = self.config.ai_profiles[index]
            profile.name = name_var.get().strip() or f"配置 {index + 1}"
            profile.base_url = base_var.get().strip()
            profile.api_key = key_var.get().strip()
            profile.model = model_var.get().strip()
            profile.temperature = temperature()
            profile.prompt = prompt_text.get("1.0", "end").strip()
            profile.disable_thinking = bool(think_var.get())
            self.config.ai = profile

        def on_profile_selected(_event: object = None) -> None:
            target_name = profile_box.get()
            names = [p.name or f"配置 {i + 1}"
                     for i, p in enumerate(self.config.ai_profiles)]
            try:
                target = names.index(target_name)
            except ValueError:
                return
            if target == state["index"]:
                return
            store_profile()
            load_profile(target)

        profile_box.configure(command=on_profile_selected)

        def add_profile() -> None:
            store_profile()
            current = self.config.ai_profiles[state["index"]]
            self.config.ai_profiles.append(
                replace(current, name=self.config.unique_profile_name(), api_key="")
            )
            load_profile(len(self.config.ai_profiles) - 1)

        def delete_profile() -> None:
            if len(self.config.ai_profiles) <= 1:
                messagebox.showinfo("无法删除", "至少保留一套 API 配置。", parent=dlg)
                return
            index = state["index"]
            name = self.config.ai_profiles[index].name
            if not messagebox.askyesno("删除配置", f"确定删除「{name}」吗？", parent=dlg):
                return
            del self.config.ai_profiles[index]
            load_profile(min(index, len(self.config.ai_profiles) - 1))

        load_profile(next(
            (i for i, p in enumerate(self.config.ai_profiles) if p is self.config.ai), 0
        ))

        # ── test buttons ─────────────────────────────────────────────────
        test_vars: Dict[str, tk.StringVar] = {"web": web_result, "ai": ai_result}
        results: Dict[str, str] = {}
        active: set = set()
        polling = {"running": False}

        def poll_tests() -> None:
            for channel in list(results):
                test_vars[channel].set(results.pop(channel))
                active.discard(channel)
            if not dlg.winfo_exists() or not active:
                polling["running"] = False
                return
            dlg.after(150, poll_tests)

        def run_test(channel: str) -> None:
            if channel in active:
                return
            store_profile()
            active.add(channel)
            test_vars[channel].set("测试中…")
            if channel == "web":
                cfg = TranslationConfig(
                    mode="web",
                    web_engine=self._engine_code(engine_var.get()),
                    target_lang=self._lang_code(lang_var.get()),
                )
            else:
                cfg = TranslationConfig(
                    mode="ai",
                    target_lang=self._lang_code(lang_var.get()),
                    ai=replace(self.config.ai),
                )

            def work() -> None:
                try:
                    out = cfg.build_translator().translate([TEST_SENTENCE])
                    results[channel] = "✓ " + (out[0] if out else "(空)")
                except Exception as exc:  # noqa: BLE001
                    results[channel] = f"✗ {exc}"

            threading.Thread(target=work, daemon=True).start()
            if not polling["running"]:
                polling["running"] = True
                dlg.after(150, poll_tests)

        # ── save / cancel ────────────────────────────────────────────────
        def save_and_close() -> None:
            store_profile()
            self.config.mode = self._mode.get()
            self.config.web_engine = self._engine_code(engine_var.get())
            self.config.target_lang = self._lang_code(lang_var.get())
            self._save_config()
            self._refresh_mode_labels()

            new_engine = EngineConfig(
                device=self._device_code(device_var.get()),
                threads=_parse_threads(threads_var.get()),
                ocr_batch=_parse_batch(batch_var.get()),
            ).normalized()
            changed = (
                new_engine.device != self.engine.device
                or new_engine.threads != self.engine.threads
                or new_engine.ocr_batch != self.engine.ocr_batch
            )
            self.engine = new_engine
            try:
                self.engine.save(ENGINE_CONFIG_PATH)
            except OSError as exc:
                self._set_status(f"性能配置保存失败：{exc}")
            dlg.destroy()
            if changed:
                self.reload_models()

        ctk.CTkButton(actions, text="保存并关闭", width=110,
                      command=save_and_close).pack(side="right")
        ctk.CTkButton(actions, text="取消", width=90,
                      command=dlg.destroy, **_BTN_SECONDARY).pack(side="right", padx=(0, 8))

    # -- action handlers (clipboard, copy, save, export, clear) --------------

    def on_copy_all(self) -> None:
        texts = [self._card_display(c) for c in self._cards if self._card_display(c)]
        if not texts:
            self._set_status("还没有识别结果")
            return
        self._copy_text("\n\n".join(texts))

    def _copy_text(self, text: str) -> None:
        if not text:
            self._set_status("没有可复制的内容")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status(f"已复制 {len(text)} 字")

    def on_export_overlays(self) -> None:
        if self._exporting_overlays:
            self._set_status("正在导出叠图，请稍候…")
            return
        cards = [card for card in self._cards if card.get("block_pairs")]
        if not cards:
            self._set_status("还没有可叠图的译文，请先翻译")
            return
        out_dir = filedialog.askdirectory(title="选择叠图导出目录")
        if not out_dir:
            return
        self._exporting_overlays = True
        self._set_status(f"正在导出 {len(cards)} 张叠图…")
        threading.Thread(target=self._export_overlays, args=(cards, out_dir),
                         daemon=True).start()

    def _export_overlays(self, cards: List[Dict], out_dir: str) -> None:
        directory = Path(out_dir)
        saved = 0
        failures: List[str] = []
        width = len(str(len(cards)))
        for number, card in enumerate(cards, start=1):
            try:
                overlay = card.get("overlay")
                if overlay is None:
                    result = card["result"]
                    translations = build_translations_for_blocks(
                        result.blocks, card["block_pairs"]
                    )
                    overlay = render_translated_image(
                        _pil_to_bgr(card["image"]), result.blocks, translations
                    )
                overlay.convert("RGB").save(directory / f"translated_{number:0{width}d}.png")
                saved += 1
            except TypesetError as exc:
                failures.append(f"第 {number} 张：{exc}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"第 {number} 张：{type(exc).__name__}: {exc}")
        self.queue.put(("overlays_done", (saved, len(cards), str(directory), failures)))

    def _on_overlays_done(self, saved: int, total: int, directory: str,
                          failures: List[str]) -> None:
        self._exporting_overlays = False
        if failures:
            self._set_status(f"叠图导出完成：{saved}/{total} 张")
            detail = "\n".join(failures[:8])
            if len(failures) > 8:
                detail += f"\n…另有 {len(failures) - 8} 张失败"
            messagebox.showwarning(
                "批量导出叠图",
                f"成功 {saved}/{total} 张，输出目录：\n{directory}\n\n失败：\n{detail}",
            )
            return
        self._set_status(f"已导出 {saved} 张叠图到 {directory}")
        messagebox.showinfo("批量导出叠图", f"已导出 {saved} 张叠图到：\n{directory}")

    def on_save(self) -> None:
        texts = [self._card_display(c) for c in self._cards if self._card_display(c)]
        if not texts:
            self._set_status("还没有识别结果")
            return
        path = filedialog.asksaveasfilename(
            title="保存识别结果", defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not path:
            return
        Path(path).write_text("\n\n".join(texts) + "\n", encoding="utf-8")
        self._set_status(f"已保存到 {path}")

    def on_clear(self) -> None:
        for card in self._cards:
            card["frame"].destroy()
        self._cards.clear()
        self._text_labels.clear()
        self._thumb_refs.clear()
        if self._pending is not None:
            self._pending["frame"].destroy()
            self._pending = None
        self._translating_cards = []
        if self._placeholder is None:
            self._add_placeholder()
        self._set_status("已清空")

    # -- helpers -------------------------------------------------------------

    def _toggle_topmost(self) -> None:
        self.root.attributes("-topmost", self._topmost.get())

    def _set_status(self, message: str) -> None:
        self.status.configure(text=message)

    def _on_log_configure(self, event: tk.Event) -> None:
        """Update text-label wraplengths when the log frame is resized."""
        wrap = max(140, event.width - THUMB_SIZE - 60)
        for label in self._text_labels:
            if label.winfo_exists():
                label.configure(wraplength=wrap)

    def _scroll_to_bottom(self) -> None:
        self.log.update_idletasks()
        try:
            self.log._parent_canvas.yview_moveto(1.0)  # type: ignore[attr-defined]
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _pil_to_bgr(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)


def _editor_height(text: str, per_row: int = 28) -> int:
    rows = -(-len(text) // per_row)
    return max(1, min(5, rows))


def _rewrite_block_text(block, text: str) -> None:
    x1, y1, x2, y2 = (float(value) for value in block.xyxy)
    block.lines = [ExtractedLine(
        polygon=[[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
        text=text,
    )]


def _format_detail(result: Optional[PageResult],
                   pairs: Optional[List[tuple]] = None) -> str:
    if result is None:
        return "（无数据）"
    lines = [f"{result.width} × {result.height}　{len(result.blocks)} 个文本块", ""]
    for index, block in enumerate(result.blocks):
        direction = "竖排" if block.vertical else "横排"
        x1, y1, x2, y2 = block.xyxy
        lines.append(f"#{index}　[{direction}]　({x1},{y1})-({x2},{y2})")
        for line in block.lines:
            lines.append(f"    {line.text}")
        lines.append("")
    if pairs:
        lines.append("—— 翻译（原文 / 译文）——")
        for src, tgt in pairs:
            lines.append(src)
            lines.append(tgt)
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI / entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manga-extract-gui",
        description="悬浮窗截图提取日文漫画文字。",
    )
    parser.add_argument("--model", type=Path, default=DEFAULT_DETECTOR,
                        help=f"检测模型路径（默认 {DEFAULT_DETECTOR}）")
    parser.add_argument("--hf-endpoint", default=None,
                        help="下载 OCR 模型用的 Hugging Face 镜像，如 https://hf-mirror.com")
    parser.add_argument("--no-topmost", action="store_true", help="启动时不置顶")
    return parser


def main(argv: Optional[List[str]] = None,
         root: Optional[tk.Tk] = None) -> int:
    """Entry point for the GUI.

    ``root`` may be a pre-created Tk instance (e.g. from a splash screen in
    ``run_gui.py``). When omitted a fresh :class:`ctk.CTk` is created here.
    """
    args = build_parser().parse_args(argv)

    enable_dpi_awareness()
    if root is None:
        root = ctk.CTk()
    app = MangaExtractApp(  # noqa: F841
        root,
        model_path=args.model,
        hf_endpoint=args.hf_endpoint,
        topmost=not args.no_topmost,
        preload=True,
    )
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return 0
