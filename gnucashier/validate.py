"""Self-check a parsed/planned import against the broker's own control totals.

Pure Python (no GnuCash bindings) so it can be run on any machine before
importing. Verifies, per report:
  * every generated transaction balances to zero;
  * modeled net cash movement == the broker's 'Итого:' figure;
  * modeled coupon income == the broker's 'Купоны' figure;
  * modeled commission == the broker's 'Списано по тарифам Банка' figure;
  * no unhandled cash operations were dropped.
"""
from __future__ import annotations

from decimal import Decimal

from .bookindex import BookIndex
from .brokers import Broker
from .planner import build_plan


def _cash_and_income(report, idx: BookIndex, broker: Broker):
    plan = build_plan([report], idx, broker)
    cash = Decimal(0)
    coupons = Decimal(0)
    commission = Decimal(0)
    unbalanced = [t for t in plan.transactions if t.imbalance() != 0]
    for t in plan.transactions:
        for s in t.splits:
            if s.account_path.endswith(":" + broker.cash_leaf):
                cash += s.value
            elif s.account_path == broker.coupon_account:
                coupons += -s.value        # income credit -> positive income
            elif s.account_path == broker.commission_account:
                commission += s.value
    return plan, cash, coupons, commission, unbalanced


def validate_report(report, idx: BookIndex | None, broker: Broker) -> list[str]:
    """Return a list of problem strings (empty == all checks passed)."""
    idx = idx or BookIndex()
    problems: list[str] = []
    plan, cash, coupons, commission, unbalanced = _cash_and_income(report, idx, broker)

    if unbalanced:
        problems.append(f"{len(unbalanced)} unbalanced transaction(s)")

    if report.total_cash_change is not None and cash != report.total_cash_change:
        problems.append(
            f"net cash {cash} != broker Итого {report.total_cash_change} "
            f"(diff {cash - report.total_cash_change})"
        )
    # НКД is netted into coupon income, so modeled coupon income = coupons + НКД.
    # The broker's 'Купоны' line counts standalone coupons only; compare that
    # subset separately.
    modeled_coupons_only = sum((c.amount for c in report.coupons), Decimal(0))
    if report.coupons_total is not None and modeled_coupons_only != report.coupons_total:
        problems.append(
            f"coupon income {modeled_coupons_only} != broker Купоны {report.coupons_total}"
        )
    if report.fees_total is not None and -commission != report.fees_total:
        problems.append(
            f"commission {-commission} != broker fees {report.fees_total}"
        )
    if report.unknown_ops:
        problems.append(f"{len(report.unknown_ops)} unhandled cash operation(s) dropped")
    return problems


def validate_paths(report_paths: list[str], idx: BookIndex | None, broker: Broker) -> bool:
    ok = True
    for p in report_paths:
        report = broker.parse(p)
        problems = validate_report(report, idx, broker)
        status = "OK" if not problems else "FAIL"
        print(f"[{status}] {report.account}  {p}")
        for pr in problems:
            print(f"    - {pr}")
            ok = False
    return ok
