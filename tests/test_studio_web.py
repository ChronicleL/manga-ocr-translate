"""WebImporter (background fetching) and WebImportDialog, against the local fake site."""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from manga_extract.studio.bootstrap import prepare_windows_dlls

    prepare_windows_dlls()
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from _studio_fakes import dispose_widget, wait_until
    from test_web_import import FakeSite, png

    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False


if HAVE_QT:
    class Sink(QObject):
        """Collects importer signals through bound methods, like the dialog does."""

        def __init__(self, signals):
            super().__init__()
            self.pages, self.fetch_errors, self.loaded, self.failed = [], [], [], []
            signals.pageFetched.connect(self.on_page)
            signals.fetchFailed.connect(self.on_fetch_failed)
            signals.imageLoaded.connect(self.on_loaded)
            signals.imageFailed.connect(self.on_failed)

        def on_page(self, url, candidates): self.pages.append((url, candidates))
        def on_fetch_failed(self, message): self.fetch_errors.append(message)
        def on_loaded(self, index, thumb, w, h, nbytes, path, suffix):
            self.loaded.append((index, thumb, w, h, nbytes, path, suffix))
        def on_failed(self, index, message): self.failed.append((index, message))


class SiteMixin:
    """A fake chapter: 3 full pages, 1 small icon and 1 'image' that is really an HTML page."""

    SIZES = [(800, 1200), (800, 1200), (900, 1300), (120, 80)]
    COLORS = ["#ff0000", "#00ff00", "#0000ff", "#ffff00"]

    def start_site(self, require_referer: bool = True):
        self.site = FakeSite()
        self.addCleanup(self.site.close)
        self.page_url = self.site.base + "/ch/1.html"
        gate = (lambda: self.page_url) if require_referer else None
        html = ""
        for i in range(len(self.SIZES)):
            html += f'<img src="/ph.gif" data-src="/img/{i}.png">'
        html += '<img src="/img/blocked.png">'
        self.site.add("/ch/1.html", html, "text/html")
        for i, (w, h) in enumerate(self.SIZES):
            options = {"referer": gate} if gate else {}
            self.site.add(f"/img/{i}.png", png(w, h, self.COLORS[i]), "image/png", **options)
        self.site.add("/img/blocked.png", "<html>请从原站访问</html>", "text/html")
        self.image_bytes = {f"{self.site.base}/img/{i}.png": png(w, h, self.COLORS[i])
                            for i, (w, h) in enumerate(self.SIZES)}


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class DaemonPoolTests(unittest.TestCase):
    def test_a_failing_job_does_not_kill_its_worker(self):
        from manga_extract.studio.web_worker import DaemonPool

        pool, done = DaemonPool(workers=1), threading.Event()
        import contextlib
        import io

        with contextlib.redirect_stderr(io.StringIO()):               # the traceback is expected
            pool.submit(lambda: 1 / 0)
            pool.submit(done.set)
            self.assertTrue(done.wait(3))

    def test_workers_are_daemon_threads(self):
        from manga_extract.studio.web_worker import DaemonPool

        pool = DaemonPool(workers=2)
        pool.submit(lambda: None)
        self.assertTrue(all(thread.daemon for thread in pool._threads))

    def test_pending_jobs_can_be_dropped(self):
        from manga_extract.studio.web_worker import DaemonPool

        pool, gate, ran = DaemonPool(workers=1), threading.Event(), []
        pool.submit(gate.wait, 5)                                       # occupies the only worker
        time.sleep(0.1)
        for i in range(5):
            pool.submit(ran.append, i)
        pool.clear_pending()
        gate.set()
        time.sleep(0.3)
        self.assertEqual(ran, [])


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class WebImporterTests(SiteMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from _studio_fakes import tempdir
        from manga_extract.studio.web_worker import WebImporter

        self.start_site()
        self.folder = tempdir(self) / "dl"
        self.importer = WebImporter()
        self.sink = Sink(self.importer.signals)

    def test_fetch_reports_the_final_url_and_candidates(self):
        self.importer.fetch_page(self.page_url)
        self.assertTrue(wait_until(self.app, lambda: self.sink.pages))
        url, candidates = self.sink.pages[0]
        self.assertEqual(url, self.page_url)
        self.assertEqual(len(candidates), 5)

    def test_fetch_failure_is_reported_once_in_words(self):
        self.importer.fetch_page(self.site.base + "/missing")
        self.assertTrue(wait_until(self.app, lambda: self.sink.fetch_errors))
        self.assertIn("404", self.sink.fetch_errors[0])
        self.assertEqual(self.sink.pages, [])

    def test_images_are_saved_by_index_with_thumbnails_and_real_sizes(self):
        self.importer.fetch_page(self.page_url)
        wait_until(self.app, lambda: self.sink.pages)
        candidates = self.sink.pages[0][1]
        self.importer.load_images(candidates[:4], self.page_url, self.folder)
        self.assertTrue(wait_until(self.app, lambda: len(self.sink.loaded) == 4))
        by_index = {item[0]: item for item in self.sink.loaded}
        for index, (w, h) in enumerate(self.SIZES):
            _i, thumb, width, height, nbytes, path, suffix = by_index[index]
            self.assertEqual((width, height, suffix), (w, h, ".png"))
            self.assertEqual(Path(path).name, f"{index:04d}.png")
            self.assertEqual(Path(path).read_bytes(), self.image_bytes[candidates[index].url])
            self.assertEqual(nbytes, len(self.image_bytes[candidates[index].url]))
            self.assertLessEqual(thumb.width(), 170)
            self.assertLessEqual(thumb.height(), 230)

    def test_a_blocked_image_fails_with_a_hint_and_does_not_stop_the_others(self):
        self.importer.fetch_page(self.page_url)
        wait_until(self.app, lambda: self.sink.pages)
        self.importer.load_images(self.sink.pages[0][1], self.page_url, self.folder)
        self.assertTrue(wait_until(self.app, lambda: len(self.sink.loaded) == 4 and self.sink.failed))
        self.assertEqual([i for i, _m in self.sink.failed], [4])
        self.assertIn("防盗链", self.sink.failed[0][1])

    def test_without_a_referer_hotlink_protected_images_fail(self):
        self.importer.fetch_page(self.page_url)
        wait_until(self.app, lambda: self.sink.pages)
        self.importer.load_images(self.sink.pages[0][1][:4], None, self.folder)
        self.assertTrue(wait_until(self.app, lambda: len(self.sink.failed) == 4))
        self.assertTrue(all("403" in message for _i, message in self.sink.failed))
        self.assertEqual(self.sink.loaded, [])

    def test_cancel_drops_results_that_are_still_in_flight(self):
        self.site.add("/img/slow.png", png(), "image/png", sleep=0.6)
        from manga_extract.studio.web_import import ImageCandidate

        self.importer.load_images([ImageCandidate(self.site.base + "/img/slow.png")], None, self.folder)
        time.sleep(0.15)
        self.importer.cancel()
        time.sleep(0.9)
        self.app.processEvents()
        self.assertEqual((self.sink.loaded, self.sink.failed), ([], []))

    def test_starting_a_new_fetch_drops_the_old_downloads(self):
        self.site.add("/img/slow.png", png(), "image/png", sleep=0.6)
        from manga_extract.studio.web_import import ImageCandidate

        self.importer.load_images([ImageCandidate(self.site.base + "/img/slow.png")], None, self.folder)
        time.sleep(0.15)
        self.importer.fetch_page(self.page_url)
        self.assertTrue(wait_until(self.app, lambda: self.sink.pages))
        time.sleep(0.8)
        self.app.processEvents()
        self.assertEqual(self.sink.loaded, [])                           # the stale download is gone


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class WebImportDialogTests(SiteMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.start_site()

    def dialog(self, **kwargs):
        from manga_extract.studio.web_dialog import WebImportDialog

        dlg = WebImportDialog(None, **kwargs)
        dlg.show()
        self.addCleanup(dispose_widget, self.app, dlg)
        self.app.processEvents()
        return dlg

    def load(self, dlg, expect_finished: bool = True):
        dlg.url_edit.setText(self.page_url)
        dlg.parse_button.click()
        self.assertTrue(wait_until(self.app, lambda: len(dlg._cards) == 5))
        if expect_finished:
            self.assertTrue(wait_until(self.app, lambda: all(c.state != "loading" for c in dlg._cards)))

    # -- the happy path -------------------------------------------------------
    def test_parsing_builds_one_card_per_image_in_page_order(self):
        dlg = self.dialog()
        self.load(dlg)
        self.assertEqual([c.url for c in dlg._cards[:4]],
                         [f"{self.site.base}/img/{i}.png" for i in range(4)])
        self.assertEqual(dlg._cards[0].toolTip(), dlg._cards[0].url)
        self.assertEqual([(dlg.grid.getItemPosition(i)[0], dlg.grid.getItemPosition(i)[1])
                          for i in range(5)], [(0, 0), (0, 1), (0, 2), (0, 3), (1, 0)])

    def test_large_images_are_ticked_small_and_failed_ones_are_not(self):
        dlg = self.dialog()
        self.load(dlg)
        self.assertEqual([c.check.isChecked() for c in dlg._cards], [True, True, True, False, False])
        self.assertEqual([c.state for c in dlg._cards], ["loaded"] * 4 + ["failed"])
        self.assertFalse(dlg._cards[4].check.isEnabled())                    # a failed one cannot be ticked
        self.assertEqual(dlg.summary.text(), "已选 3 / 共 5 张")
        self.assertTrue(dlg.import_button.isEnabled())

    def test_caption_shows_the_size_and_a_failure_shows_why(self):
        dlg = self.dialog()
        self.load(dlg)
        self.assertIn("800×1200", dlg._cards[0].caption.text())
        self.assertIn("防盗链", dlg._cards[4].caption.text())
        self.assertIn("1 张失败", dlg.status.text())
        self.assertIn("Referer", dlg.status.text())

    def test_the_page_url_is_the_default_referer(self):
        """No Referer typed: the page address is sent, which the fake site demands."""
        dlg = self.dialog()
        self.load(dlg)
        self.assertEqual(sum(c.state == "loaded" for c in dlg._cards), 4)
        self.assertEqual({r for _p, r, _u in self.site.log if _p.startswith("/img/0")}, {self.page_url})

    def test_a_wrong_referer_fails_everything_and_fixing_it_then_reloading_works(self):
        dlg = self.dialog()
        dlg.referer_edit.setText("https://wrong.example/")
        self.load(dlg)
        self.assertEqual(sum(c.state == "failed" for c in dlg._cards), 5)
        self.assertFalse(dlg.import_button.isEnabled())
        self.assertTrue(dlg.reload_button.isEnabled())
        dlg.referer_edit.setText(self.page_url)
        dlg.reload_button.click()
        self.assertTrue(wait_until(self.app, lambda: sum(c.state == "loaded" for c in dlg._cards) == 4))
        self.assertTrue(dlg.import_button.isEnabled())

    # -- selection ------------------------------------------------------------
    def test_select_all_and_none_only_touch_loaded_cards(self):
        dlg = self.dialog()
        self.load(dlg)
        dlg.select_none_button.click()
        self.assertEqual(dlg.summary.text(), "已选 0 / 共 5 张")
        self.assertFalse(dlg.import_button.isEnabled())
        dlg.select_all_button.click()
        self.assertEqual(dlg.summary.text(), "已选 4 / 共 5 张")             # not the failed one

    def test_changing_the_minimum_width_reapplies_the_ticks(self):
        dlg = self.dialog()
        self.load(dlg)
        dlg.set_min_width(850)                                               # only the 900px page passes
        self.assertEqual([c.check.isChecked() for c in dlg._cards], [False, False, True, False, False])
        dlg.set_min_width(0)
        self.assertEqual([c.check.isChecked() for c in dlg._cards[:4]], [True] * 4)

    def test_the_minimum_width_box_takes_digits_only_and_empty_means_zero(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        dlg = self.dialog()
        self.load(dlg)
        self.assertEqual(dlg.min_width, 300)
        dlg.min_width_edit.clear()
        QTest.keyClicks(dlg.min_width_edit, "ab-")                            # letters are rejected
        self.assertEqual(dlg.min_width_edit.text(), "")
        self.assertEqual(dlg.min_width, 0)
        self.assertEqual([c.check.isChecked() for c in dlg._cards[:4]], [True] * 4)   # 0 ticks everything
        QTest.keyClicks(dlg.min_width_edit, "850")
        self.assertEqual(dlg.min_width, 850)
        self.assertEqual([c.check.isChecked() for c in dlg._cards], [False, False, True, False, False])

    def test_the_minimum_width_given_at_construction_is_used(self):
        dlg = self.dialog(min_width=100)
        self.load(dlg)
        self.assertEqual([c.check.isChecked() for c in dlg._cards[:4]], [True] * 4)

    # -- result ---------------------------------------------------------------
    def test_accepting_returns_the_ticked_images_in_page_order_with_their_bytes(self):
        dlg = self.dialog()
        self.load(dlg)
        dlg._cards[1].check.setChecked(False)
        dlg.import_button.click()
        self.assertEqual([i.url for i in dlg.images],
                         [f"{self.site.base}/img/0.png", f"{self.site.base}/img/2.png"])
        self.assertEqual([i.suffix for i in dlg.images], [".png", ".png"])
        for image in dlg.images:
            self.assertEqual(image.data, self.image_bytes[image.url])

    def test_the_temp_folder_is_removed_on_accept_cancel_and_close(self):
        for how in ("accept", "reject", "close"):
            dlg = self.dialog()
            self.load(dlg)
            folder = dlg._folder
            self.assertTrue(folder.exists(), how)
            {"accept": dlg.import_button.click, "reject": dlg.cancel_button.click, "close": dlg.close}[how]()
            self.assertFalse(folder.exists(), how)
            if how != "accept":
                self.assertEqual(dlg.images, [], how)

    def test_an_unreadable_temp_file_is_skipped_and_counted(self):
        dlg = self.dialog()
        self.load(dlg)
        Path(dlg._cards[0].path).unlink()
        dlg.import_button.click()
        self.assertEqual(len(dlg.images), 2)
        self.assertEqual(dlg.skipped, 1)

    def test_if_nothing_readable_is_left_the_dialog_stays_open(self):
        dlg = self.dialog()
        self.load(dlg)
        for card in dlg._cards[:3]:
            Path(card.path).unlink()
        dlg.import_button.click()
        self.assertTrue(dlg.isVisible())
        self.assertEqual(dlg.images, [])
        self.assertIn("临时文件", dlg.status.text())

    # -- parsing problems -----------------------------------------------------
    def test_an_invalid_url_is_rejected_without_a_request(self):
        dlg = self.dialog()
        dlg.url_edit.setText("ftp://nope.example/x")
        dlg.parse_button.click()
        self.assertIn("http", dlg.status.text())
        self.assertEqual(self.site.log, [])
        self.assertTrue(dlg.parse_button.isEnabled())

    def test_a_page_without_images_explains_what_to_do(self):
        self.site.add("/empty.html", "<p>nothing</p>", "text/html")
        dlg = self.dialog()
        dlg.url_edit.setText(self.site.base + "/empty.html")
        dlg.parse_button.click()
        self.assertTrue(wait_until(self.app, lambda: "JavaScript" in dlg.status.text()))
        self.assertEqual(dlg._cards, [])
        self.assertFalse(dlg.import_button.isEnabled())
        self.assertTrue(dlg.parse_button.isEnabled())

    def test_a_fetch_error_is_shown_and_parsing_can_be_retried(self):
        dlg = self.dialog()
        dlg.url_edit.setText(self.site.base + "/missing.html")
        dlg.parse_button.click()
        self.assertTrue(wait_until(self.app, lambda: "解析失败" in dlg.status.text()))
        self.assertIn("404", dlg.status.text())
        self.assertTrue(dlg.parse_button.isEnabled())
        self.load(dlg)                                                        # and a good URL now works

    def test_parsing_again_replaces_the_cards(self):
        dlg = self.dialog()
        self.load(dlg)
        # A different page: its images are not hot-link protected for the old chapter's Referer.
        self.site.add("/two.html", '<img src="/free/0.png"><img src="/free/1.png">', "text/html")
        for i in range(2):
            self.site.add(f"/free/{i}.png", png(800, 1200, "#123456"), "image/png")
        dlg.url_edit.setText(self.site.base + "/two.html")
        dlg.parse_button.click()
        self.assertTrue(wait_until(self.app, lambda: len(dlg._cards) == 2))
        self.assertTrue(wait_until(self.app, lambda: all(c.state == "loaded" for c in dlg._cards)))
        self.assertEqual(dlg.summary.text(), "已选 2 / 共 2 张")

    def test_the_initial_url_is_prefilled_and_a_scheme_is_added_on_parse(self):
        dlg = self.dialog(initial_url="example.invalid/ch1")
        self.assertEqual(dlg.url_edit.text(), "example.invalid/ch1")
        dlg.parse_button.click()
        self.assertEqual(dlg.url_edit.text(), "https://example.invalid/ch1")


if __name__ == "__main__":
    unittest.main()
