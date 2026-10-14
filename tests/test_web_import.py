"""web_import: HTML image extraction (pure) and downloading against a local fake site."""

from __future__ import annotations

import io
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manga_extract.studio import web_import  # noqa: E402
from manga_extract.studio.web_import import (  # noqa: E402
    WebImportError,
    download_image,
    extract_images,
    fetch_page,
    normalize_url,
    pick_from_srcset,
    sniff_image_suffix,
)

PAGE = "https://site.example/manga/ch1/index.html"


def urls(html: str, page: str = PAGE):
    return [c.url for c in extract_images(html, page)]


def png(width: int = 40, height: int = 60, color: str = "#336699") -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


class ExtractImagesTests(unittest.TestCase):
    def test_plain_images_keep_document_order(self):
        html = '<img src="/a.png"><p>x</p><img src="/b.jpg"><img src="/c.webp">'
        self.assertEqual(urls(html), ["https://site.example/a.png", "https://site.example/b.jpg",
                                      "https://site.example/c.webp"])

    def test_lazy_attribute_wins_over_a_placeholder_src(self):
        html = ('<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" data-src="/real1.jpg">'
                '<img src="/placeholder.gif" data-original="/real2.jpg">'
                '<img src="/spacer.png" data-lazy-src="/real3.jpg">')
        self.assertEqual(urls(html), ["https://site.example/real1.jpg",
                                      "https://site.example/real2.jpg",
                                      "https://site.example/real3.jpg"])

    def test_data_uri_only_images_are_skipped(self):
        self.assertEqual(urls('<img src="data:image/png;base64,AAAA">'), [])

    def test_srcset_picks_the_largest_descriptor(self):
        self.assertEqual(pick_from_srcset("a.jpg 480w, b.jpg 1200w, c.jpg 800w"), "b.jpg")
        self.assertEqual(pick_from_srcset("a.jpg 1x, b.jpg 2x"), "b.jpg")
        self.assertEqual(pick_from_srcset("a.jpg,b.jpg 2x"), "b.jpg")          # no space after comma
        self.assertEqual(pick_from_srcset("only.jpg"), "only.jpg")
        self.assertEqual(pick_from_srcset(""), "")

    def test_srcset_and_data_srcset_on_img(self):
        html = ('<img srcset="/s.jpg 400w, /l.jpg 1600w">'
                '<img data-srcset="/ds.jpg 500w, /dl.jpg 900w" src="/ph.gif">')
        self.assertEqual(urls(html), ["https://site.example/l.jpg", "https://site.example/dl.jpg"])

    def test_picture_sources_are_used_but_stray_sources_are_not(self):
        html = ('<picture><source srcset="/p.webp 1x, /p2.webp 2x"><img alt="no src"></picture>'
                '<video><source src="/clip.mp4" srcset="/not-an-image.jpg"></video>')
        self.assertEqual(urls(html), ["https://site.example/p2.webp"])

    def test_images_only_inside_noscript_are_found_and_not_duplicated(self):
        html = ('<img data-src="/a.jpg" src="/ph.gif">'
                '<noscript><img src="/a.jpg"></noscript>'
                '<noscript><img src="/only-here.jpg"></noscript>')
        self.assertEqual(urls(html), ["https://site.example/a.jpg",
                                      "https://site.example/only-here.jpg"])

    def test_relative_and_protocol_relative_urls_resolve_against_the_page(self):
        html = ('<img src="1.png"><img src="../2.png"><img src="/3.png">'
                '<img src="//cdn.example/4.png"><img src="https://other.example/5.png?x=1">')
        self.assertEqual(urls(html), [
            "https://site.example/manga/ch1/1.png", "https://site.example/manga/2.png",
            "https://site.example/3.png", "https://cdn.example/4.png",
            "https://other.example/5.png?x=1"])

    def test_base_href_changes_how_relative_urls_resolve(self):
        html = '<base href="https://img.example/pages/"><img src="1.png"><img src="/abs.png">'
        self.assertEqual(urls(html), ["https://img.example/pages/1.png", "https://img.example/abs.png"])

    def test_non_content_images_are_dropped(self):
        html = ('<img src="/logo.svg"><img src="/favicon.ico"><img src="/pixel.gif" width="1">'
                '<img src="/spacer.gif" height="1"><img src="javascript:alert(1)">'
                '<img src="file:///etc/passwd"><img src="ftp://x/y.png"><img src="/ok.png">')
        self.assertEqual(urls(html), ["https://site.example/ok.png"])

    def test_duplicates_are_removed_keeping_the_first(self):
        self.assertEqual(urls('<img src="/a.png"><img src="/b.png"><img src="/a.png">'),
                         ["https://site.example/a.png", "https://site.example/b.png"])

    def test_size_hints_are_captured(self):
        found = extract_images('<img src="/a.png" width="800px" height="1200">', PAGE)
        self.assertEqual((found[0].width_hint, found[0].height_hint), (800, 1200))

    def test_broken_or_empty_html_does_not_crash(self):
        self.assertEqual(urls(""), [])
        self.assertEqual(urls("<img src=\"/a.png\" <div <<>> <img"), ["https://site.example/a.png"])
        self.assertEqual(urls("just text"), [])

    def test_uppercase_tags_and_attributes_work(self):
        self.assertEqual(urls('<IMG SRC="/A.PNG" DATA-SRC="/B.PNG">'), ["https://site.example/B.PNG"])


