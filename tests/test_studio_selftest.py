"""--selftest: the verdict file, the exit code and the argument parsing."""

from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from manga_extract.studio.bootstrap import prepare_windows_dlls

    prepare_windows_dlls()
    from PySide6.QtWidgets import QApplication

    from _studio_fakes import FakeExtractor, dispose_window, make_engine, tempdir, wait_until

    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False


class RunTests(unittest.TestCase):
    """app.run is what the launch scripts call; it must leave with main's exit code."""

    @unittest.skipUnless(HAVE_QT, "PySide6 not installed")
    def test_it_exits_with_the_code_of_main(self):
        from manga_extract.studio import app

        for returned, expected in ((3, 3), (0, 0), (None, 0)):
            with mock.patch.object(app, "main", return_value=returned),                     mock.patch.object(app.os, "_exit") as hard_exit:
                app.run(["x"])
            hard_exit.assert_called_once_with(expected)

    @unittest.skipUnless(HAVE_QT, "PySide6 not installed")
    def test_an_error_inside_main_is_not_swallowed_by_the_hard_exit(self):
        from manga_extract.studio import app

        with mock.patch.object(app, "main", side_effect=RuntimeError("boom")),                 mock.patch.object(app.os, "_exit") as hard_exit:
            with self.assertRaises(RuntimeError):
                app.run(["x"])
        hard_exit.assert_not_called()

    @unittest.skipUnless(HAVE_QT, "PySide6 not installed")
    def test_the_streams_are_flushed_first(self):
        from manga_extract.studio import app

        order = []
        fake = mock.Mock()
        fake.flush.side_effect = lambda: order.append("flush")
        with mock.patch.object(app, "main", return_value=0), mock.patch.object(app.sys, "stdout", fake),                 mock.patch.object(app.sys, "stderr", fake),                 mock.patch.object(app.os, "_exit", side_effect=lambda code: order.append("exit")):
            app.run([])
        self.assertEqual(order, ["flush", "flush", "exit"])


class ParseTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_QT, "PySide6 not installed")
    def test_the_path_after_the_flag_is_returned(self):
        from manga_extract.studio.selftest import parse_selftest

        self.assertEqual(parse_selftest(["app.exe", "--selftest", "out.txt"]), "out.txt")
        self.assertEqual(parse_selftest(["--selftest", "C:\\a b\\out.txt", "--other"]), "C:\\a b\\out.txt")

    @unittest.skipUnless(HAVE_QT, "PySide6 not installed")
    def test_no_flag_or_no_path_means_no_selftest(self):
        from manga_extract.studio.selftest import parse_selftest

        for argv in ([], ["app.exe"], ["app.exe", "--selftest"], ["app.exe", "--selftest", "--other"]):
            self.assertIsNone(parse_selftest(argv), argv)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class SelfTestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempdir(self)
        self.result = self.tmp / "result.txt"
        patcher = mock.patch("manga_extract.studio.selftest.QApplication.exit")
        self.exit = patcher.start()
        self.addCleanup(patcher.stop)

    def window(self, engine):
        from manga_extract.studio.config import StudioConfig
        from manga_extract.studio.main_window import MainWindow
        from manga_extract.translation import TranslationConfig

        self.addCleanup(engine.stop)
        window = MainWindow(StudioConfig(), engine=engine, translation_config=TranslationConfig(),
                            translate_config_path=self.tmp / "t.json")
        self.addCleanup(dispose_window, self.app, window)
        self.addCleanup(window.overlay.shutdown)
        self.addCleanup(window.translator.stop)
        return window

    def test_models_loading_is_ok_and_exits_zero(self):
        from manga_extract.studio.selftest import SelfTest

        window = self.window(make_engine(info="检测 Dml · OCR cpu"))
        test = SelfTest(window, self.result)
        self.assertTrue(wait_until(self.app, lambda: test.finished))
        self.assertEqual(self.result.read_text(encoding="utf-8"), "OK 检测 Dml · OCR cpu\n")
        self.assertEqual(test.exit_code, 0)
        self.exit.assert_called_once_with(0)

    def test_a_load_failure_is_reported_with_its_first_line_and_exits_one(self):
        from manga_extract.studio.selftest import SelfTest

        window = self.window(make_engine(load_error="model file missing\nand more detail"))
        test = SelfTest(window, self.result)
        self.assertTrue(wait_until(self.app, lambda: test.finished))
        text = self.result.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("FAIL "))
        self.assertIn("model file missing", text)
        self.assertEqual(text.count("\n"), 1)                              # one line, not the traceback
        self.assertEqual(test.exit_code, 1)
        self.exit.assert_called_once_with(1)

    def test_models_that_never_load_time_out(self):
        from manga_extract.studio.selftest import SelfTest
        from manga_extract.studio.workers import EngineWorker

        gate = threading.Event()
        engine = EngineWorker(lambda: (gate.wait(10), (FakeExtractor(), "late"))[1])
        window = self.window(engine)
        test = SelfTest(window, self.result, timeout_ms=150)
        self.assertTrue(wait_until(self.app, lambda: test.finished))
        self.assertIn("timed out", self.result.read_text(encoding="utf-8"))
        self.assertEqual(test.exit_code, 1)
        gate.set()                                                          # the models arrive afterwards ...
        wait_until(self.app, lambda: False, timeout=0.3)
        self.assertIn("timed out", self.result.read_text(encoding="utf-8"))  # ... and change nothing
        self.assertEqual(self.exit.call_count, 1)

    def test_the_first_verdict_wins(self):
        from manga_extract.studio.selftest import SelfTest

        window = self.window(make_engine())
        test = SelfTest(window, self.result)
        self.assertTrue(wait_until(self.app, lambda: test.finished))
        test._on_failed("something else")
        test._on_timeout()
        self.assertTrue(self.result.read_text(encoding="utf-8").startswith("OK"))
        self.assertEqual(self.exit.call_count, 1)

    def test_a_verdict_that_cannot_be_written_counts_as_a_failure(self):
        from manga_extract.studio.selftest import SelfTest

        folder = self.tmp / "iamafolder"
        folder.mkdir()
        window = self.window(make_engine())
        test = SelfTest(window, folder)                                     # a directory, not a file
        self.assertTrue(wait_until(self.app, lambda: test.finished))
        self.assertEqual(test.exit_code, 1)

    def test_it_does_not_touch_the_users_settings(self):
        """app.main replaces config.save while a selftest runs; the hook must not need disk."""
        from manga_extract.studio.config import StudioConfig

        config = StudioConfig()
        config.save = lambda *args, **kwargs: None
        config.save(self.tmp / "never.json")
        self.assertFalse((self.tmp / "never.json").exists())


if __name__ == "__main__":
    unittest.main()
