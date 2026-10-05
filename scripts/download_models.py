"""Download the text-detection model needed by ``manga_extract``.

The OCR model (``kha-white/manga-ocr-base``) is fetched automatically by
``manga-ocr`` on first use; only the detector has to be placed manually.

Usage:
    python scripts/download_models.py
    python scripts/download_models.py --endpoint https://hf-mirror.com
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from huggingface_hub import hf_hub_download

REPO_ID = "mayocream/comic-text-detector-onnx"
FILENAME = "comic-text-detector.onnx"
DEFAULT_ENDPOINT = os.environ.get("HF_ENDPOINT") or "https://hf-mirror.com"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                        help=f"Hugging Face endpoint/mirror (default: {DEFAULT_ENDPOINT}).")
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parents[1] / "models",
                        help="Output directory for the model file.")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    destination = args.out / FILENAME
    if destination.is_file():
        print(f"Already present: {destination}")
        return 0

    print(f"Downloading {REPO_ID}/{FILENAME} via {args.endpoint} ...")
    cached = hf_hub_download(repo_id=REPO_ID, filename=FILENAME, endpoint=args.endpoint)
    shutil.copyfile(cached, destination)
    print(f"Saved to {destination} ({destination.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
