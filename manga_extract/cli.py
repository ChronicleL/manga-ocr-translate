"""Command line interface for manga text extraction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .detector import ComicTextDetector
from .image_io import find_images, imread, imwrite
from .pipeline import MangaExtractor
from .recognizer import DEFAULT_MODEL, MangaOcrRecognizer
from .visualize import draw_result

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DETECTOR = PROJECT_ROOT / "models" / "comic-text-detector.onnx"


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manga-extract",
        description="Extract Japanese text from manga page images in reading order.",
    )
    parser.add_argument("inputs", nargs="+", help="Image files or directories.")
    parser.add_argument(
        "-o", "--output-dir", type=Path, default=None,
        help="Directory for .txt/.json/.vis.png outputs (default: alongside inputs).",
    )
    parser.add_argument(
        "--model", type=Path, default=DEFAULT_DETECTOR,
        help=f"Path to the comic-text-detector ONNX model (default: {DEFAULT_DETECTOR}).",
    )
    parser.add_argument(
        "--ocr-model", default=DEFAULT_MODEL,
        help=f"manga-ocr model name or path (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--hf-endpoint", default=None,
        help="Hugging Face endpoint/mirror used to download the OCR model, "
             "e.g. https://hf-mirror.com.",
    )
    parser.add_argument(
        "--channel-order", choices=("bgr", "rgb"), default="bgr",
        help="Channel order fed to the detector model (default: bgr).",
    )
    parser.add_argument(
        "--no-ocr", action="store_true",
        help="Only detect/order text regions; skip OCR and leave text empty.",
    )
    parser.add_argument("--json", action="store_true", help="Write a JSON result per image.")
    parser.add_argument("--vis", action="store_true", help="Write an annotated PNG per image.")
    parser.add_argument("--quiet", action="store_true", help="Do not print extracted text.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    _force_utf8_stdout()
    args = build_parser().parse_args(argv)

    if not args.model.is_file():
        print(f"error: detector model not found: {args.model}", file=sys.stderr)
        return 2

    images: List[Path] = []
    for item in args.inputs:
        images.extend(find_images(item))
    if not images:
        print("error: no images found", file=sys.stderr)
        return 2

    detector = ComicTextDetector(args.model, channel_order=args.channel_order)
    recognizer = None
    if not args.no_ocr:
        recognizer = MangaOcrRecognizer(
            model_name=args.ocr_model, hf_endpoint=args.hf_endpoint
        )
    extractor = MangaExtractor(detector=detector, recognizer=recognizer)

    for image_path in images:
        image = imread(image_path)
        result = extractor.extract_page(image, source=str(image_path))

        out_dir = args.output_dir if args.output_dir else image_path.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = image_path.stem

        (out_dir / f"{stem}.txt").write_text(result.text + "\n", encoding="utf-8")
        if args.json:
            (out_dir / f"{stem}.json").write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if args.vis:
            imwrite(out_dir / f"{stem}.vis.png", draw_result(image, result))

        if not args.quiet:
            print(f"=== {image_path} ({len(result.blocks)} blocks) ===")
            print(result.text)
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