class UrlAndSniffTests(unittest.TestCase):
    def test_normalize_url(self):
        self.assertEqual(normalize_url(" example.com/ch1 "), "https://example.com/ch1")
        self.assertEqual(normalize_url("http://a.b/c"), "http://a.b/c")
        for bad in ("", "   ", "ftp://x.y/z", "file:///etc/passwd", "javascript:alert(1)", "https://",
                    "mailto:a@b.c", "data:text/html,hi", "view-source:example.com"):
            with self.assertRaises(WebImportError, msg=bad):
                normalize_url(bad)
        # a port is not a scheme
        self.assertEqual(normalize_url("localhost:8080/x"), "https://localhost:8080/x")
        self.assertEqual(normalize_url("127.0.0.1:8000"), "https://127.0.0.1:8000")
        self.assertEqual(normalize_url("example.com/a:b"), "https://example.com/a:b")

    def test_sniffing_image_types(self):
        self.assertEqual(sniff_image_suffix(png()), ".png")
        self.assertEqual(sniff_image_suffix(b"\xff\xd8\xff\xe0" + b"0" * 20), ".jpg")
        self.assertEqual(sniff_image_suffix(b"GIF89a" + b"0" * 20), ".gif")
        self.assertEqual(sniff_image_suffix(b"RIFF\x00\x00\x00\x00WEBPVP8 "), ".webp")
        self.assertEqual(sniff_image_suffix(b"BM" + b"0" * 20), ".bmp")
        self.assertEqual(sniff_image_suffix(b"II*\x00" + b"0" * 8), ".tif")
        self.assertIsNone(sniff_image_suffix(b"<!DOCTYPE html>"))
        self.assertIsNone(sniff_image_suffix(b"RIFF\x00\x00\x00\x00WAVEfmt "))        # RIFF but not WebP
        self.assertIsNone(sniff_image_suffix(b""))

    def test_html_decoding_follows_the_declared_charset(self):
        text = "<img alt='漫画第一话'>"
        self.assertEqual(web_import._decode_html(text.encode("gb18030"), "text/html; charset=gb2312"), text)
        meta = ("<meta charset='gbk'>" + text).encode("gbk")
        self.assertEqual(web_import._decode_html(meta, "text/html"), "<meta charset='gbk'>" + text)
        self.assertEqual(web_import._decode_html(text.encode("utf-8"), ""), text)
        self.assertEqual(web_import._decode_html(b"\xff\xfe\x00", "text/html; charset=nonsense"),
                         "\xff\xfe\x00")                                              # never raises


