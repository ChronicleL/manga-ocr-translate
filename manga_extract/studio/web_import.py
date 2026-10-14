"""Find and download the page images of a chapter from a web page (static HTML only).

What is handled: ``<img>`` with lazy-loading attributes (``data-src`` & co.), ``srcset``,
``<picture><source>``, images only present inside ``<noscript>``, ``<base href>``, relative and
protocol-relative URLs, hot-link protection (a ``Referer`` is sent), and servers that answer
an image request with an HTML page.

What is not: pages that build their image list with JavaScript, and sites that need a login.
For those the user falls back to saving the images or taking screenshots.

Pure standard library (``urllib`` + ``html.parser``), no Qt, so it is easy to test.
"""

from __future__ import annotations

import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

from ..translation import USER_AGENT

MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 40 * 1024 * 1024
CHUNK = 64 * 1024

# Attributes sites use for the real image while ``src`` holds a placeholder, most specific first.
LAZY_ATTRS = ("data-original", "data-src", "data-lazy-src", "data-lazy", "data-url",
              "data-actualsrc", "data-echo", "data-origin", "data-full", "data-hi-res")
SKIP_SUFFIXES = (".svg", ".ico")


class WebImportError(RuntimeError):
    """A page or an image could not be fetched; the message is meant for the user."""


@dataclass
class ImageCandidate:
    url: str
    width_hint: int = 0       # from the width/height attributes, 0 when unknown
    height_hint: int = 0


@dataclass
class DownloadedImage:
    url: str
    data: bytes
    suffix: str               # ".png" / ".jpg" / ".webp" / ".gif" / ".bmp" / ".tif"


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

