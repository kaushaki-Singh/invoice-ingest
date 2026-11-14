from __future__ import annotations

from .parse import REQUIRED, ParsedInvoice, fmt_money


def validate(p: ParsedInvoice) -> list[str]:
    """Return review reasons. Empty list == valid."""
    reasons = []
    for name in REQUIRED:
        if name in p.issues:
            reasons.append(p.issues[name])
        elif getattr(p, name) is None:
            reasons.append(f"missing {name.replace('_paise', '')}")
    if None not in (p.subtotal_paise, p.tax_paise, p.total_paise):
        if p.subtotal_paise + p.tax_paise != p.total_paise:
            reasons.append(
                f"subtotal + tax != total ({fmt_money(p.subtotal_paise)} + "
                f"{fmt_money(p.tax_paise)} != {fmt_money(p.total_paise)})")
    return reasons
