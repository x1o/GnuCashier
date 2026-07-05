"""One-time ISIN backfill for existing book commodities.

Reads broker report(s), builds a name -> ISIN map from their trades and holdings,
and sets the ISIN (GnuCash 'cusip' / cmdty:xcode) on any book commodity that is
missing one and whose name matches — tolerating case, punctuation, and
Latin/Cyrillic homoglyphs. Run once; re-running is a no-op for filled commodities.
After this, the importer matches instruments by ISIN and won't create duplicates.

    gnucashier backfill [--broker <name>] <book.gnucash> <report.zip | .xls ...>
"""
from .bookindex import norm_name


def maps_from_reports(reports):
    """name -> ISIN maps (exact and homoglyph-normalized) from trades + holdings."""
    exact, norm = {}, {}
    for report in reports:
        pairs = ([(t.asset, t.isin) for t in report.trades]
                 + [(h.name, h.isin) for h in report.holdings])
        for name, isin in pairs:
            if isin:
                exact.setdefault(name, isin)
                norm.setdefault(norm_name(name), isin)
    return exact, norm


def build_isin_maps(report_paths, broker):
    return maps_from_reports([broker.parse(p) for p in report_paths])


def _lookup(exact, norm, *names):
    for n in names:
        if n and n in exact:
            return exact[n]
    for n in names:
        if n and norm_name(n) in norm:
            return norm[norm_name(n)]
    return None


def audit_missing_isins(idx, reports, broker):
    """This broker's security commodities missing an ISIN.

    Returns (fillable, unfillable), each a list of (key, mnemonic, isin_or_None):
    'fillable' can be tagged from these reports (the real duplicate risk),
    'unfillable' are held instruments not present in these reports.
    """
    exact, norm = maps_from_reports(reports)
    fillable, unfillable = [], []
    for key in sorted(idx.security_commodity_keys_under(broker.broker_root)):
        if idx.commodity_has_xcode(key):
            continue
        info = idx.commodity(key)
        if info is None:
            continue
        isin = _lookup(exact, norm, info.mnemonic, info.fullname)
        (fillable if isin else unfillable).append((key, info.mnemonic, isin))
    return fillable, unfillable


def run(book_file, report_paths, broker, confirm=True):
    from gnucash import Session, SessionOpenMode  # bindings only needed to apply

    exact, norm = build_isin_maps(report_paths, broker)
    session = Session(book_file, SessionOpenMode.SESSION_NORMAL_OPEN)
    try:
        table = session.book.get_table()
        planned = []  # (commodity, label, isin)
        for ns_obj in table.get_namespaces_list():
            if ns_obj.get_name() in ("CURRENCY", "template"):
                continue
            for c in ns_obj.get_commodity_list():
                if c.get_cusip():  # already has an ISIN
                    continue
                isin = (exact.get(c.get_mnemonic()) or exact.get(c.get_fullname())
                        or norm.get(norm_name(c.get_mnemonic()))
                        or norm.get(norm_name(c.get_fullname())))
                if isin:
                    planned.append((c, f"{c.get_namespace()}:{c.get_mnemonic()}", isin))

        print("=" * 70)
        print(f"ISIN backfill ({broker.name}) — {len(planned)} commodity(ies) to update")
        print("=" * 70)
        for _c, label, isin in planned:
            print(f"  • {label}  ← {isin}")
        if not planned:
            print("Nothing to backfill.")
            return
        if confirm:
            print("\nApply these ISINs? (yes/no): ", end="")
            if input().strip().lower() not in ("yes", "y"):
                print("Cancelled.")
                return
        for c, _label, isin in planned:
            c.set_cusip(isin)
        session.save()
        print(f"✓ Backfilled {len(planned)} ISIN(s) and saved.")
    finally:
        session.end()
        session.destroy()