def normalize_url(text: str) -> str:
    """``example.com/ch1`` -> ``https://example.com/ch1``; rejects anything but http(s)."""
    text = (text or "").strip()
    if not text:
        raise WebImportError("请输入网址")
    if "://" not in text:
        # "javascript:alert(1)" / "mailto:a@b.c" name another scheme; "localhost:8080/x" is a port.
        if re.match(r"[A-Za-z][A-Za-z0-9+.\-]*:(?!\d+(?:/|$))", text):
            raise WebImportError("只支持 http / https 网址")
        text = "https://" + text.lstrip("/")
    parts = urlparse(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise WebImportError("只支持 http / https 网址")
    return text


def _is_http(url: str) -> bool:
    return urlparse(url).scheme in ("http", "https")


# ---------------------------------------------------------------------------
# HTML -> image candidates
# ---------------------------------------------------------------------------

_SRCSET_ITEM = re.compile(r"([^\s,]+)(?:\s+(\d+(?:\.\d+)?)([wx]))?")


def pick_from_srcset(value: str) -> str:
    """The URL with the largest ``w`` / ``x`` descriptor of a ``srcset`` (last one if none)."""
    best_url, best_weight = "", -1.0
    for url, number, _unit in _SRCSET_ITEM.findall(value or ""):
        weight = float(number) if number else 0.0
        if weight >= best_weight:
            best_url, best_weight = url, weight
    return best_url


def _int(value: Optional[str]) -> int:
    match = re.match(r"\s*(\d+)", value or "")
    return int(match.group(1)) if match else 0


class _ImageCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href: Optional[str] = None
        self.items: List[Tuple[str, int, int]] = []      # raw (url, width, height) in DOM order
        self._picture_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if tag == "base" and self.base_href is None and attributes.get("href"):
            self.base_href = attributes["href"]
        elif tag == "picture":
            self._picture_depth += 1
        elif tag == "img":
            url = self._img_url(attributes)
            if url:
                self.items.append((url, _int(attributes.get("width")), _int(attributes.get("height"))))
        elif tag == "source" and self._picture_depth:
            url = pick_from_srcset(attributes.get("data-srcset") or attributes.get("srcset", ""))
            if url:
                self.items.append((url, 0, 0))

    def handle_endtag(self, tag: str) -> None:
        if tag == "picture" and self._picture_depth:
            self._picture_depth -= 1

    @staticmethod
    def _img_url(attributes: dict) -> str:
        for name in LAZY_ATTRS:
            value = attributes.get(name, "").strip()
            if value and not value.startswith("data:"):
                return value
        srcset = attributes.get("data-srcset") or attributes.get("srcset", "")
        picked = pick_from_srcset(srcset)
        if picked and not picked.startswith("data:"):
            return picked
        src = attributes.get("src", "").strip()
        return "" if src.startswith("data:") else src


def extract_images(html: str, page_url: str) -> List[ImageCandidate]:
    """Image URLs of a page in document order, absolute and without duplicates."""
    collector = _ImageCollector()
    collector.feed(html or "")
    collector.close()
    base = urljoin(page_url, collector.base_href) if collector.base_href else page_url

    found: List[ImageCandidate] = []
    seen = set()
    for raw, width, height in collector.items:
        url = urljoin(base, raw.strip())
        if not _is_http(url) or url in seen:
            continue
        path = urlparse(url).path.lower()
        if path.endswith(SKIP_SUFFIXES):
            continue
        if (width and width <= 1) or (height and height <= 1):
            continue                                            # tracking pixel / spacer
        seen.add(url)
        found.append(ImageCandidate(url, width, height))
    return found


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def _describe(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"服务器返回 {exc.code}"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "连接超时"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "连接超时"
        return f"无法连接：{reason}"
    return f"{type(exc).__name__}: {exc}"


def _read_limited(response, limit: int, what: str) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise WebImportError(f"{what}太大（超过 {limit // (1024 * 1024)} MB）")
    chunks, total = [], 0
    while True:
        chunk = response.read(CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise WebImportError(f"{what}太大（超过 {limit // (1024 * 1024)} MB）")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_html(data: bytes, content_type: str) -> str:
    charset = ""
    match = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    else:
        head = data[:4096].decode("ascii", "ignore")
        meta = re.search(r"<meta[^>]+charset=[\"']?([\w\-]+)", head, re.I)
        if meta:
            charset = meta.group(1)
    for candidate in (charset, "utf-8", "gb18030"):
        if not candidate:
            continue
        try:
            return data.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("latin-1", "replace")


@dataclass
class FetchedPage:
    url: str                  # after redirects
    candidates: List[ImageCandidate]


def fetch_page(url: str, timeout: float = 20.0) -> FetchedPage:
    """Download a page and list its images."""
    url = normalize_url(url)
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = _read_limited(response, MAX_PAGE_BYTES, "网页")
            html = _decode_html(data, response.headers.get("Content-Type", ""))
            final_url = response.geturl()
    except WebImportError:
        raise
    except Exception as exc:  # noqa: BLE001 - turned into a message for the user
        raise WebImportError(_describe(exc)) from exc
    return FetchedPage(final_url, extract_images(html, final_url))


_MAGIC: Sequence[Tuple[bytes, str]] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
    (b"II*\x00", ".tif"),
    (b"MM\x00*", ".tif"),
)


def sniff_image_suffix(data: bytes) -> Optional[str]:
    """File suffix from the leading bytes, or None when it is not a supported image."""
    for magic, suffix in _MAGIC:
        if data.startswith(magic):
            return suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def download_image(url: str, referer: Optional[str] = None, timeout: float = 30.0,
                   max_bytes: int = MAX_IMAGE_BYTES) -> DownloadedImage:
    """Fetch one image, sending a Referer so hot-link protection lets it through."""
    if not _is_http(url):
        raise WebImportError("只支持 http / https 图片")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    timeout=timeout) as response:
            data = _read_limited(response, max_bytes, "图片")
    except WebImportError:
        raise
    except Exception as exc:  # noqa: BLE001 - turned into a message for the user
        raise WebImportError(_describe(exc)) from exc
    suffix = sniff_image_suffix(data)
    if suffix is None:
        looks_like_page = data.lstrip()[:15].lower().startswith((b"<!doctype", b"<html"))
        raise WebImportError("服务器返回的不是图片（可能被防盗链拦截，试试改一下 Referer）"
                             if looks_like_page else "不支持的图片格式")
    return DownloadedImage(url, data, suffix)
