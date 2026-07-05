"""Normalized model that every broker report parser produces.

Format-specific parsing lives in gnucashier/parsers/<broker>.py; the planner and
importer work only against these types, so adding a broker means writing a parser
that returns a `Report`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass
class Trade:
    trade_id: str          # exchange trade id (matches the cash-sheet legs)
    trade_dt: datetime     # trade date — used as the GnuCash posting date
    settle_dt: datetime
    isin: str
    asset: str             # broker's display name for the instrument
    qty: Decimal           # signed: + acquired (buy), - disposed (sell)
    price: Decimal         # as reported (% of par for bonds); informational
    amount: Decimal        # settlement amount incl. accrued interest, positive
    nkd: Decimal           # accrued coupon interest portion, always positive
    currency: str
    commission: Decimal    # always positive
    commission_currency: str

    @property
    def is_buy(self) -> bool:
        return self.qty > 0

    @property
    def principal(self) -> Decimal:
        """Clean amount without accrued interest."""
        return self.amount - self.nkd

    @property
    def unit_price(self) -> Decimal:
        """Currency per unit, derived from principal (robust to amortized bonds)."""
        return self.principal / abs(self.qty)


@dataclass
class Coupon:
    op_dt: datetime
    amount: Decimal        # positive; cash credited
    currency: str
    comment: str           # e.g. 'погашение купона <regcode> (облигации <issuer>) ...'
    subaccount: str


@dataclass
class UnknownCashOp:
    """A cash-movement row we don't model yet (surfaced so nothing is dropped)."""
    op_dt: datetime
    name: str
    comment: str
    amount: Decimal
    subaccount: str


@dataclass
class Holding:
    """A position from the report's holdings snapshot (ISIN + display name)."""
    name: str
    isin: str
    start_qty: Decimal
    end_qty: Decimal

    @property
    def held_at_start(self) -> bool:
        return self.start_qty != 0


@dataclass
class Report:
    path: str
    account: str                       # sub-account identifier, e.g. '1234567'
    currency: str                      # dominant settlement currency, e.g. 'RUB'
    period: str
    trades: list[Trade] = field(default_factory=list)
    coupons: list[Coupon] = field(default_factory=list)
    unknown_ops: list[UnknownCashOp] = field(default_factory=list)
    holdings: list[Holding] = field(default_factory=list)
    category_by_isin: dict[str, str] = field(default_factory=dict)
    # Control totals reported by the broker, for self-checking the import.
    total_cash_change: Decimal | None = None
    coupons_total: Decimal | None = None
    fees_total: Decimal | None = None
