"""Parse Alfa-Bank / MOEX broker XLS reports into the normalized model.

Pure Python (calamine only) so it can be developed and tested without the
GnuCash bindings. Returns a `gnucashier.model.Report`.
"""
from __future__ import annotations

import re
from datetime import datetime, date
from decimal import Decimal

import python_calamine as pc

from ..model import Trade, Coupon, UnknownCashOp, Holding, Report

# Sheet names (note the leading space on the cash-movement sheet).
SHEET_TRADES = "Завершенные сделки"
SHEET_CASH = " Движение ДС"
SHEET_POSITIONS = "Динамика позиций"

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

# Position-dynamics section header -> book category (sub-account leaf).
CATEGORY_BY_SECTION = {
    "Облигации": "Bonds",
    "Акции": "Stocks",
    "Прочее": "Funds",
    "Валюта": "Currency",
}


def _dec(value) -> Decimal:
    """Exact Decimal from a calamine cell (float/int/str), via shortest repr."""
    if value is None or value == "":
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _parse_dt(value) -> datetime:
    """Parse a trade cell like '02.06.2026\\n 10:51:31' or a datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = " ".join(str(value).split())  # collapse newlines / repeated spaces
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognized datetime: {value!r}")


def _cell(row, i):
    return row[i] if i < len(row) else ""


def _rows(wb, sheet):
    return wb.get_sheet_by_name(sheet).to_python(skip_empty_area=False)


def _find_account_and_period(wb) -> tuple[str, str]:
    """Read '№ <account> от ...' and the reporting period from any sheet header."""
    account, period = "", ""
    for row in _rows(wb, SHEET_TRADES):
        for cell in row:
            text = str(cell)
            m = re.search(r"№\s*(\d{6,})", text)
            if m and not account:
                account = m.group(1)
            m = re.search(r"\d{2}\.\d{2}\.\d{4}\s*-\s*\d{2}\.\d{2}\.\d{4}", text)
            if m and not period:
                period = m.group(0)
        if account and period:
            break
    return account, period


def _parse_categories(wb) -> dict[str, str]:
    """Map ISIN -> book category from the position-dynamics sheet."""
    out: dict[str, str] = {}
    section = None
    for row in _rows(wb, SHEET_POSITIONS):
        label = str(_cell(row, 2)).strip()
        if label in CATEGORY_BY_SECTION:
            section = CATEGORY_BY_SECTION[label]
        for cell in row:
            text = str(cell).strip()
            if ISIN_RE.match(text) and section:
                out[text] = section
    return out


def _parse_holdings(wb) -> list[Holding]:
    """Positions from the position-dynamics sheet.

    Each instrument spans two rows: name + quantities, then a row with the ISIN.
    Currency rows have no ISIN row and are naturally skipped.
    """
    out: list[Holding] = []
    pending = None
    for row in _rows(wb, SHEET_POSITIONS):
        name = str(_cell(row, 6)).strip()
        if ISIN_RE.match(name) and pending is not None:
            out.append(Holding(pending[0], name, _dec(pending[1]), _dec(pending[2])))
            pending = None
        elif name and isinstance(_cell(row, 12), (int, float)):
            pending = (name, _cell(row, 12), _cell(row, 15))  # name, start qty, end qty
    return out


def _parse_trades(wb, currency_hint: str) -> list[Trade]:
    trades: list[Trade] = []
    for row in _rows(wb, SHEET_TRADES):
        c4 = str(_cell(row, 4))
        if not re.match(r"^\d{6,}", c4):
            continue
        trade_id = c4.split("\n")[0].strip()
        trades.append(Trade(
            trade_id=trade_id,
            trade_dt=_parse_dt(_cell(row, 7)),
            settle_dt=_parse_dt(_cell(row, 10)),
            isin=str(_cell(row, 12)).strip(),
            asset=str(_cell(row, 14)).strip(),
            qty=_dec(_cell(row, 16)),
            price=_dec(_cell(row, 18)),
            amount=_dec(_cell(row, 19)),
            nkd=_dec(_cell(row, 21)),
            currency=str(_cell(row, 23)).strip() or currency_hint,
            commission=_dec(_cell(row, 24)),
            commission_currency=str(_cell(row, 26)).strip() or currency_hint,
        ))
    return trades


def _parse_cash(wb, currency_hint: str) -> tuple[list[Coupon], list[UnknownCashOp]]:
    coupons: list[Coupon] = []
    unknown: list[UnknownCashOp] = []
    for row in _rows(wb, SHEET_CASH):
        name = str(_cell(row, 9)).strip()
        comment = str(_cell(row, 10)).strip()
        value = _cell(row, 6)
        amount = _cell(row, 14)
        subaccount = str(_cell(row, 26)).strip()
        if not name or not isinstance(amount, (int, float)):
            continue
        # Trade legs are reconstructed from the trades sheet; skip them here.
        if re.match(r"Расчеты по сделке \d+", name):
            continue
        if re.match(r"Комиссия по сделке \d+", name):
            continue
        if name == "НКД по сделке":
            continue
        if name == "Перевод" and comment.startswith("погашение купона"):
            coupons.append(Coupon(
                op_dt=_parse_dt(value),
                amount=_dec(amount),
                currency=currency_hint,
                comment=comment,
                subaccount=subaccount,
            ))
            continue
        # Anything else (deposits, taxes, dividends, FX, ...) — not modeled yet.
        unknown.append(UnknownCashOp(
            op_dt=_parse_dt(value),
            name=name,
            comment=comment,
            amount=_dec(amount),
            subaccount=subaccount,
        ))
    return coupons, unknown


def _parse_summary(wb) -> dict[str, Decimal]:
    """Broker control totals from the tail of the cash-movement sheet."""
    out: dict[str, Decimal] = {}
    for row in _rows(wb, SHEET_CASH):
        labels = [str(_cell(row, i)).strip() for i in range(len(row))]
        amount = _cell(row, 14)
        if not isinstance(amount, (int, float)):
            continue
        if "Итого:" in labels:
            out["total_cash_change"] = _dec(amount)
        elif "Купоны" in labels:
            out["coupons_total"] = _dec(amount)
        elif "Списано по тарифам Банка" in labels:
            out["fees_total"] = _dec(amount)
    return out


def parse_report(path: str) -> Report:
    """Parse one broker XLS file into a Report."""
    wb = pc.CalamineWorkbook.from_path(path)
    account, period = _find_account_and_period(wb)
    categories = _parse_categories(wb)
    summary = _parse_summary(wb)

    # Determine dominant settlement currency from the trades themselves.
    tmp_trades = _parse_trades(wb, currency_hint="RUB")
    currency = tmp_trades[0].currency if tmp_trades else "RUB"

    trades = _parse_trades(wb, currency_hint=currency)
    coupons, unknown = _parse_cash(wb, currency_hint=currency)
    return Report(
        path=path,
        account=account,
        currency=currency,
        period=period,
        trades=trades,
        coupons=coupons,
        unknown_ops=unknown,
        holdings=_parse_holdings(wb),
        category_by_isin=categories,
        total_cash_change=summary.get("total_cash_change"),
        coupons_total=summary.get("coupons_total"),
        fees_total=summary.get("fees_total"),
    )
