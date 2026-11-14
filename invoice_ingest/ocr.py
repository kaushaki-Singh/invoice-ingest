"""OCR via the local Tesseract executable (no Python OCR dependency)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class OcrError(RuntimeError):
    pass


def tesseract_available(cmd: str = "tesseract") -> bool:
    return shutil.which(cmd) is not None


def run_tesseract(path: Path, cmd: str = "tesseract", psm: int = 6,
                  lang: str = "eng", timeout: int = 60) -> str:
    """Return raw OCR text for one image. psm 6 = 'single uniform block of text'."""
    try:
        proc = subprocess.run(
            [cmd, str(path), "stdout", "--psm", str(psm), "-l", lang],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrError(f"tesseract timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise OcrError(proc.stderr.strip() or f"tesseract exited {proc.returncode}")
    return proc.stdout
