"""Human-readable dry-run rendering of an import plan (no GnuCash bindings)."""
from __future__ import annotations

from .model import Report
from .planner import Plan


def format_dry_run(reports: list[Report], plan: Plan,
                   isin_fillable=(), isin_unfillable=(), broker_name: str = "Broker") -> str:
    lines = []
    add = lines.append
    add("=" * 70)
    add("DRY RUN - Broker report import")
    add("=" * 70)
    for r in reports:
        add(f"  {r.path}")
        add(f"    account {r.account}  period {r.period}  "
            f"{len(r.trades)} trades, {len(r.coupons)} coupons")
    add("")

    if plan.commodities:
        add(f"Commodities to create ({len(plan.commodities)}):")
        for c in plan.commodities:
            add(f"  • {c.namespace}:{c.mnemonic}  [ISIN {c.isin}, fraction {c.fraction}]")
        add("")
    if plan.accounts:
        add(f"Accounts to create ({len(plan.accounts)}):")
        for a in plan.accounts:
            add(f"  • [{a.acct_type}] {a.path}")
        add("")
    if isin_fillable:
        add(f"⛔ {broker_name} commodities missing ISINs — fillable from these reports "
            f"({len(isin_fillable)}):")
        for key, name, isin in isin_fillable:
            add(f"  • {key[0]}:{name}  ← {isin}")
        add("  Run `python -m broker.backfill` before importing (prevents duplicates).")
        add("")
    if isin_unfillable:
        add(f"{broker_name} commodities missing ISINs — not in these reports "
            f"({len(isin_unfillable)}):")
        for key, name, _isin in isin_unfillable:
            add(f"  • {key[0]}:{name}")
        add("")
    if plan.warnings:
        add(f"⚠ Warnings ({len(plan.warnings)}):")
        for w in plan.warnings:
            add(f"  • {w}")
        add("")

    add("=" * 70)
    add("SUMMARY")
    add("=" * 70)
    add(f"Commodities to create:      {len(plan.commodities)}")
    add(f"Accounts to create:         {len(plan.accounts)}")
    add(f"Transactions to import:     {len(plan.transactions)}")
    add(f"Commodities w/o ISIN:       {len(isin_fillable)} fillable, {len(isin_unfillable)} other")
    add(f"Warnings:                   {len(plan.warnings)}")
    add("")
    return "\n".join(lines)
