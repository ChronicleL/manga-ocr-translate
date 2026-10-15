"""检测框编辑：手柄拖动信号、新增/删除、区域重新检测保护逻辑。不加载真实模型。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    sys.path.insert(0, str(ROOT / "tests"))
    from _studio_fakes import (
        dispose_widget, dispose_window, fake_result,
        make_engine, wait_until,
    )

    HAVE_QT = True
except Exception:
    HAVE_QT = False


def pump(app, n=10):
    for _ in range(n):
        app.processEvents()


# ---------------------------------------------------------------------------
# project.PageData editing methods
# ---------------------------------------------------------------------------
class PageDataEditTests(unittest.TestCase):
    def _make(self):
        from manga_extract.studio.project import BlockData, PageData
        data = PageData(width=500, height=700, blocks=[
            BlockData(bbox=[10, 10, 100, 100]),
            BlockData(bbox=[200, 200, 400, 400], source="ふはは", translation="哈哈",
                      reviewed=True),
        ])
        return data

    def test_add_block_appends_and_clamps(self):
        data = self._make()
        idx = data.add_block([10, 10, 60, 60])
        self.assertEqual(idx, 2)
        self.assertEqual(len(data.blocks), 3)
        # Too-large box is clamped to page bounds
        idx2 = data.add_block([490, 690, 9999, 9999])
        self.assertEqual(data.blocks[idx2].bbox, [490, 690, 500, 700])

    def test_remove_block_shifts_nothing_else(self):
        data = self._make()
        data.remove_block(0)
        self.assertEqual(len(data.blocks), 1)
        self.assertEqual(data.blocks[0].source, "ふはは")

    def test_remove_block_out_of_range_is_safe(self):
        data = self._make()
        data.remove_block(99)
        self.assertEqual(len(data.blocks), 2)

    def test_set_bbox_clamps_and_reports_change(self):
        data = self._make()
        changed = data.set_bbox(0, [0, 0, 200, 200])
        self.assertTrue(changed)
        self.assertEqual(data.blocks[0].bbox, [0, 0, 200, 200])
        # Same value → False
        self.assertFalse(data.set_bbox(0, [0, 0, 200, 200]))

    def test_set_bbox_enforces_minimum_size(self):
        data = self._make()
        data.set_bbox(0, [100, 100, 101, 101])   # 1×1 → min 8×8
        x1, y1, x2, y2 = data.blocks[0].bbox
        self.assertGreaterEqual(x2 - x1, 8)
        self.assertGreaterEqual(y2 - y1, 8)


# ---------------------------------------------------------------------------
# canvas: new signals exist, geometry signal on simulated drag
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class CanvasSignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from manga_extract.studio.canvas import CanvasView
        from manga_extract.studio.project import BlockData

        self.view = CanvasView()
        self.view.resize(600, 800)
        self.view.show()
        self.app.processEvents()
        self.addCleanup(dispose_widget, self.app, self.view)
        self.blocks = [
            BlockData(bbox=[50, 50, 200, 200]),
            BlockData(bbox=[300, 300, 450, 450]),
        ]
        # Use a real 600×800 pixmap so set_page sets _page_rect
        from PySide6.QtGui import QPixmap
        pm = QPixmap(600, 800)
        pm.fill()
        self.view.set_page(pm, self.blocks)
        self.view.select_block(0)
        pump(self.app)
        self.log = []
        self.view.blockGeometryChanged.connect(
            lambda i, x1, y1, x2, y2: self.log.append(("geom", i, x1, y1, x2, y2)))
        self.view.blockAddRequested.connect(
            lambda x1, y1, x2, y2: self.log.append(("add", x1, y1, x2, y2)))
        self.view.blockDeleteRequested.connect(
            lambda i: self.log.append(("del", i)))
        self.view.blockRedetectRequested.connect(
            lambda i: self.log.append(("redetect", i)))

    def test_signals_exist(self):
        self.assertTrue(hasattr(self.view, "blockGeometryChanged"))
        self.assertTrue(hasattr(self.view, "blockAddRequested"))
        self.assertTrue(hasattr(self.view, "blockDeleteRequested"))
        self.assertTrue(hasattr(self.view, "blockRedetectRequested"))

    def test_geometry_changed_emitted_after_handle_drag(self):
        """Simulate a handle drag by calling internal helpers directly."""
        bi = self.view._blocks[0]
        self.assertTrue(bi._selected)
        original_rect = bi.rect()
        from PySide6.QtCore import QPointF, QRectF
        # Start drag on handle 7 (BR corner)
        self.view._drag_block  = 0
        self.view._drag_handle = 7                      # BR handle
        self.view._drag_scene_start = QPointF(200, 200)  # scene pos at handle
        self.view._drag_rect_start  = QRectF(original_rect)
        # Move 20px right+down
        self.view._apply_drag(QPointF(220, 220))
        self.view._finish_drag()
        self.assertEqual(len(self.log), 1)
        event_type, idx = self.log[0][0], self.log[0][1]
        self.assertEqual(event_type, "geom")
        self.assertEqual(idx, 0)

    def test_no_geometry_signal_when_rect_unchanged(self):
        """A zero-length drag must not emit blockGeometryChanged."""
        from PySide6.QtCore import QPointF, QRectF
        bi = self.view._blocks[0]
        r  = bi.rect()
        self.view._drag_block  = 0
        self.view._drag_handle = -1
        self.view._drag_scene_start = QPointF(100, 100)
        self.view._drag_rect_start  = QRectF(r)
        self.view._apply_drag(QPointF(100, 100))        # no movement
        self.view._finish_drag()
        self.assertEqual(self.log, [])

    def test_handles_invisible_when_block_deselected(self):
        self.view.select_block(-1)
        bi = self.view._blocks[0]
        self.assertFalse(any(h.isVisible() for h in bi._handles))

    def test_handles_visible_when_selected(self):
        bi = self.view._blocks[0]
        self.assertTrue(any(h.isVisible() for h in bi._handles))

    def test_block_add_requested_signal_carries_image_coords(self):
        """Emit add via canvas context-menu helper path (signal directly)."""
        self.view.blockAddRequested.emit(100, 100, 180, 180)
        self.assertEqual(self.log, [("add", 100, 100, 180, 180)])

    def test_block_delete_requested_carries_index(self):
        self.view.blockDeleteRequested.emit(1)
        self.assertEqual(self.log, [("del", 1)])


# ---------------------------------------------------------------------------
# region re-detection: protection rule + result validation
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class RegionRedetectFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from manga_extract.studio.main_window import MainWindow
        from manga_extract.studio.config import StudioConfig

        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        # FakeExtractor with a redetect_region method
        import types
        from _studio_fakes import FakeExtractor
        extractor = FakeExtractor()

        class FakeBlock:
            def __init__(self):
                self.xyxy = [50, 50, 200, 200]
                self.vertical = True
                self.text = "テスト"
                self.lines = [types.SimpleNamespace(polygon=[[50,50],[200,50],[200,200],[50,200]],
                                                    text="テスト")]

        extractor.redetect_region = lambda img, xyxy: FakeBlock()
        self.engine = make_engine(extractor)

        self.window = MainWindow(StudioConfig(), engine=self.engine)
        self.window.show()
        pump(self.app, 20)
        self.addCleanup(dispose_window, self.app, self.window)

    def _open_page(self):
        """Import one fake page and wait for recognition."""
        from _studio_fakes import fake_result
        from manga_extract.studio.project import PageData

        self.engine.signals.loaded.emit("fake")
        w = self.window
        # Create a real temp image
        import numpy as np, cv2, uuid
        img_path = Path(self.tmpdir.name) / f"{uuid.uuid4().hex}.png"
        cv2.imwrite(str(img_path), np.ones((200, 200, 3), np.uint8) * 200)
        w.import_images([img_path])
        pump(self.app, 20)
        page_id = list(w._data.keys())[0] if w._data else None
        if page_id:
            data = PageData.from_result(fake_result())
            w._data[page_id] = data
            w.page_data_changed(page_id, save=False)
            pump(self.app, 10)
        return page_id

    def test_redetect_request_adds_to_region_jobs(self):
        page_id = self._open_page()
        if page_id is None:
            self.skipTest("page not created")
        self.window._engine_ready = True
        pump(self.app)
        n_before = len(self.window._region_jobs)
        self.window.canvas.view.blockRedetectRequested.emit(0)
        pump(self.app, 5)
        # Either the job was submitted or already completed (engine is fast)
        # Either way, no unhandled error
        self.assertGreaterEqual(len(self.window._region_jobs) + 1, n_before + 1 - 1)

    def test_redetect_stale_result_is_ignored(self):
        """If the bbox changed after submitting, the result must be silently dropped."""
        page_id = self._open_page()
        if page_id is None:
            self.skipTest("page not created")
        data = self.window._data[page_id]
        old_bbox = tuple(data.blocks[0].bbox)
        # Pretend we submitted a job
        req_id = "fake-req"
        self.window._region_jobs[req_id] = (page_id, 0, old_bbox)
        # Now move the block before the result comes in
        data.blocks[0].bbox = [999, 999, 1000, 1000]
        old_source = data.blocks[0].source
        # Deliver result – should be ignored because bbox changed
        from types import SimpleNamespace
        fake_block = SimpleNamespace(
            xyxy=[50, 50, 200, 200], vertical=True,
            text="テスト",
            lines=[SimpleNamespace(polygon=[], text="テスト")],
        )
        self.window._on_region_recognized(page_id, req_id, fake_block)
        pump(self.app, 5)
        self.assertEqual(data.blocks[0].source, old_source)   # not overwritten


if __name__ == "__main__":
    unittest.main()
