"""Manga text-block grouping and reading order.

Japanese manga is read top-to-bottom and right-to-left, so a naive
top-to-bottom/left-to-right sort of detected text lines produces garbage.
This module groups the detected text lines into text blocks and sorts both
the blocks and the lines inside each block into true reading order:

* Lines of vertical text are sorted from right to left (the origin used for
  the distance metric is the top-right corner of the page).
* Lines of horizontal text are sorted from top to bottom.
* Blocks are sorted with a coarse grid heuristic: the page is split into a
  3x4 grid which, for manga, is mirrored left/right so that the rightmost
  block comes first.

The approach follows the reading-order logic used by ``comic-text-detector``
and ``manga-image-translator``.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

try:  # shapely is only used for polygon intersection tests
    from shapely.geometry import Polygon as _ShapelyPolygon
except Exception:  # pragma: no cover - optional dependency
    _ShapelyPolygon = None

# Blocks whose overlap ratio with a line is above this are considered to own it.
BBOX_SCORE_THRESH = 0.4
# Minimum text-mask coverage for a block/line that has no detected block box.
MASK_SCORE_THRESH = 0.1


def _polygons_intersect(poly_a: np.ndarray, poly_b: np.ndarray) -> bool:
    if _ShapelyPolygon is None:
        return _bboxes_intersect(_bbox_of(poly_a), _bbox_of(poly_b))
    try:
        return _ShapelyPolygon(poly_a).intersects(_ShapelyPolygon(poly_b))
    except Exception:
        return _bboxes_intersect(_bbox_of(poly_a), _bbox_of(poly_b))


def _bbox_of(poly: np.ndarray) -> List[float]:
    poly = np.asarray(poly, dtype=np.float64).reshape(-1, 2)
    return [poly[:, 0].min(), poly[:, 1].min(), poly[:, 0].max(), poly[:, 1].max()]


def _bboxes_intersect(a: Sequence[float], b: Sequence[float]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _intersection_area(a: Sequence[float], b: Sequence[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 < x1 or y2 < y1:
        return -1.0
    return float((x2 - x1) * (y2 - y1))


@dataclass
class TextBlock:
    """A group of text lines that should be read together."""

    xyxy: List[int]
    lines: List[np.ndarray] = field(default_factory=list)
    language: str = "unknown"
    vertical: bool = False
    angle: int = 0
    font_size: float = -1.0
    distance: Optional[np.ndarray] = None
    vec: Optional[np.ndarray] = None
    norm: float = -1.0
    weight: float = -1.0
    merged: bool = False
    texts: List[str] = field(default_factory=list)
    text: str = ""

    def __post_init__(self) -> None:
        self.xyxy = [int(v) for v in self.xyxy]

    def lines_array(self) -> np.ndarray:
        return np.array(self.lines, dtype=np.float64).reshape(-1, 4, 2)

    def sort_lines(self) -> None:
        if self.distance is None or len(self.lines) < 2:
            return
        order = np.argsort(self.distance)
        self.distance = self.distance[order]
        self.lines = [self.lines[i] for i in order]

    def adjust_bbox(self, with_bbox: bool = False) -> None:
        if not self.lines:
            return
        arr = self.lines_array()
        x1, x2 = arr[..., 0].min(), arr[..., 0].max()
        y1, y2 = arr[..., 1].min(), arr[..., 1].max()
        if with_bbox:
            x1, y1 = min(x1, self.xyxy[0]), min(y1, self.xyxy[1])
            x2, y2 = max(x2, self.xyxy[2]), max(y2, self.xyxy[3])
        self.xyxy = [int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))]

    def center(self) -> Tuple[float, float]:
        return ((self.xyxy[0] + self.xyxy[2]) / 2, (self.xyxy[1] + self.xyxy[3]) / 2)

    def __len__(self) -> int:
        return len(self.lines)


@dataclass
class TextLine:
    """A single OCR-able text line."""

    polygon: np.ndarray
    score: float = 1.0
    text: str = ""


def _rect_polygon(x1: float, y1: float, x2: float, y2: float) -> np.ndarray:
    return np.array(
        [[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float64
    )


def _examine_block(block: TextBlock, im_w: int, im_h: int, sort: bool = False) -> None:
    """Determine text orientation and compute per-line reading distances."""
    lines = block.lines_array()
    n = lines.shape[0]

    # Midpoints of each polygon edge; opposite midpoints give the two axes.
    mids = (lines[:, [1, 2, 3, 0]] + lines) / 2
    vec_v = mids[:, 2] - mids[:, 0]  # along the "vertical" axis of each line box
    vec_h = mids[:, 1] - mids[:, 3]  # along the "horizontal" axis of each line box
    centers = (lines[:, 0] + lines[:, 2]) / 2  # line centers

    v = vec_v.sum(axis=0)
    h = vec_h.sum(axis=0)
    norm_v, norm_h = float(np.linalg.norm(v)), float(np.linalg.norm(h))

    # If the summed vertical extent dominates, the text is written vertically.
    if block.language == "ja":
        vertical = norm_v > norm_h
    else:
        vertical = norm_v > norm_h * 2

    if vertical:
        primary_vec, primary_norm = v, norm_v
        origin = np.array([[im_w, 0]], dtype=np.float64)  # vertical text starts top-right
        font_size = int(round(norm_h / n))
    else:
        primary_vec, primary_norm = h, norm_h
        origin = np.array([[0, 0]], dtype=np.float64)
        font_size = int(round(norm_v / n))

    angle = int(math.degrees(math.atan2(primary_vec[1], primary_vec[0])))
    delta = centers - origin
    distance = np.linalg.norm(delta, axis=1)
    denom = np.maximum(distance * primary_norm, 1e-9)
    angle_between = np.arccos(np.clip((delta @ primary_vec) / denom, -1.0, 1.0))
    distance = np.abs(np.sin(angle_between) * distance)

    if vertical:
        angle -= 90
    if abs(angle) < 3:
        angle = 0

    block.vertical = bool(vertical)
    block.angle = angle
    block.font_size = float(font_size)
    block.vec = primary_vec.astype(np.float64)
    block.norm = primary_norm
    block.distance = distance
    if sort:
        block.sort_lines()


def _try_merge_line(block: TextBlock, other: TextBlock, fntsize_tol: float = 1.3,
                    distance_tol: float = 2.0) -> bool:
    """Try to append the last line of ``other`` onto ``block``."""
    if other.merged or not block.lines or not other.lines:
        return False
    if block.font_size <= 0 or other.font_size <= 0:
        return False

    fntsize_div = block.font_size / other.font_size
    n1, n2 = len(block.lines), len(other.lines)
    fntsz_avg = (block.font_size * n1 + other.font_size * n2) / (n1 + n2)

    # Filled in by _examine_block; without them the geometry is unknown.
    vec, other_vec = block.vec, other.vec
    block_distance, other_distance = block.distance, other.distance
    if vec is None or other_vec is None or block_distance is None or other_distance is None:
        return False

    vec_prod = float(vec @ other_vec)
    vec_sum = vec + other_vec
    cos_vec = vec_prod / max(block.norm * other.norm, 1e-9)
    distance = float(other_distance[-1] - block_distance[-1])
    distance_p1 = float(
        np.linalg.norm(np.asarray(other.lines[-1][0]) - np.asarray(block.lines[-1][0]))
    )

    if not _polygons_intersect(block.lines[-1], other.lines[-1]):
        if fntsize_div > fntsize_tol or 1 / fntsize_div > fntsize_tol:
            return False
        if abs(cos_vec) < 0.866:  # cos(30 deg)
            return False
        if distance > distance_tol * fntsz_avg or distance_p1 > fntsz_avg * 2.5:
            return False

    block.lines.append(other.lines[0])
    block.vec = vec_sum
    block.angle = int(round(math.degrees(math.atan2(vec_sum[1], vec_sum[0]))))
    if block.vertical:
        block.angle -= 90
    block.norm = float(np.linalg.norm(vec_sum))
    block.distance = np.append(block_distance, other_distance[-1])
    block.font_size = fntsz_avg
    other.merged = True
    return True


def _merge_lines(block_list: List[TextBlock]) -> List[TextBlock]:
    if len(block_list) < 2:
        return block_list
    block_list = sorted(block_list, key=lambda b: b.distance[0] if b.distance is not None else 0.0)
    merged: List[TextBlock] = []
    for i, current in enumerate(block_list):
        if current.merged:
            continue
        for other in block_list[i + 1:]:
            _try_merge_line(current, other)
        merged.append(current)
    for block in merged:
        block.adjust_bbox(with_bbox=False)
    return merged


def _split_block(block: TextBlock) -> Tuple[bool, List[TextBlock]]:
    """Split a block when consecutive lines are separated by a large gap."""
    font_size, distance = block.font_size, block.distance
    if distance is None:  # no per-line distances yet, so nothing to compare
        return False, [block]
    first = np.array(block.lines[0])
    lines = sorted(block.lines, key=lambda ln: float(np.linalg.norm(np.array(ln[0]) - first[0])))

    distance_tol = font_size * 2
    current = copy.deepcopy(block)
    current.lines = [first]
    sub_blocks = [current]
    split_any = False

    for j, line in enumerate(lines[1:]):
        is_split = False
        if not _polygons_intersect(lines[j], line):
            line_distance = abs(distance[j + 1] - distance[j])
            if line_distance > distance_tol:
                is_split = True
            elif block.vertical and abs(block.angle) < 15:
                if len(current.lines) > 1 or line_distance > font_size:
                    is_split = abs(lines[j][0][1] - line[0][1]) > font_size
        if is_split:
            current = copy.deepcopy(current)
            current.lines = [line]
            sub_blocks.append(current)
        else:
            current.lines.append(line)

    if len(sub_blocks) > 1:
        split_any = True
        for sub in sub_blocks:
            sub.adjust_bbox(with_bbox=False)
    return split_any, sub_blocks


def _sort_blocks(block_list: List[TextBlock], im_w: int, im_h: int) -> List[TextBlock]:
    """Sort blocks into manga reading order using a mirrored 3x4 grid."""
    if not block_list:
        return block_list

    num_ja = sum(1 for b in block_list if b.language == "ja")
    xyxy = np.array([b.xyxy for b in block_list], dtype=np.float64)

    # For mostly-Japanese pages, mirror the horizontal axis so that the
    # rightmost blocks sort first (manga is read right-to-left).
    flip_lr = num_ja > len(block_list) / 2

    im_oriw = im_w
    eff_w = im_w / 2 if im_w > im_h else im_w

    num_gridy, num_gridx = 4, 3
    img_area = im_h * eff_w

    center_x = (xyxy[:, 0] + xyxy[:, 2]) / 2
    if flip_lr:
        if eff_w != im_oriw:
            center_x = im_oriw - center_x
        else:
            center_x = eff_w - center_x

    grid_x = (center_x / eff_w * num_gridx).astype(np.int32)
    center_y = (xyxy[:, 1] + xyxy[:, 3]) / 2
    grid_y = (center_y / im_h * num_gridy).astype(np.int32)

    grid_indices = grid_y * num_gridx + grid_x
    weights = (
        grid_indices * img_area
        + 1.2 * (center_x - grid_x * eff_w / num_gridx)
        + (center_y - grid_y * im_h / num_gridy)
    )
    if eff_w != im_oriw:
        weights[np.where(grid_x >= num_gridx)] += img_area * num_gridy * num_gridx

    for block, weight in zip(block_list, weights):
        block.weight = float(weight)
    return sorted(block_list, key=lambda b: b.weight)


def group_text_lines(
    blocks: Sequence,
    lines: np.ndarray,
    line_scores: np.ndarray,
    mask: Optional[np.ndarray],
    im_w: int,
    im_h: int,
    sort_blocks: bool = True,
) -> List[TextBlock]:
    """Group detected lines into ordered text blocks.

    ``blocks`` is a sequence of detector block detections (objects exposing
    ``xyxy`` and ``language``), ``lines`` is an ``(N, 4, 2)`` array of text-line
    quadrilaterals and ``mask`` is an optional text-segmentation mask.
    """
    block_list = [
        TextBlock(xyxy=list(b.xyxy), language=getattr(b, "language", "unknown"))
        for b in blocks
    ]
    scattered = {"ver": [], "hor": []}
    lines = np.asarray(lines, dtype=np.float64).reshape(-1, 4, 2)

    # Step 1: assign each detected line to the text block it overlaps most.
    for line in lines:
        bx = line[:, 0]
        by = line[:, 1]
        bx1, bx2 = int(bx.min()), int(bx.max())
        by1, by2 = int(by.min()), int(by.max())
        line_area = max((by2 - by1) * (bx2 - bx1), 1)

        bbox_score, bbox_idx = -1.0, -1
        for j, block in enumerate(block_list):
            score = _intersection_area(block.xyxy, [bx1, by1, bx2, by2]) / line_area
            if score > bbox_score:
                bbox_score, bbox_idx = score, j

        if bbox_score > BBOX_SCORE_THRESH:
            block_list[bbox_idx].lines.append(line)
            continue

        # No block claimed the line: keep it only if the text mask agrees.
        if mask is not None:
            patch = mask[by1:by2, bx1:bx2]
            if patch.size and patch.mean() / 255 < MASK_SCORE_THRESH:
                continue
        block = TextBlock([bx1, by1, bx2, by2], lines=[line])
        _examine_block(block, im_w, im_h, sort=False)
        scattered["ver" if block.vertical else "hor"].append(block)

    # Step 2: orient each block, sort its lines, and split on large gaps.
    final_blocks: List[TextBlock] = []
    for block in block_list:
        if len(block.lines) == 0:
            bx1, by1, bx2, by2 = block.xyxy
            if mask is not None:
                patch = mask[by1:by2, bx1:bx2]
                if patch.size == 0 or patch.mean() / 255 < MASK_SCORE_THRESH:
                    continue
            block.lines = [_rect_polygon(bx1, by1, bx2, by2)]

        _examine_block(block, im_w, im_h, sort=True)

        should_split = len(block.lines) > 1 and (
            block.language == "ja" or block.vertical
        )
        if should_split:
            did_split, sub_blocks = _split_block(block)
        else:
            did_split, sub_blocks = False, [block]
        if not did_split:
            for sub in sub_blocks:
                sub.adjust_bbox(with_bbox=True)
        final_blocks.extend(sub_blocks)

    # Step 3: re-attach scattered lines, then sort everything into reading order.
    final_blocks.extend(_merge_lines(scattered["hor"]))
    final_blocks.extend(_merge_lines(scattered["ver"]))
    if sort_blocks:
        final_blocks = _sort_blocks(final_blocks, im_w, im_h)
    return final_blocks
