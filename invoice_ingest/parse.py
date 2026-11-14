"""Field extraction from raw OCR text. Pure functions: no I/O, no database.

Principle: if a value is absent we leave it None; if it is present but
conflicting/unreadable we leave it None AND record an entry in `issues`.
We never guess a value to make validation pass.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

REQUIRED = ("invoice_number", "vendor", "invoice_date", "currency",
            "subtotal_paise", "tax_paise", "total_paise")


def fmt_money(paise: int | None) -> str | None:
    return None if paise is None else f"{paise // 100}.{paise % 100:02d}"


@dataclass
class ParsedInvoice:
    invoice_number: str | None = None
    vendor: str | None = None
    invoice_date: str | None = None      # ISO yyyy-mm-dd
    currency: str | None = None          # ISO 4217 code
    subtotal_paise: int | None = None
    tax_paise: int | None = None
    total_paise: int | None = None
    issues: dict = field(default_factory=dict)     # field -> why it is ambiguous/unreadable
    warnings: list = field(default_factory=list)   # non-blocking notes

    def to_dict(self) -> dict:
        return {
            "invoice_number": self.invoice_number, "vendor": self.vendor,
            "invoice_date": self.invoice_date, "currency": self.currency,
            "subtotal": fmt_money(self.subtotal_paise), "subtotal_paise": self.subtotal_paise,
            "tax": fmt_money(self.tax_paise), "tax_paise": self.tax_paise,
            "total": fmt_money(self.total_paise), "total_paise": self.total_paise,
        }


# ---------------------------------------------------------------- normalisation
def invoice_key(number: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", number.upper())


def vendor_key(vendor: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", vendor.lower()).strip()


def to_paise(token: str) -> int | None:
    """'1,23,456.50' -> 12345650. Returns None if not a clean amount (>2 decimals etc.)."""
    try:
        d = Decimal(token.replace(",", "").rstrip(","))
    except InvalidOperation:
        return None
    if d != d.quantize(Decimal("0.01")):
        return None
    return int((d * 100).to_integral_value())


# ---------------------------------------------------------------- invoice number
_NUM_PATTERNS = [
    re.compile(r"\binvoice\s*(?:(?:number|num|no|id)\b\.?|#)\s*[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/_]*)", re.I),
    re.compile(r"\binv(?:oice)?\s*[#:]\s*([A-Za-z0-9][A-Za-z0-9\-/_]*)", re.I),
]


def _invoice_number(lines, issues):
    found = {}
    for line in lines:
        for pat in _NUM_PATTERNS:
            for m in pat.finditer(line):
                val = m.group(1).strip("-/_").upper()
                if any(c.isdigit() for c in val):
                    found[invoice_key(val)] = val
    if len(found) > 1:
        issues["invoice_number"] = "ambiguous invoice number: " + ", ".join(sorted(found.values()))
        return None
    return next(iter(found.values()), None)


# ---------------------------------------------------------------- vendor
_VENDOR_LABEL = re.compile(
    r"^\W*(?:vendor(?:\s+name)?|supplier(?:\s+name)?|seller|sold\s+by|bill(?:ed)?\s+from|from)"
    r"\s*[:\-]\s*(\S.*)$", re.I)
_NOT_A_NAME = re.compile(r"^\W*(?:tax\s+)?invoice\b|^\W*bill\b", re.I)


def _vendor(lines, issues, warnings):
    labelled = {}
    for line in lines:
        m = _VENDOR_LABEL.match(line)
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip(" .,;:")
            if name:
                labelled[vendor_key(name)] = name
    if len(labelled) > 1:
        issues["vendor"] = "ambiguous vendor: " + " | ".join(sorted(labelled.values()))
        return None
    if labelled:
        return next(iter(labelled.values()))
    # Fallback: letterhead = first text line. Recorded as a warning, never silent.
    for line in lines:
        if _NOT_A_NAME.match(line) or ":" in line or len(re.findall(r"[A-Za-z]", line)) < 2:
            continue
        warnings.append(f"vendor taken from first header line (no 'Vendor:' label): {line!r}")
        return re.sub(r"\s+", " ", line).strip(" .,;:")
    return None


# ---------------------------------------------------------------- date
_MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_DATE_LABEL = re.compile(
    r"\b(?:invoice\s*date|date\s*of\s*issue|issue\s*date|dated?)\b\s*[:\-]?\s*(\S.*)$", re.I)


def _mk(y, m, d, raw):
    try:
        return date(y, m, d).isoformat(), None
    except ValueError:
        return None, f"invalid calendar date {raw!r}"


def parse_date(s: str, dayfirst: bool = False):
    """Return (iso_date, None) or (None, reason)."""
    s = s.strip()
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        return _mk(y, mo, d, s)
    m = re.search(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", s)
    if m:
        a, b, y = map(int, m.groups())
        if a > 12:
            d, mo = a, b
        elif b > 12:
            mo, d = a, b
        elif a == b or dayfirst:
            d, mo = a, b
        else:
            return None, (f"ambiguous date {s!r}: day/month order unclear "
                          "(rerun with --assume-day-first if DD/MM)")
        return _mk(y, mo, d, s)
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3,9})\.?,?[\s\-]+(\d{4})", s)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return _mk(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)), s)
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", s)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return _mk(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)), s)
    return None, f"unparseable date {s!r}"


def _date(lines, issues, dayfirst):
    found, problems = set(), []
    for line in lines:
        if "due" in line.lower():
            continue
        m = _DATE_LABEL.search(line)
        if m:
            iso, why = parse_date(m.group(1), dayfirst)
            (found.add(iso) if iso else problems.append(why))
    if problems:
        issues["invoice_date"] = "; ".join(problems)
        return None
    if len(found) > 1:
        issues["invoice_date"] = "ambiguous invoice date: " + ", ".join(sorted(found))
        return None
    return next(iter(found), None)


# ---------------------------------------------------------------- currency
_CURRENCIES = [
    ("INR", re.compile(r"₹|\bINR\b|\bRs\b|\bRupees?\b", re.I)),
    ("USD", re.compile(r"\$|\bUSD\b", re.I)),
    ("EUR", re.compile(r"€|\bEUR\b", re.I)),
    ("GBP", re.compile(r"£|\bGBP\b", re.I)),
]


def _currency(text, issues):
    found = [code for code, pat in _CURRENCIES if pat.search(text)]
    if len(found) > 1:
        issues["currency"] = "ambiguous currency: " + ", ".join(found)
        return None
    return found[0] if found else None


# ---------------------------------------------------------------- amounts
_SUB = re.compile(r"^\W*sub\s*-?\s*total\b", re.I)
_TAX = re.compile(r"^\W*(?:total\s+)?(cgst|sgst|igst|gst|vat|tax)\b", re.I)
_TOTAL = re.compile(r"^\W*(?:grand\s+total|total\s+(?:amount|due|payable)|amount\s+(?:due|payable)|total)\b", re.I)


def _last_amount(line):
    cleaned = re.sub(r"\d+(?:\.\d+)?\s*%", " ", line)       # drop "18%" rates
    toks = re.findall(r"\d[\d,]*(?:\.\d+)?", cleaned)
    return toks[-1].rstrip(",") if toks else None


def _distinct(name, values, issues):
    """Collapse repeated labelled values; flag unreadable or conflicting ones."""
    if any(v is None for v in values):
        issues[name] = f"unreadable {name.replace('_paise', '')} amount"
        return None
    vals = set(values)
    if len(vals) > 1:
        issues[name] = (f"ambiguous {name.replace('_paise', '')}: multiple values "
                        + ", ".join(fmt_money(v) for v in sorted(vals)))
        return None
    return next(iter(vals), None)


def _amounts(lines, issues):
    sub, total = [], []
    tax = {"cgst": [], "sgst": [], "other": []}
    for line in lines:
        tok = _last_amount(line)
        if tok is None:
            continue
        val = to_paise(tok)
        if _SUB.match(line):
            sub.append(val)
        elif "invoice" not in line.lower() and (m := _TAX.match(line)):
            kind = m.group(1).lower()
            tax[kind if kind in ("cgst", "sgst") else "other"].append(val)
        elif _TOTAL.match(line):
            total.append(val)

    probe = {}
    s, t = _distinct("subtotal_paise", sub, issues), _distinct("total_paise", total, issues)
    if tax["cgst"] or tax["sgst"]:
        c = _distinct("tax_paise", tax["cgst"], probe) if tax["cgst"] else None
        g = _distinct("tax_paise", tax["sgst"], probe) if tax["sgst"] else None
        o = _distinct("tax_paise", tax["other"], probe) if tax["other"] else None
        if "tax_paise" in probe or c is None or g is None:
            issues["tax_paise"] = probe.get("tax_paise", "tax split incomplete: only one of CGST/SGST found")
            x = None
        elif o is not None and o != c + g:
            issues["tax_paise"] = "ambiguous tax: CGST+SGST differs from stated total tax"
            x = None
        else:
            x = c + g
    else:
        x = _distinct("tax_paise", tax["other"], issues)
    return s, x, t
def parse_invoice(text: str, dayfirst: bool = False) -> ParsedInvoice:
    text = text.replace("\u00a0", " ")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    p = ParsedInvoice()
    p.invoice_number = _invoice_number(lines, p.issues)
    p.vendor = _vendor(lines, p.issues, p.warnings)
    p.invoice_date = _date(lines, p.issues, dayfirst)
    p.currency = _currency(text, p.issues)
    p.subtotal_paise, p.tax_paise, p.total_paise = _amounts(lines, p.issues)
    return p
