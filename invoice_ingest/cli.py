from __future__ import annotations

import argparse
import sys
from functools import partial
from pathlib import Path

from . import ocr as ocr_mod
from .pipeline import run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="invoice_ingest",
                                 description="OCR invoice images, validate, store, report.")
    ap.add_argument("input_dir", help="directory containing invoice images")
    ap.add_argument("--db", default="invoices.db", help="SQLite file (default: invoices.db)")
    ap.add_argument("--out", default="out", help="output dir for report.json and ocr/*.txt")
    ap.add_argument("--psm", type=int, default=6, help="tesseract page segmentation mode")
    ap.add_argument("--tesseract", default="tesseract", help="tesseract executable")
    ap.add_argument("--assume-day-first", action="store_true",
                    help="read ambiguous numeric dates like 03/04/2026 as DD/MM (default: send to review)")
    a = ap.parse_args(argv)

    if not Path(a.input_dir).is_dir():
        print(f"error: {a.input_dir} is not a directory", file=sys.stderr)
        return 2
    if not ocr_mod.tesseract_available(a.tesseract):
        print("error: tesseract not found. See README for install steps.", file=sys.stderr)
        return 2

    report = run(a.input_dir, a.db, a.out,
                 partial(ocr_mod.run_tesseract, cmd=a.tesseract, psm=a.psm),
                 dayfirst=a.assume_day_first)
    for r in report["records"]:
        why = f" - {'; '.join(r['reasons'])}" if r["reasons"] else ""
        print(f"{r['source_file']:<28} {r['disposition']}{why}")
    print(f"summary: {report['summary']}  -> {Path(a.out) / 'report.json'}")
    return 0
