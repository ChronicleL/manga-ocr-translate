"""Text detection for manga pages.

This wraps the ``comic-text-detector`` ONNX model (YOLOv5 text-block detector +
UNet text mask + DBNet text-line head) and turns its raw outputs into:

* ``blocks`` -- text block bounding boxes with a language guess, and
* ``lines``  -- individual text-line quadrilaterals.

The post-processing mirrors the reference ``comic-text-detector`` pipeline so
that the downstream reading-order logic receives the same kind of data.

Reference model: https://github.com/dmMaze/comic-text-detector (GPL-3.0).
The ONNX export used here: https://huggingface.co/mayocream/comic-text-detector-onnx
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, cast

import cv2
import numpy as np
import onnxruntime as ort

INPUT_SIZE = 1024
CONF_THRESH = 0.4
NMS_IOU_THRESH = 0.35
SEG_THRESH = 0.3
BOX_SCORE_THRESH = 0.6
UNCLIP_RATIO = 1.5
MAX_CANDIDATES = 1000

# Index 0 -> horizontal/English text, index 1 -> vertical/Japanese text.
LANG_LIST: Tuple[str, ...] = ("eng", "ja", "unknown")

# Execution providers, most preferred first, used when ``device="auto"``.
CUDA_PROVIDER = "CUDAExecutionProvider"
DML_PROVIDER = "DmlExecutionProvider"
OPENVINO_PROVIDER = "OpenVINOExecutionProvider"

_DEVICE_PROVIDERS: Dict[str, str] = {
    "cuda": CUDA_PROVIDER,
    "dml": DML_PROVIDER,
    "openvino": OPENVINO_PROVIDER,
}
_AUTO_ORDER: Tuple[str, ...] = (CUDA_PROVIDER, DML_PROVIDER, OPENVINO_PROVIDER)


def available_devices() -> List[str]:
    """Device names this installation can actually accelerate with."""
    providers = set(ort.get_available_providers())
    devices = ["auto", "cpu"]
    devices += [name for name, provider in _DEVICE_PROVIDERS.items() if provider in providers]
    return devices


def resolve_providers(device: str = "auto") -> List[str]:
    """Return the ONNX Runtime providers to request for ``device``.

    ``auto`` prefers a GPU/accelerator provider and falls back to CPU. A
    requested but unavailable provider also falls back to CPU, so a saved
    setting can never stop the app from starting. CPU stays as a secondary
    entry so ONNX Runtime can still place nodes the accelerator rejects.
    """
    providers = set(ort.get_available_providers())
    if device == "auto":
        wanted = next((p for p in _AUTO_ORDER if p in providers), None)
    else:
        candidate = _DEVICE_PROVIDERS.get(device)
        wanted = candidate if candidate in providers else None
    if wanted is None:
        return ["CPUExecutionProvider"]
    return [wanted, "CPUExecutionProvider"]


@dataclass
class TextBlockDetection:
    """A detected text block (typically one speech bubble region)."""

    xyxy: Tuple[int, int, int, int]
    language: str
    confidence: float


@dataclass
class PageDetection:
    """Everything the detector produced for a single page."""

    width: int
    height: int
    blocks: List[TextBlockDetection]
    lines: np.ndarray          # (N, 4, 2) float32 quadrilaterals, original page coords
    line_scores: np.ndarray    # (N,) float32
    mask: np.ndarray           # (H, W) uint8 text mask at original resolution


def letterbox(
    im: np.ndarray,
    new_shape: Tuple[int, int] = (INPUT_SIZE, INPUT_SIZE),
    color: Tuple[int, int, int] = (0, 0, 0),
) -> Tuple[np.ndarray, Tuple[float, float], Tuple[int, int]]:
    """Resize ``im`` keeping the aspect ratio, padding only bottom/right."""
    shape = im.shape[:2]
    if not isinstance(new_shape, tuple):
        new_shape = (new_shape, new_shape)

    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    new_unpad = (int(round(shape[1] * r)), int(round(shape[0] * r)))
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]
    dh, dw = int(dh), int(dw)

    if shape[::-1] != new_unpad:
        im = cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR)
    im = cv2.copyMakeBorder(im, 0, dh, 0, dw, cv2.BORDER_CONSTANT, value=color)
    return im, (r, r), (dw, dh)


def _xywh2xyxy(x: np.ndarray) -> np.ndarray:
    y = np.empty_like(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2
    y[:, 1] = x[:, 1] - x[:, 3] / 2
    y[:, 2] = x[:, 0] + x[:, 2] / 2
    y[:, 3] = x[:, 1] + x[:, 3] / 2
    return y


def _nms_numpy(boxes: np.ndarray, scores: np.ndarray, iou_thres: float) -> List[int]:
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    order = scores.argsort()[::-1]
    keep: List[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thres]
    return keep


def non_max_suppression(
    prediction: np.ndarray,
    conf_thres: float = CONF_THRESH,
    iou_thres: float = NMS_IOU_THRESH,
    max_det: int = 300,
) -> np.ndarray:
    """YOLOv5-style NMS. ``prediction`` is ``(N, 5 + num_classes)``.

    Returns an ``(M, 6)`` array of ``[x1, y1, x2, y2, conf, cls]``.
    """
    if prediction.size == 0:
        return np.zeros((0, 6), dtype=np.float32)

    x = prediction[prediction[:, 4] > conf_thres]
    if x.shape[0] == 0:
        return np.zeros((0, 6), dtype=np.float32)

    x = x.copy()
    x[:, 5:] *= x[:, 4:5]  # conf = objectness * class confidence
    box = _xywh2xyxy(x[:, :4])
    conf = x[:, 5:].max(1)
    cls = x[:, 5:].argmax(1)
    x = np.concatenate([box, conf[:, None], cls[:, None].astype(np.float32)], 1)
    x = x[conf > conf_thres]
    if x.shape[0] == 0:
        return np.zeros((0, 6), dtype=np.float32)

    if x.shape[0] > 30000:
        x = x[x[:, 4].argsort()[::-1][:30000]]

    # Class-aware NMS: offset each class into its own coordinate space.
    offset = x[:, 5:6] * 4096.0
    keep = _nms_numpy(x[:, :4] + offset, x[:, 4], iou_thres)
    x = x[keep]
    if x.shape[0] > max_det:
        x = x[x[:, 4].argsort()[::-1][:max_det]]
    return x


def _order_box_points(box: np.ndarray) -> np.ndarray:
    """Order 4 points as [top-left, top-right, bottom-right, bottom-left]."""
    pts = sorted(box.tolist(), key=lambda p: p[0])
    i1, i4 = (0, 1) if pts[1][1] > pts[0][1] else (1, 0)
    i2, i3 = (2, 3) if pts[3][1] > pts[2][1] else (3, 2)
    return np.array([pts[i1], pts[i2], pts[i3], pts[i4]], dtype=np.float32)


def _box_score_fast(bitmap: np.ndarray, box: np.ndarray) -> float:
    """Mean probability inside a polygon."""
    h, w = bitmap.shape[:2]
    box = box.copy()
    xmin = int(np.clip(np.floor(box[:, 0].min()), 0, w - 1))
    xmax = int(np.clip(np.ceil(box[:, 0].max()), 0, w - 1))
    ymin = int(np.clip(np.floor(box[:, 1].min()), 0, h - 1))
    ymax = int(np.clip(np.ceil(box[:, 1].max()), 0, h - 1))
    if ymax < ymin or xmax < xmin:
        return 0.0

    masked = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
    local = box - np.array([xmin, ymin], dtype=box.dtype)
    cv2.fillPoly(masked, [local.reshape(-1, 1, 2).astype(np.int32)], 1)
    if masked.sum() == 0:
        return 0.0
    region = bitmap[ymin : ymax + 1, xmin : xmax + 1]
    return float(cv2.mean(region, masked)[0])


def _db_lines(
    prob: np.ndarray,
    unclip_ratio: float = UNCLIP_RATIO,
    box_score_thresh: float = BOX_SCORE_THRESH,
    max_candidates: int = MAX_CANDIDATES,
) -> Tuple[np.ndarray, np.ndarray]:
    """DBNet-style post-processing of the text-line probability map.

    Returns ``(boxes, scores)`` where boxes is ``(N, 4, 2)`` float32.
    """
    contours, _ = cv2.findContours(
        (prob > SEG_THRESH).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
    )

    boxes: List[np.ndarray] = []
    scores: List[float] = []
    for contour in contours[:max_candidates]:
        cont = contour.squeeze(1)
        if cont.ndim != 2 or cont.shape[0] < 4:
            continue
        (cx, cy), (w, h), angle = cv2.minAreaRect(cont)
        if min(w, h) < 2:
            continue

        score = _box_score_fast(prob, cont.astype(np.float32))
        if score < box_score_thresh:
            continue

        # Approximate pyclipper's unclip: grow the minimum-area rectangle
        # outward by area * ratio / perimeter on every side.
        distance = (w * h) * unclip_ratio / (2.0 * (w + h) + 1e-9)
        nw, nh = w + 2 * distance, h + 2 * distance
        if min(nw, nh) < 4:
            continue

        quad = cv2.boxPoints(((cx, cy), (nw, nh), angle))
        boxes.append(_order_box_points(quad))
        scores.append(score)

    if not boxes:
        return np.zeros((0, 4, 2), dtype=np.float32), np.zeros((0,), dtype=np.float32)
    return np.stack(boxes).astype(np.float32), np.asarray(scores, dtype=np.float32)


class ComicTextDetector:
    """Runs the comic-text-detector ONNX model on a page image."""

    def __init__(
        self,
        model_path: str | Path,
        providers: Optional[Sequence[str]] = None,
        input_size: int = INPUT_SIZE,
        channel_order: str = "bgr",
        conf_thresh: float = CONF_THRESH,
        nms_iou_thresh: float = NMS_IOU_THRESH,
        box_score_thresh: float = BOX_SCORE_THRESH,
        unclip_ratio: float = UNCLIP_RATIO,
        device: str = "auto",
        threads: int = 0,
    ) -> None:
        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise FileNotFoundError(f"Detector model not found: {self.model_path}")
        self.input_size = input_size
        if channel_order not in ("bgr", "rgb"):
            raise ValueError("channel_order must be 'bgr' or 'rgb'")
        self.channel_order = channel_order
        self.conf_thresh = conf_thresh
        self.nms_iou_thresh = nms_iou_thresh
        self.box_score_thresh = box_score_thresh
        self.unclip_ratio = unclip_ratio

        options = ort.SessionOptions()
        if threads and threads > 0:
            # One inference stream at a time, so inter-op parallelism only adds
            # contention; keep the budget on the intra-op pool instead.
            options.intra_op_num_threads = int(threads)
            options.inter_op_num_threads = 1
        chosen = list(providers) if providers else resolve_providers(device)
        self.session = ort.InferenceSession(
            str(self.model_path), sess_options=options, providers=chosen
        )
        self.providers = self.session.get_providers()
        self._input_name = self.session.get_inputs()[0].name
        outputs = {o.name: o for o in self.session.get_outputs()}
        self._blk_name = "blk" if "blk" in outputs else self.session.get_outputs()[0].name
        self._seg_name = "seg" if "seg" in outputs else None
        self._det_name = "det" if "det" in outputs else None

    def _preprocess(self, img_bgr: np.ndarray) -> Tuple[np.ndarray, Tuple[int, int]]:
        size = self.input_size
        # The reference implementation converts BGR->RGB and then lets
        # cv2.dnn.blobFromImage swap R/B back, so the network effectively
        # consumes BGR channel order (the default here).
        padded, _, (dw, dh) = letterbox(img_bgr, (size, size))
        chw = padded.transpose(2, 0, 1)
        if self.channel_order == "rgb":
            chw = chw[::-1]
        blob = (chw.astype(np.float32) / 255.0)[None]
        return np.ascontiguousarray(blob), (dw, dh)

    def detect(self, img_bgr: np.ndarray) -> PageDetection:
        """Detect text blocks and lines in a BGR page image."""
        im_h, im_w = img_bgr.shape[:2]
        blob, (dw, dh) = self._preprocess(img_bgr)

        # All heads of this model are dense float tensors, but the onnxruntime
        # stubs type the outputs as a union that includes SparseTensor.
        outputs = cast(List[np.ndarray], self.session.run(None, {self._input_name: blob}))
        named = {o.name: v for o, v in zip(self.session.get_outputs(), outputs)}
        blk = named[self._blk_name][0]
        seg = named[self._seg_name][0] if self._seg_name else None
        det = named[self._det_name][0] if self._det_name else None

        ratio_x = im_w / (self.input_size - dw)
        ratio_y = im_h / (self.input_size - dh)

        blocks = self._postprocess_blocks(blk, ratio_x, ratio_y, self.conf_thresh, self.nms_iou_thresh)
        lines, scores = self._postprocess_lines(
            det, ratio_x, ratio_y, self.box_score_thresh, self.unclip_ratio
        )
        mask = self._postprocess_mask(seg, im_w, im_h, dw, dh)
        return PageDetection(im_w, im_h, blocks, lines, scores, mask)

    @staticmethod
    def _postprocess_blocks(
        blk: np.ndarray,
        ratio_x: float,
        ratio_y: float,
        conf_thresh: float = CONF_THRESH,
        iou_thresh: float = NMS_IOU_THRESH,
    ) -> List[TextBlockDetection]:
        det = non_max_suppression(blk, conf_thres=conf_thresh, iou_thres=iou_thresh)
        blocks: List[TextBlockDetection] = []
        for x1, y1, x2, y2, conf, cls in det:
            x1, x2 = x1 * ratio_x, x2 * ratio_x
            y1, y2 = y1 * ratio_y, y2 * ratio_y
            cls_idx = int(cls)
            language = LANG_LIST[cls_idx] if 0 <= cls_idx < len(LANG_LIST) else "unknown"
            blocks.append(
                TextBlockDetection(
                    xyxy=(int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))),
                    language=language,
                    confidence=float(conf),
                )
            )
        return blocks

    @staticmethod
    def _postprocess_lines(
        det: Optional[np.ndarray],
        ratio_x: float,
        ratio_y: float,
        box_score_thresh: float = BOX_SCORE_THRESH,
        unclip_ratio: float = UNCLIP_RATIO,
    ) -> Tuple[np.ndarray, np.ndarray]:
        if det is None:
            return np.zeros((0, 4, 2), dtype=np.float32), np.zeros((0,), dtype=np.float32)
        prob = det[0]  # DBNet probability map
        boxes, scores = _db_lines(
            prob, unclip_ratio=unclip_ratio, box_score_thresh=box_score_thresh
        )
        if boxes.size == 0:
            return boxes, scores
        boxes[:, :, 0] *= ratio_x
        boxes[:, :, 1] *= ratio_y
        return boxes, scores

    @staticmethod
    def _postprocess_mask(
        seg: Optional[np.ndarray], im_w: int, im_h: int, dw: int, dh: int
    ) -> np.ndarray:
        if seg is None:
            return np.zeros((im_h, im_w), dtype=np.uint8)
        m = np.squeeze(seg)
        m = (m * 255).astype(np.uint8)
        m = m[: m.shape[0] - dh, : m.shape[1] - dw]
        return cv2.resize(m, (im_w, im_h), interpolation=cv2.INTER_LINEAR)