class FakeSite:
    """A tiny web server: routes can demand a Referer, redirect, stall, or lie about content."""

    def __init__(self):
        self.routes = {}
        self.log = []                                   # (path, referer, user_agent)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):               # keep test output clean
                pass

            def do_GET(self):
                route = outer.routes.get(self.path.split("?")[0])
                outer.log.append((self.path, self.headers.get("Referer"), self.headers.get("User-Agent")))
                if route is None:
                    self.send_error(404)
                    return
                if route.get("referer") and self.headers.get("Referer") != route["referer"]():
                    self.send_error(403)
                    return
                if route.get("sleep"):
                    time.sleep(route["sleep"])
                if route.get("redirect"):
                    self.send_response(302)
                    self.send_header("Location", route["redirect"])
                    self.end_headers()
                    return
                body = route["body"]
                self.send_response(route.get("status", 200))
                self.send_header("Content-Type", route.get("type", "application/octet-stream"))
                if not route.get("no_length"):
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class QuietServer(ThreadingHTTPServer):
            def handle_error(self, request, client_address):   # clients abort on purpose (timeouts)
                pass

        self.server = QuietServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def add(self, path, body, type="application/octet-stream", **options):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.routes[path] = dict(body=body, type=type, **options)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.site = FakeSite()
        self.addCleanup(self.site.close)
        self.page_url = self.site.base + "/ch/1.html"

    # -- pages --------------------------------------------------------------
    def test_fetching_a_page_lists_absolute_image_urls(self):
        self.site.add("/ch/1.html", '<img data-src="p1.png"><img src="/img/p2.png">', "text/html")
        page = fetch_page(self.page_url)
        self.assertEqual([c.url for c in page.candidates],
                         [self.site.base + "/ch/p1.png", self.site.base + "/img/p2.png"])

    def test_redirects_are_followed_and_urls_resolve_against_the_final_page(self):
        self.site.add("/short", b"", redirect="/ch/1.html")
        self.site.add("/ch/1.html", '<img src="p1.png">', "text/html")
        page = fetch_page(self.site.base + "/short")
        self.assertEqual(page.url, self.page_url)
        self.assertEqual(page.candidates[0].url, self.site.base + "/ch/p1.png")

    def test_http_errors_are_reported_in_words(self):
        with self.assertRaises(WebImportError) as ctx:
            fetch_page(self.site.base + "/nope")
        self.assertIn("404", str(ctx.exception))

    def test_connection_refused_is_reported(self):
        self.site.close()
        with self.assertRaises(WebImportError) as ctx:                 # not a raw URLError
            fetch_page(self.page_url, timeout=2)
        # Windows may stall on a closed local port until the timeout instead of refusing at once.
        self.assertTrue("无法连接" in str(ctx.exception) or "超时" in str(ctx.exception))
        self.site = FakeSite()                      # so the cleanup has something to close

    def test_a_slow_page_times_out(self):
        self.site.add("/slow", '<img src="/a.png">', "text/html", sleep=1.5)
        with self.assertRaises(WebImportError) as ctx:
            fetch_page(self.site.base + "/slow", timeout=0.3)
        self.assertIn("超时", str(ctx.exception))

    def test_an_oversized_page_is_refused(self):
        self.site.add("/big", "<img src='/a.png'>" + "x" * 5000, "text/html")
        with mock.patch.object(web_import, "MAX_PAGE_BYTES", 1000):
            with self.assertRaises(WebImportError) as ctx:
                fetch_page(self.site.base + "/big")
        self.assertIn("太大", str(ctx.exception))

    def test_the_browser_like_user_agent_is_sent(self):
        self.site.add("/ch/1.html", "", "text/html")
        fetch_page(self.page_url)
        self.assertIn("Mozilla/5.0", self.site.log[-1][2])

    # -- images -------------------------------------------------------------
    def test_download_sends_the_referer_that_hotlink_protection_demands(self):
        self.site.add("/img/1.png", png(), "image/png", referer=lambda: self.page_url)
        with self.assertRaises(WebImportError) as ctx:
            download_image(self.site.base + "/img/1.png")                       # no Referer
        self.assertIn("403", str(ctx.exception))
        with self.assertRaises(WebImportError):
            download_image(self.site.base + "/img/1.png", referer="https://wrong.example/")
        image = download_image(self.site.base + "/img/1.png", referer=self.page_url)
        self.assertEqual((image.suffix, image.data[:4]), (".png", b"\x89PNG"))
        self.assertEqual(self.site.log[-1][1], self.page_url)

    def test_an_html_answer_is_recognised_as_hotlink_blocking(self):
        self.site.add("/img/1.png", "<!DOCTYPE html><html>请从原站访问</html>", "text/html")
        with self.assertRaises(WebImportError) as ctx:
            download_image(self.site.base + "/img/1.png")
        self.assertIn("防盗链", str(ctx.exception))

    def test_garbage_is_not_an_image(self):
        self.site.add("/img/1.png", b"\x00\x01\x02 not an image", "image/png")
        with self.assertRaises(WebImportError) as ctx:
            download_image(self.site.base + "/img/1.png")
        self.assertIn("不支持", str(ctx.exception))

    def test_the_type_comes_from_the_bytes_not_from_the_url_or_header(self):
        self.site.add("/img/x.jpg", png(), "image/jpeg")                        # a PNG called .jpg
        self.assertEqual(download_image(self.site.base + "/img/x.jpg").suffix, ".png")

    def test_oversized_images_are_refused_with_and_without_a_content_length(self):
        data = png(300, 300)
        self.site.add("/with-length.png", data, "image/png")
        self.site.add("/no-length.png", data, "image/png", no_length=True)
        for path in ("/with-length.png", "/no-length.png"):
            with self.assertRaises(WebImportError, msg=path) as ctx:
                download_image(self.site.base + path, max_bytes=100)
            self.assertIn("太大", str(ctx.exception))
        self.assertEqual(download_image(self.site.base + "/no-length.png").suffix, ".png")

    def test_a_stalled_image_times_out(self):
        self.site.add("/slow.png", png(), "image/png", sleep=1.5)
        with self.assertRaises(WebImportError) as ctx:
            download_image(self.site.base + "/slow.png", timeout=0.3)
        self.assertIn("超时", str(ctx.exception))

    def test_only_http_images_are_downloaded(self):
        for bad in ("file:///etc/passwd", "ftp://x/y.png", "data:image/png;base64,AAAA"):
            with self.assertRaises(WebImportError, msg=bad):
                download_image(bad)

    def test_a_whole_chapter_round_trip(self):
        """Page -> candidate list -> every image, the way the dialog drives it."""
        colors = ["#ff0000", "#00ff00", "#0000ff"]
        html = "".join(f'<img src="ph.gif" data-src="/img/{i}.png">' for i in range(3))
        self.site.add("/ch/1.html", html + '<img src="/logo.svg">', "text/html")
        for i, color in enumerate(colors):
            self.site.add(f"/img/{i}.png", png(40, 60, color), "image/png",
                          referer=lambda: self.page_url)
        page = fetch_page(self.page_url)
        self.assertEqual(len(page.candidates), 3)
        got = [download_image(c.url, referer=page.url) for c in page.candidates]
        self.assertEqual([g.suffix for g in got], [".png"] * 3)
        from PIL import Image

        pixels = [Image.open(io.BytesIO(g.data)).convert("RGB").getpixel((5, 5)) for g in got]
        self.assertEqual(pixels, [(255, 0, 0), (0, 255, 0), (0, 0, 255)])        # in page order


if __name__ == "__main__":
    unittest.main()
