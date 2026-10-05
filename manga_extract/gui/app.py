"""Floating, always-on-top window for extracting manga text from the screen.

Workflow: press **截取**, drag a rectangle over the picture shown in the
browser, and the recognised Japanese text is appended to the log below like a
chat message. Captures from the clipboard and image files are supported too.
"""

from __future__ import annotations

import argparse
import os
import queue
import threading
import time
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageGrab, ImageTk

from ..detector import ComicTextDetector
from ..pipeline import MangaExtractor, PageResult
from ..recognizer import MangaOcrRecognizer
from ..translation import (
    TARGET_LANGUAGES,
    WEB_ENGINES,
    TranslationConfig,
    TranslationError,
)
from ..typeset import TypesetError, build_translations_for_blocks, render_translated_image
from ..visualize import draw_result
from .capture import enable_dpi_awareness, select_region

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DETECTOR = PROJECT_ROOT / "models" / "comic-text-detector.onnx"
CONFIG_PATH = PROJECT_ROOT / "translate_config.json"

CARD_BG = "#f6f7f9"
CARD_BORDER = "#c9ced6"
THUMB_SIZE = 132
FILE_TYPES = [
    ("图片", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"),
    ("所有文件", "*.*"),
]

# Presets for the AI model dropdown; the combobox itself stays editable.
AI_MODEL_PRESETS = [
    "deepseek-chat",
    "deepseek-reasoner",
    "gpt-4o-mini",
    "qwen-plus",
    "glm-4-flash",
    "moonshot-v1-8k",
    "llama3.1",
]

# Sample used by the two "测试" buttons. It deliberately carries some content
# (rather than a bare greeting) so a style prompt has something to act on.
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


class HoverTip:
    """Show a small tooltip while the pointer rests on ``widget``."""

    def __init__(self, widget: tk.Widget, text: str, wraplength: int = 340) -> None:
        self.widget = widget
        self.text = text
        self.wraplength = wraplength
        self.tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def show(self, _event: object = None) -> None:
        if self.tip is not None or not self.widget.winfo_exists():
            return
        tip = tk.Toplevel(self.widget)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=self.text, justify="left", wraplength=self.wraplength,
                 background="#ffffe1", foreground="#333333", relief="solid",
                 borderwidth=1, padx=8, pady=6).pack()
        tip.update_idletasks()
        # Prefer the space above the widget, drop below it if there is none.
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
        self._text_labels: List[tk.Label] = []
        self._not_ready = "正在加载模型…"

        self.config = TranslationConfig.load(CONFIG_PATH)
        self._mode = tk.StringVar(value=self.config.mode)
        self._translating = False
        self._translating_cards: List[Dict] = []

        root.title("漫画文字提取")
        root.geometry("460x660+80+80")
        root.minsize(360, 300)
        root.attributes("-topmost", bool(topmost))
        self._topmost = tk.BooleanVar(value=bool(topmost))

        self._build_ui()
        self._set_status("正在加载模型…" if preload else "模型未加载")
        if preload:
            threading.Thread(target=self._load_models, daemon=True).start()
        root.after(80, self._poll)

    # -- UI construction -----------------------------------------------------
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=6)
        outer.pack(fill="both", expand=True)

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(0, 6))

        self.btn_capture = ttk.Button(bar, text="截取", width=6,
                                      command=self.on_capture, state="disabled")
        self.btn_capture.pack(side="left")
        self.btn_clip = ttk.Button(bar, text="剪贴板", width=8,
                                   command=self.on_clipboard, state="disabled")
        self.btn_clip.pack(side="left", padx=(4, 0))
        self.btn_open = ttk.Button(bar, text="打开图片", width=9,
                                   command=self.on_open_file, state="disabled")
        self.btn_open.pack(side="left", padx=(4, 0))
        ttk.Checkbutton(bar, text="置顶", variable=self._topmost,
                        command=self._toggle_topmost).pack(side="left", padx=(8, 0))

        bar2 = ttk.Frame(outer)
        bar2.pack(fill="x", pady=(0, 6))
        ttk.Button(bar2, text="复制全部", width=9, command=self.on_copy_all).pack(side="left")
        ttk.Button(bar2, text="保存…", width=7, command=self.on_save).pack(side="left", padx=(4, 0))
        ttk.Button(bar2, text="清空", width=6, command=self.on_clear).pack(side="left", padx=(4, 0))
        ttk.Button(bar2, text="翻译设置", width=9,
                   command=self.open_settings).pack(side="left", padx=(4, 0))

        bar3 = ttk.Frame(outer)
        bar3.pack(fill="x", pady=(0, 6))
        ttk.Label(bar3, text="翻译：").pack(side="left")
        self._web_radio = ttk.Radiobutton(bar3, text="普通", value="web",
                                          variable=self._mode, command=self._on_mode_change)
        self._web_radio.pack(side="left")
        ttk.Radiobutton(bar3, text="AI", value="ai",
                        variable=self._mode, command=self._on_mode_change).pack(side="left", padx=(4, 0))
        ttk.Button(bar3, text="翻译全部", width=9,
                   command=self.on_translate_all).pack(side="right")

        # Scrollable log area.
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(body, highlightthickness=0, bg="#ffffff")
        vbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        self.log = ttk.Frame(self.canvas)
        self._log_window = self.canvas.create_window((0, 0), window=self.log, anchor="nw")
        self.log.bind("<Configure>",
                      lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        for widget in (self.canvas, self.log):
            widget.bind("<MouseWheel>", self._on_mousewheel)

        self._add_placeholder()

        self.status = ttk.Label(self.root, anchor="w", relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

        self._refresh_mode_labels()

    def _add_placeholder(self) -> None:
        label = tk.Label(
            self.log, justify="left", anchor="nw", fg="#888888", bg="#ffffff",
            text=("点「截取」后拖动鼠标框选图片区域，\n"
                  "识别结果会一条条显示在这里。\n\n"
                  "也可以用「剪贴板」粘贴 (Win+Shift+S) 截的图，\n"
                  "或用「打开图片」选择本地文件。\n\n"
                  "每个结果里的「翻译」按钮可翻译成中文，\n"
                  "译文以「原文一句 / 译文一句」排列。"),
        )
        label.pack(fill="x", padx=10, pady=10)
        self._placeholder = label

    # -- model loading -------------------------------------------------------
    def _load_models(self) -> None:
        try:
            if not self.model_path.is_file():
                raise FileNotFoundError(f"找不到检测模型：{self.model_path}")
            detector = ComicTextDetector(self.model_path)
            recognizer = MangaOcrRecognizer(hf_endpoint=self.hf_endpoint)
            self.extractor = MangaExtractor(detector=detector, recognizer=recognizer)
            self.queue.put(("ready", None))
        except Exception:
            self.queue.put(("fatal", traceback.format_exc()))

    # -- event queue ---------------------------------------------------------
    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "ready":
                    self._on_ready()
                elif kind == "result":
                    self._on_result(*payload)
                elif kind == "error":
                    self._on_error(payload)
                elif kind == "fatal":
                    self._on_fatal(payload)
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

    def _on_ready(self) -> None:
        for button in (self.btn_capture, self.btn_clip, self.btn_open):
            button.configure(state="normal")
        self._set_status("就绪　·　点「截取」框选图片区域")

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
        time.sleep(0.12)  # let the window disappear before the screen freezes
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
        try:
            bgr = _pil_to_bgr(image)
            result = self.extractor.extract_page(bgr)
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
        if getattr(self, "_placeholder", None) is not None:
            self._placeholder.destroy()
            self._placeholder = None

        frame = tk.Frame(self.log, bd=1, relief="solid", bg=CARD_BG,
                         highlightbackground=CARD_BORDER, padx=6, pady=6)
        frame.pack(fill="x", expand=True, padx=2, pady=(0, 6))

        thumb_label = tk.Label(frame, bg=CARD_BG)
        thumb_label.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 8))
        self._set_thumbnail_on(thumb_label, image, THUMB_SIZE)

        text_label = tk.Label(frame, text="识别中…", anchor="nw", justify="left",
                              bg=CARD_BG, wraplength=200)
        text_label.grid(row=0, column=1, sticky="nwe")
        self._text_labels.append(text_label)

        buttons = tk.Frame(frame, bg=CARD_BG)
        buttons.grid(row=1, column=1, sticky="sw", pady=(6, 0))
        card: Dict = {}

        copy_btn = ttk.Button(buttons, text="复制", width=4, state="disabled",
                              command=lambda: self._copy_text(self._card_display(card)))
        copy_btn.pack(side="left")
        translate_btn = ttk.Button(buttons, text="翻译", width=6, state="disabled",
                                   command=lambda: self.on_translate_card(card))
        translate_btn.pack(side="left", padx=(4, 0))
        overlay_btn = ttk.Button(buttons, text="叠图", width=4, state="disabled",
                                 command=lambda: self._show_overlay(card))
        overlay_btn.pack(side="left", padx=(4, 0))
        toggle_btn = ttk.Button(buttons, text="显示原文", width=8,
                                command=lambda: self._toggle_card_view(card))
        detail_btn = ttk.Button(buttons, text="详情", width=4, state="disabled",
                                command=lambda: self._show_detail(card))
        detail_btn.pack(side="left", padx=(4, 0))

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

    def _show_detail(self, card: Dict) -> tk.Toplevel:
        result: Optional[PageResult] = card.get("result")
        annotated: Image.Image = card.get("annotated", card["image"])
        win = tk.Toplevel(self.root)
        win.title("识别详情")
        win.attributes("-topmost", True)
        win.geometry("760x680")

        preview = annotated.copy()
        preview.thumbnail((720, 420))
        photo = ImageTk.PhotoImage(preview)
        self._thumb_refs.append(photo)
        tk.Label(win, image=photo, bg="#ffffff").pack(fill="x", padx=8, pady=8)

        text = tk.Text(win, wrap="word", height=14)
        text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        text.insert("1.0", _format_detail(result, card.get("pairs")))
        text.configure(state="disabled")

        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 8))
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
        card["overlay"] = None  # invalidate cached preview
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
        """原文一句、译文一句（翻译后）；否则显示原文。"""
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
                card["toggle_btn"].pack(side="left", padx=(4, 0), before=card["detail_btn"])

    def _toggle_card_view(self, card: Dict) -> None:
        card["show_original"] = not card.get("show_original", False)
        self._refresh_card(card)

    # -- translated-image overlay -------------------------------------------
    def _show_overlay(self, card: Dict) -> Optional[tk.Toplevel]:
        """Render the translation back onto the page and preview it."""
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

    def _open_image_window(self, title: str, image: Image.Image, default_name: str) -> tk.Toplevel:
        win = tk.Toplevel(self.root)
        win.title(title)
        win.attributes("-topmost", True)

        display = image.copy()
        display.thumbnail((1000, 760))
        photo = ImageTk.PhotoImage(display)
        self._thumb_refs.append(photo)
        tk.Label(win, image=photo, bg="#2b2b2b").pack(padx=8, pady=8)

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

        actions = ttk.Frame(win)
        actions.pack(fill="x", pady=(0, 8))
        ttk.Button(actions, text="保存图片", width=10, command=save).pack(side="left", padx=8)
        ttk.Button(actions, text="关闭", width=8, command=win.destroy).pack(side="right", padx=8)
        return win

    def open_settings(self) -> None:
        self.config.ensure_profiles()

        dlg = tk.Toplevel(self.root)
        dlg.title("翻译设置")
        dlg.attributes("-topmost", True)
        dlg.geometry("620x680")
        dlg.transient(self.root)
        dlg.resizable(False, False)

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill="both", expand=True)

        # -- 普通翻译（网页通道） ------------------------------------------
        web = ttk.LabelFrame(body, text="普通翻译（网页通道）", padding=10)
        web.pack(fill="x")
        ttk.Label(web, text="翻译引擎").grid(row=0, column=0, sticky="w")
        engine_var = tk.StringVar(value=self._engine_label(self.config.web_engine))
        ttk.Combobox(web, textvariable=engine_var, values=list(WEB_ENGINES.keys()),
                     state="readonly", width=18).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(web, text="目标语言").grid(row=1, column=0, sticky="w", pady=(6, 0))
        lang_var = tk.StringVar(value=self._lang_label(self.config.target_lang))
        ttk.Combobox(web, textvariable=lang_var, values=list(TARGET_LANGUAGES.keys()),
                     state="readonly", width=18).grid(row=1, column=1, sticky="w",
                                                      padx=(8, 0), pady=(6, 0))
        web_result = tk.StringVar(value="")
        ttk.Button(web, text="测试机翻", width=10,
                   command=lambda: run_test("web")).grid(row=0, column=2, rowspan=2, padx=(14, 0))
        ttk.Label(web, textvariable=web_result, foreground="#3a7", wraplength=500,
                  justify="left").grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # -- AI 翻译（多套 API 预设） --------------------------------------
        ai = ttk.LabelFrame(body, text="AI 翻译（OpenAI 兼容接口）", padding=10)
        ai.pack(fill="x", pady=(10, 0))

        profile_var = tk.StringVar()
        name_var = tk.StringVar(value=self.config.ai.name)
        base_var = tk.StringVar(value=self.config.ai.base_url)
        key_var = tk.StringVar(value=self.config.ai.api_key)
        model_var = tk.StringVar(value=self.config.ai.model)
        temp_var = tk.StringVar(value=str(self.config.ai.temperature))
        think_var = tk.BooleanVar(value=self.config.ai.disable_thinking)
        ai_result = tk.StringVar(value="")
        state = {"index": 0}

        ttk.Label(ai, text="接口配置").grid(row=0, column=0, sticky="w")
        profile_box = ttk.Combobox(ai, textvariable=profile_var, state="readonly", width=30)
        profile_box.grid(row=0, column=1, sticky="we", padx=(8, 0))

        profile_actions = ttk.Frame(ai)
        profile_actions.grid(row=0, column=2, sticky="w", padx=(6, 0))
        ttk.Button(profile_actions, text="新增", width=6,
                   command=lambda: add_profile()).pack(side="left")
        ttk.Button(profile_actions, text="删除", width=6,
                   command=lambda: delete_profile()).pack(side="left", padx=(4, 0))

        ttk.Label(ai, text="名称").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(ai, textvariable=name_var, width=34).grid(row=1, column=1, sticky="we",
                                                            padx=(8, 0), pady=(6, 0))
        ttk.Label(ai, text="接口地址").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(ai, textvariable=base_var, width=34).grid(row=2, column=1, sticky="we",
                                                            padx=(8, 0), pady=(6, 0))
        ttk.Label(ai, text="API Key").grid(row=3, column=0, sticky="w", pady=(6, 0))
        key_entry = ttk.Entry(ai, textvariable=key_var, width=34, show="•")
        key_entry.grid(row=3, column=1, sticky="we", padx=(8, 0), pady=(6, 0))
        show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(ai, text="显示", variable=show_key,
                        command=lambda: key_entry.configure(show="" if show_key.get() else "•"),
                        ).grid(row=3, column=2, sticky="w", padx=(6, 0), pady=(6, 0))
        ttk.Label(ai, text="模型").grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(ai, textvariable=model_var, values=AI_MODEL_PRESETS,
                     width=32).grid(row=4, column=1, sticky="we", padx=(8, 0), pady=(6, 0))
        ttk.Label(ai, text="随机性").grid(row=5, column=0, sticky="w", pady=(6, 0))
        temp_row = ttk.Frame(ai)
        temp_row.grid(row=5, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Entry(temp_row, textvariable=temp_var, width=10).pack(side="left")
        think_check = ttk.Checkbutton(temp_row, text="关闭思考模式（更快）", variable=think_var)
        think_check.pack(side="left", padx=(14, 0))
        temp_help = ttk.Button(temp_row, text="?", width=2, cursor="question_arrow")
        temp_help.pack(side="left", padx=(6, 0))
        temp_help.configure(command=HoverTip(temp_help, TEMPERATURE_HELP).show)
        HoverTip(think_check, THINKING_HELP)
        ttk.Label(ai, text="提示词").grid(row=6, column=0, sticky="nw", pady=(6, 0))
        prompt_text = tk.Text(ai, height=4, width=34, wrap="word")
        prompt_text.grid(row=6, column=1, sticky="we", padx=(8, 0), pady=(6, 0))
        if self.config.ai.prompt:
            prompt_text.insert("1.0", self.config.ai.prompt)
        ttk.Label(ai, text="追加给 AI 的风格要求，例如「用轻松吐槽的语气，保留拟声词」",
                  foreground="#888").grid(row=7, column=1, sticky="w", padx=(8, 0))
        ttk.Button(ai, text="测试 AI", width=10,
                   command=lambda: run_test("ai")).grid(row=8, column=1, sticky="w", pady=(10, 0))
        ttk.Label(ai, textvariable=ai_result, foreground="#3a7", wraplength=500,
                  justify="left").grid(row=9, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ai.columnconfigure(1, weight=1)

        def temperature() -> float:
            try:
                return float(temp_var.get())
            except ValueError:
                return 0.3

        def refresh_profile_box(select: int) -> None:
            names = [p.name or f"配置 {i + 1}" for i, p in enumerate(self.config.ai_profiles)]
            profile_box.configure(values=names)
            profile_box.current(select)

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
            """Write the form back into the selected profile."""
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
            target = profile_box.current()
            if target < 0 or target == state["index"]:
                return
            store_profile()
            load_profile(target)

        profile_box.bind("<<ComboboxSelected>>", on_profile_selected)

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

        load_profile(next((i for i, p in enumerate(self.config.ai_profiles)
                           if p is self.config.ai), 0))

        # -- 两个通道各自独立的「测试」 ------------------------------------
        test_vars = {"web": web_result, "ai": ai_result}
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
                except Exception as exc:  # noqa: BLE001 - surfaced to the user
                    results[channel] = f"✗ {exc}"

            threading.Thread(target=work, daemon=True).start()
            if not polling["running"]:
                polling["running"] = True
                dlg.after(150, poll_tests)

        def save_and_close() -> None:
            store_profile()
            self.config.mode = self._mode.get()
            self.config.web_engine = self._engine_code(engine_var.get())
            self.config.target_lang = self._lang_code(lang_var.get())
            self._mode.set(self.config.mode)
            self._save_config()
            self._refresh_mode_labels()
            self._set_status("翻译设置已保存")
            dlg.destroy()

        actions = ttk.Frame(body)
        actions.pack(fill="x", side="bottom", pady=(12, 0))
        ttk.Button(actions, text="保存", width=8, command=save_and_close).pack(side="right")
        ttk.Button(actions, text="取消", width=8, command=dlg.destroy).pack(side="right", padx=(0, 6))

    # -- toolbar actions -----------------------------------------------------
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
        if getattr(self, "_placeholder", None) is None:
            self._add_placeholder()
        self._set_status("已清空")

    # -- helpers -------------------------------------------------------------
    def _toggle_topmost(self) -> None:
        self.root.attributes("-topmost", self._topmost.get())

    def _set_status(self, message: str) -> None:
        self.status.configure(text=message)

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._log_window, width=event.width)
        wrap = max(140, event.width - THUMB_SIZE - 50)
        for label in self._text_labels:
            if label.winfo_exists():
                label.configure(wraplength=wrap)

    def _on_mousewheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _scroll_to_bottom(self) -> None:
        self.canvas.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(1.0)


def _pil_to_bgr(image: Image.Image) -> np.ndarray:
    """PIL image -> OpenCV BGR array."""
    return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)


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


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    enable_dpi_awareness()
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    app = MangaExtractApp(
        root,
        model_path=args.model,
        hf_endpoint=args.hf_endpoint,
        topmost=not args.no_topmost,
        preload=True,
    )
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return 0
