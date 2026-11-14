# invoice-ingest

Small Python CLI: OCR invoice images (Tesseract) -> extract fields -> validate ->
store valid invoices in SQLite -> write a JSON review report.

## Setup

Python 3.9+ (tested on Python 3.15.2, Windows). Runtime needs **no pip packages**; only `pytest` for tests.

Install Tesseract (the OCR engine):

| OS | Command |
|---|---|
| Windows | Install the UB-Mannheim build (`winget install --id UB-Mannheim.TesseractOCR`), keep "English" selected, add `C:\Program Files\Tesseract-OCR` to `PATH` (or pass `--tesseract "C:\Program Files\Tesseract-OCR\tesseract.exe"`) |
| Ubuntu/Debian | `sudo apt install tesseract-ocr` |
| macOS | `brew install tesseract` |

```bash
tesseract --version
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## Run

Download the four fixture PNGs into one directory (`fixtures/`), then:

```bash
python -m invoice_ingest fixtures --db invoices.db --out out_run1 --assume-day-first
python -m invoice_ingest fixtures --db invoices.db --out out_run2 --assume-day-first   # repeat run: inserts nothing new
```

Outputs: `invoices.db`, `out_run*/report.json` (records, validation status, reasons,
warnings, source filenames, raw OCR text) and `out_run*/ocr/<image>.png.txt` (raw OCR per file).
Options: `--psm N` (Tesseract layout mode, default 6), `--assume-day-first`, `--tesseract PATH`.

## Test

```bash
python -m pytest -q
```
9 tests pass. They use fixed OCR text (no Tesseract needed): valid extraction, duplicate/repeat
import, incomplete/ambiguous input, conflicting duplicate, and "one failed image doesn't stop the batch".

## Results on the supplied fixtures

* `invoice-101.png` and `invoice-102.png`: valid, stored.
* `invoice-101-copy.png`: same vendor + invoice number as 101 (`TNM-101`), stored as a duplicate scan linked to the same invoice, not a new row.
* `invoice-103-review.png`: `needs_review` (date is `[not supplied]`, and subtotal + tax != total).
* Repeat run: all valid files report `already_imported`; no new invoice rows.

## Design decisions

* **Money** is parsed with `Decimal` and stored as **integer paise**. More than 2 decimals is treated as unreadable, never rounded.
* **Duplicate key: `(vendor_key, invoice_number_key)`**, a `UNIQUE` constraint on `invoices`. Invoice numbers are only unique per vendor. Keys are normalised (case/punctuation/spacing), so `Acme Office Supplies` and `ACME OFFICE SUPPLIES` match. Date or amount is deliberately *not* in the key: a rescan with slightly different OCR should still match.
* **Same key, different content** (e.g. total differs) is *not* overwritten and *not* ignored: it goes to `needs_review` naming the stored invoice.
* **Traceability:** every file gets a row in `source_scans` (filename, sha256, OCR text, status, reasons, `invoice_id`). Both the original and the duplicate scan link to the same invoice; the report shows `other_sources`.
* **Repeatability:** re-running finds the scan by filename+hash and reports `already_imported`; no new rows. Which of two duplicate scans is inserted first depends on sorted filename order, but only one invoice row exists either way.
* **Honesty about uncertainty:** a field is either found, missing, or ambiguous/unreadable. Missing/ambiguous never get defaulted (tax is not assumed to be 0; conflicts are not resolved by picking one). Reasons are listed in `needs_review`.
* **Failure isolation:** each file is processed inside its own try/except and recorded with status `error`.
* Schema lives in `invoice_ingest/migrations/001_init.sql`, applied by a small migration runner. CHECK constraints enforce `subtotal + tax = total` in the DB too.

## Assumptions

* **Date order:** numeric dates like `01/10/2026` are ambiguous (1 Oct or 10 Jan), so by default they go to review. In `invoice-101-copy.png` the date is `01/10/2026` while the original `invoice-101.png` says `2026-10-01`, so I read the copy as DD/MM and ran with `--assume-day-first`. Without that flag the copy lands in `needs_review`.
* Labels like `Invoice/Invoice No/#`, `Vendor`, `Date`, `Subtotal`, `GST/Tax/VAT/CGST+SGST`, `Total/Grand Total/Amount Due` start their line, with the amount at the end of the line.
* Currency is read from `₹ / Rs / INR / $ / USD / € / £` anywhere in the text; 2-decimal currencies only.
* **Vendor:** uses a `Vendor:/Supplier:/From:` label if present; otherwise the first header line, with a warning in the report (visible, not blocking).

## Limitations

* Tested on the four supplied synthetic fixtures only; other layouts may need regex tweaks. Check `out_run*/ocr/*.txt` first if a valid invoice lands in review.
* No image preprocessing (deskew, upscaling, thresholding); OCR misreads of `₹` or digits (O/0, 1/l) will surface as missing/ambiguous/mismatch, not be "fixed".
* Label and value on different lines (table layouts) are not joined. Line items are not parsed. Negative amounts/credit notes unsupported.
* If OCR gives a different result on a re-run, the scan's status is updated; a failed re-run (error) does not overwrite an earlier good row.
* Only SQLite is supported.

## Where I'd add human correction and retries

* **Retries:** on `needs_review`, re-OCR with other `--psm` modes (4, 11) and a preprocessed (upscaled, grayscale, thresholded) copy; accept only if validation passes *and* the arithmetic check holds; otherwise keep the review item.
* **Human correction:** a `corrections` table (scan_id, field, corrected_value, corrected_by, at) plus a `review --apply corrections.csv` command that re-validates corrected values and inserts through the same `store_valid` path, so the duplicate rule and audit trail stay intact.
* Transient OCR errors (timeouts) could be retried with backoff before being recorded as `error`.

## Time spent, AI-tool disclosure

* **Time spent:** 1 hours.
* **Unfinished:** nothing from the required list; see Limitations for known gaps.
* **AI use:** Claude (Anthropic) was used to write the Python code, tests and this README. I am new to Python (my professional experience is React, React Native and Node.js). I installed Tesseract, downloaded the fixtures, ran the tool, read the OCR output for the invoice-101 scans, decided to use `--assume-day-first`, and ran the repeat run and tests. I can explain the code and make small changes in the walkthrough.