-- Money is stored as integer paise (1 INR = 100 paise); never floats.
CREATE TABLE invoices (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor             TEXT NOT NULL,
    vendor_key         TEXT NOT NULL,   -- normalised vendor (lowercase, punctuation stripped)
    invoice_number     TEXT NOT NULL,
    invoice_number_key TEXT NOT NULL,   -- uppercase alphanumerics only
    invoice_date       TEXT NOT NULL CHECK (invoice_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    currency           TEXT NOT NULL,
    subtotal_paise     INTEGER NOT NULL CHECK (subtotal_paise >= 0),
    tax_paise          INTEGER NOT NULL CHECK (tax_paise >= 0),
    total_paise        INTEGER NOT NULL CHECK (total_paise = subtotal_paise + tax_paise),
    created_at         TEXT NOT NULL,
    -- Duplicate key: one invoice number per vendor.
    UNIQUE (vendor_key, invoice_number_key)
);

-- Every scanned file is recorded, valid or not, so nothing is silently dropped.
CREATE TABLE source_scans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    filename      TEXT NOT NULL UNIQUE,
    sha256        TEXT NOT NULL,
    ocr_text      TEXT,
    status        TEXT NOT NULL CHECK (status IN ('valid', 'needs_review', 'error')),
    reasons       TEXT NOT NULL DEFAULT '[]',   -- JSON array of review reasons
    invoice_id    INTEGER REFERENCES invoices(id),
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
CREATE INDEX idx_source_scans_invoice ON source_scans(invoice_id);
