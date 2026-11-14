from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import db
from .parse import parse_invoice
from .validate import validate

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def process_text(conn, filename: str, sha: str, text: str, dayfirst: bool = False) -> dict:
    """Parse + validate + store one OCR result. Takes text, so tests need no OCR."""
    parsed = parse_invoice(text, dayfirst=dayfirst)
    reasons = validate(parsed)
    rec = {"source_file": filename, "sha256": sha, "extracted": parsed.to_dict(),
           "warnings": parsed.warnings, "raw_ocr_text": text}
    if reasons:
        db.save_scan(conn, filename, sha, text, "needs_review", reasons)
        rec.update(status="needs_review", disposition="needs_review",
                   reasons=reasons, invoice_id=None, other_sources=[])
    else:
        rec.update(db.store_valid(conn, filename, sha, text, parsed))
    return rec


def _error_record(filename, sha, message):
    return {"source_file": filename, "sha256": sha, "status": "error", "disposition": "error",
            "reasons": [message], "extracted": None, "warnings": [], "raw_ocr_text": None,
            "invoice_id": None, "other_sources": []}


def process_file(conn, path: Path, ocr: Callable[[Path], str], dayfirst: bool,
                 ocr_dir: Path | None) -> dict:
    sha = ""
    try:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        text = ocr(path)
        if ocr_dir:
            (ocr_dir / f"{path.name}.txt").write_text(text, encoding="utf-8")
        return process_text(conn, path.name, sha, text, dayfirst)
    except Exception as exc:  # one bad file must never stop the batch
        msg = f"{type(exc).__name__}: {exc}"
        try:
            db.save_error(conn, path.name, sha, msg)
        except Exception:
            pass
        return _error_record(path.name, sha, msg)


def run(input_dir, db_path, out_dir, ocr, dayfirst: bool = False) -> dict:
    files = sorted(p for p in Path(input_dir).iterdir() if p.suffix.lower() in IMAGE_EXTS)
    out = Path(out_dir)
    ocr_dir = out / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(str(db_path))
    db.migrate(conn)
    records = [process_file(conn, p, ocr, dayfirst, ocr_dir) for p in files]
    conn.close()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_dir": str(input_dir),
        "summary": dict(Counter(r["disposition"] for r in records)) | {"files": len(records)},
        "records": records,
        "needs_review": [{"source_file": r["source_file"], "status": r["status"],
                          "reasons": r["reasons"]}
                         for r in records if r["status"] != "valid"],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
    return report
