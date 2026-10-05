"""Debug visualization of extraction results."""

from __future__ import annotations

import cv2
import numpy as np

from .pipeline import PageResult


def draw_result(image: np.ndarray, page: PageResult, show_text: bool = True) -> np.ndarray:
    """Draw text blocks, lines and their reading-order indices on a copy."""
    vis = image.copy()
    scale = max(round(sum(vis.shape[:2]) / 2 * 0.002), 1)

    palette = [(0, 200, 0), (0, 160, 255), (200, 0, 200), (255, 120, 0)]

    for block_idx, block in enumerate(page.blocks):
        color = palette[block_idx % len(palette)]
        x1, y1, x2, y2 = block.xyxy
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2 * scale)

        for line_idx, line in enumerate(block.lines):
            poly = np.asarray(line.polygon, dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(vis, [poly], True, (0, 0, 255), max(scale, 1))
            anchor = poly.reshape(-1, 2)[0]
            cv2.putText(
                vis, f"{block_idx}.{line_idx}",
                (int(anchor[0]), int(anchor[1]) - 3),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45 * scale, (255, 0, 0), max(scale, 1),
                cv2.LINE_AA,
            )

        label = f"#{block_idx} {'V' if block.vertical else 'H'}"
        cv2.putText(
            vis, label, (x1, max(y1 - 6, 14)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6 * scale, color, 2 * scale, cv2.LINE_AA,
        )
        if show_text and block.text:
            cv2.putText(
                vis, repr(block.text), (x1, min(y2 + 18 * scale, vis.shape[0] - 4)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (0, 0, 0), 3 * scale, cv2.LINE_AA,
            )
            cv2.putText(
                vis, repr(block.text), (x1, min(y2 + 18 * scale, vis.shape[0] - 4)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (255, 255, 255), scale, cv2.LINE_AA,
            )
    return vis
