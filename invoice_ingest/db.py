from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .parse import ParsedInvoice, invoice_key, vendor_key

MIGRATIONS = Path(__file__).parent / "migrations"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)")
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    for sql in sorted(MIGRATIONS.glob("*.sql")):
        if sql.name not in done:
            conn.executescript(sql.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations VALUES (?)", (sql.name,))
            conn.commit()


def _upsert_scan(conn, filename, sha, text, status, reasons, invoice_id, now):
    conn.execute(
        """INSERT INTO source_scans
             (filename, sha256, ocr_text, status, reasons, invoice_id, first_seen_at, last_seen_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(filename) DO UPDATE SET
             sha256=excluded.sha256, ocr_text=excluded.ocr_text, status=excluded.status,
             reasons=excluded.reasons, invoice_id=excluded.invoice_id,
             last_seen_at=excluded.last_seen_at""",
        (filename, sha, text, status, json.dumps(reasons), invoice_id, now, now))


def save_scan(conn, filename, sha, text, status, reasons) -> None:
    """Record a scan that did NOT produce a stored invoice (needs_review)."""
    with conn:
        _upsert_scan(conn, filename, sha, text, status, reasons, None, _now())


def save_error(conn, filename, sha, message) -> None:
    """Record a failed file without clobbering an earlier good row for it."""
    now = _now()
    with conn:
        conn.execute(
            """INSERT INTO source_scans
                 (filename, sha256, ocr_text, status, reasons, invoice_id, first_seen_at, last_seen_at)
               VALUES (?,?,NULL,'error',?,NULL,?,?) ON CONFLICT(filename) DO NOTHING""",
            (filename, sha, json.dumps([message]), now, now))


def store_valid(conn, filename, sha, text, p: ParsedInvoice) -> dict:
    """Insert the invoice once; link every scan of it. One transaction.

    Dispositions: inserted | duplicate_scan | already_imported | needs_review (conflict).
    """
    now = _now()
    with conn:
        prior = conn.execute("SELECT sha256, invoice_id FROM source_scans WHERE filename=?",
                             (filename,)).fetchone()
        vkey, nkey = vendor_key(p.vendor), invoice_key(p.invoice_number)
        inv = conn.execute("SELECT * FROM invoices WHERE vendor_key=? AND invoice_number_key=?",
                           (vkey, nkey)).fetchone()
        if inv is None:
            cur = conn.execute(
                """INSERT INTO invoices (vendor, vendor_key, invoice_number, invoice_number_key,
                     invoice_date, currency, subtotal_paise, tax_paise, total_paise, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (p.vendor, vkey, p.invoice_number, nkey, p.invoice_date, p.currency,
                 p.subtotal_paise, p.tax_paise, p.total_paise, now))
            invoice_id, disposition = cur.lastrowid, "inserted"
        else:
            diffs = [f for f in ("invoice_date", "currency", "subtotal_paise", "tax_paise", "total_paise")
                     if inv[f] != getattr(p, f)]
            if diffs:  # same key, different content: never overwrite, never ignore
                reasons = [f"same vendor+invoice number as stored invoice #{inv['id']} "
                           f"but {', '.join(diffs)} differ; stored record left unchanged"]
                _upsert_scan(conn, filename, sha, text, "needs_review", reasons, None, now)
                return {"status": "needs_review", "disposition": "needs_review",
                        "reasons": reasons, "invoice_id": None, "other_sources": []}
            invoice_id = inv["id"]
            same = prior and prior["invoice_id"] == invoice_id and prior["sha256"] == sha
            disposition = "already_imported" if same else "duplicate_scan"
        _upsert_scan(conn, filename, sha, text, "valid", [], invoice_id, now)
        others = [r[0] for r in conn.execute(
            "SELECT filename FROM source_scans WHERE invoice_id=? AND filename<>? ORDER BY filename",
            (invoice_id, filename))]
    return {"status": "valid", "disposition": disposition, "reasons": [],
            "invoice_id": invoice_id, "other_sources": others}
