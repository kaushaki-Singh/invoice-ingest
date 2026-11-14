"""Tests use fixed OCR text, so they do not depend on the Tesseract version."""
import json
import pytest

from invoice_ingest import db, pipeline
from invoice_ingest.parse import parse_invoice, to_paise
from invoice_ingest.validate import validate

VALID = """Acme Supplies Pvt Ltd
INVOICE
Invoice No: INV-101
Invoice Date: 15 Mar 2026
Vendor: Acme Supplies Pvt Ltd
Widgets x 10        Rs. 1,000.00
Subtotal: Rs. 1,000.00
GST (18%): Rs. 180.00
Total: Rs. 1,180.00
"""


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    return c


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# 1. valid-field extraction + validation
def test_valid_invoice_extracted_normalised_and_valid():
    p = parse_invoice(VALID)
    assert validate(p) == []
    assert (p.invoice_number, p.vendor, p.invoice_date, p.currency) == (
        "INV-101", "Acme Supplies Pvt Ltd", "2026-03-15", "INR")
    assert (p.subtotal_paise, p.tax_paise, p.total_paise) == (100000, 18000, 118000)


def test_amounts_are_exact_paise_not_floats():
    assert to_paise("1,23,456.50") == 12345650
    assert to_paise("0.10") == 10
    assert to_paise("1.234") is None  # >2 decimals is unreadable, not rounded


def test_arithmetic_mismatch_goes_to_review():
    p = parse_invoice(VALID.replace("Total: Rs. 1,180.00", "Total: Rs. 1,190.00"))
    assert any("subtotal + tax != total" in r for r in validate(p))


# 2. duplicate / repeated import
def test_repeat_run_and_duplicate_scan_do_not_duplicate_invoices(conn):
    first = pipeline.process_text(conn, "invoice-101.png", "aaa", VALID)
    again = pipeline.process_text(conn, "invoice-101.png", "aaa", VALID)
    noisy = VALID.replace("Rs. ", "Rs.  ").replace("INV-101", "inv-101")  # rescan, spacing/case differs
    copy = pipeline.process_text(conn, "invoice-101-copy.png", "bbb", noisy)

    assert [first["disposition"], again["disposition"], copy["disposition"]] == [
        "inserted", "already_imported", "duplicate_scan"]
    assert count(conn, "invoices") == 1
    assert count(conn, "source_scans") == 2          # both scans traceable
    linked = {r[0] for r in conn.execute("SELECT invoice_id FROM source_scans")}
    assert linked == {first["invoice_id"]}
    assert copy["other_sources"] == ["invoice-101.png"]


def test_same_key_different_amounts_is_flagged_not_overwritten(conn):
    pipeline.process_text(conn, "a.png", "a", VALID)
    other = VALID.replace("1,000.00", "2,000.00").replace("1,180.00", "2,180.00")
    rec = pipeline.process_text(conn, "b.png", "b", other)
    assert rec["status"] == "needs_review" and "differ" in rec["reasons"][0]
    assert conn.execute("SELECT total_paise FROM invoices").fetchone()[0] == 118000


# 3. incomplete / ambiguous input
def test_missing_total_is_reviewed_with_reason_and_not_stored(conn):
    text = "\n".join(l for l in VALID.splitlines() if not l.startswith("Total"))
    rec = pipeline.process_text(conn, "invoice-103-review.png", "c", text)
    assert rec["status"] == "needs_review"
    assert "missing total" in rec["reasons"]
    assert count(conn, "invoices") == 0
    assert conn.execute("SELECT status FROM source_scans").fetchone()[0] == "needs_review"


def test_conflicting_values_are_not_guessed():
    p = parse_invoice(VALID + "Invoice No: INV-999\nTotal: Rs. 1,500.00\n")
    reasons = validate(p)
    assert p.invoice_number is None and p.total_paise is None
    assert any("ambiguous invoice number" in r for r in reasons)
    assert any("ambiguous total" in r for r in reasons)


def test_ambiguous_numeric_date_needs_review_unless_flag_given():
    text = VALID.replace("15 Mar 2026", "03/04/2026")
    assert any("ambiguous date" in r for r in validate(parse_invoice(text)))
    p = parse_invoice(text, dayfirst=True)
    assert validate(p) == [] and p.invoice_date == "2026-04-03"


# 4. a failing image must not stop the batch
def test_one_failed_image_does_not_stop_the_run(tmp_path):
    imgs = tmp_path / "imgs"
    imgs.mkdir()
    for name in ("a.png", "b.png", "c.png"):
        (imgs / name).write_bytes(name.encode())

    def fake_ocr(path):
        if path.name == "b.png":
            raise RuntimeError("corrupt image")
        return VALID

    report = pipeline.run(imgs, tmp_path / "t.db", tmp_path / "out", fake_ocr)
    by = {r["source_file"]: r["disposition"] for r in report["records"]}
    assert by == {"a.png": "inserted", "b.png": "error", "c.png": "duplicate_scan"}
    saved = json.loads((tmp_path / "out" / "report.json").read_text())
    assert saved["needs_review"][0]["source_file"] == "b.png"
