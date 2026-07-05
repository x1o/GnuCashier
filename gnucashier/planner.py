"""Turn parsed broker reports into a book-neutral import plan.

The plan (commodities/accounts to create and fully-formed transactions with
balanced splits) is executed by broker/importer.py against a live GnuCash
session. Kept free of the GnuCash bindings so it runs on the Mac.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .bookindex import BookIndex, CommodityKey
from .brokers import Broker
from .model import Report, Trade, Coupon


@dataclass
class CommoditySpec:
    namespace: str
    mnemonic: str
    fullname: str
    isin: str
    fraction: int


@dataclass
class AccountSpec:
    path: str
    acct_type: str                 # 'STOCK' | 'MUTUAL'
    commodity_key: CommodityKey


@dataclass
class SplitSpec:
    account_path: str
    value: Decimal                 # in the transaction currency
    quantity: Decimal              # in the account's commodity (== value for cash)
    memo: str = ""


@dataclass
class TransactionSpec:
    date: datetime
    description: str
    num: str
    currency: str
    splits: list[SplitSpec]

    def imbalance(self) -> Decimal:
        return sum((s.value for s in self.splits), Decimal(0))


@dataclass
class Plan:
    commodities: list[CommoditySpec] = field(default_factory=list)
    accounts: list[AccountSpec] = field(default_factory=list)
    transactions: list[TransactionSpec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _Planner:
    def __init__(self, idx: BookIndex, broker: Broker):
        self.idx = idx
        self.broker = broker
        self.plan = Plan()
        self._new_commodities: dict[CommodityKey, CommoditySpec] = {}
        self._new_accounts: dict[str, AccountSpec] = {}

    # ---- instrument resolution ----
    def _category(self, report: Report, trade: Trade) -> str:
        cat = report.category_by_isin.get(trade.isin)
        if cat in ("Bonds", "Funds", "Stocks"):
            return cat
        return "Bonds" if trade.nkd > 0 else "Stocks"

    def _resolve_commodity(self, isin: str, asset: str, category: str) -> CommodityKey:
        # ISIN backfill on existing commodities is a separate one-time step
        # (broker/backfill.py); here we only match and, if unknown, create.
        key = self.idx.commodity_by_isin(isin) or self.idx.commodity_by_name(asset)
        if key:
            return key
        # brand new
        key = (self.broker.commodity_namespace, asset)
        if key not in self._new_commodities:
            spec = CommoditySpec(
                namespace=self.broker.commodity_namespace,
                mnemonic=asset,
                fullname=asset,
                isin=isin,
                fraction=(self.broker.bond_fraction if category == "Bonds"
                          else self.broker.default_fraction),
            )
            self._new_commodities[key] = spec
            self.plan.commodities.append(spec)
        return key

    def _resolve_security_account(self, base: str, category: str, key: CommodityKey) -> str:
        existing = self.idx.find_security_account(base, key)
        if existing:
            return existing
        # already planned in this run?
        for path, spec in self._new_accounts.items():
            if spec.commodity_key == key and path.startswith(base + ":"):
                return path
        path = f"{base}:{category}:{key[1]}"
        acct_type = "MUTUAL" if category == "Funds" else "STOCK"
        spec = AccountSpec(path, acct_type, key)
        self._new_accounts[path] = spec
        self.plan.accounts.append(spec)
        return path

    def _require_account(self, path: str):
        if not self.idx.account_exists(path) and path not in self._new_accounts:
            self.plan.warnings.append(
                f"Target account does not exist and must be created manually: {path}"
            )

    # ---- transactions ----
    def _trade_txn(self, report: Report, trade: Trade) -> TransactionSpec:
        base = self.broker.subtree_base(report.account, trade.currency)
        category = self._category(report, trade)
        key = self._resolve_commodity(trade.isin, trade.asset, category)
        sec_acct = self._resolve_security_account(base, category, key)
        cash_acct = f"{base}:{self.broker.cash_leaf}"
        self._require_account(cash_acct)

        sign = Decimal(1) if trade.is_buy else Decimal(-1)
        principal, nkd, comm = trade.principal, trade.nkd, trade.commission

        splits = [
            SplitSpec(sec_acct, value=sign * principal, quantity=trade.qty,
                      memo=f"{trade.trade_id} @ {trade.price}"),
            SplitSpec(cash_acct, value=-(sign * (principal + nkd)) - comm,
                      quantity=-(sign * (principal + nkd)) - comm, memo=trade.trade_id),
        ]
        if comm > 0:
            self._require_account(self.broker.commission_account)
            splits.append(SplitSpec(self.broker.commission_account, value=comm, quantity=comm,
                                    memo=f"Commission {trade.asset}"))
        if nkd > 0:
            self._require_account(self.broker.coupon_account)
            splits.append(SplitSpec(self.broker.coupon_account, value=sign * nkd,
                                    quantity=sign * nkd, memo=f"НКД {trade.asset}"))

        action = "Buy" if trade.is_buy else "Sell"
        desc = f"{action} {abs(trade.qty):f} {trade.asset}".replace(".000000", "")
        return TransactionSpec(
            date=trade.trade_dt, description=desc, num=trade.trade_id,
            currency=trade.currency, splits=splits,
        )

    def _coupon_txn(self, report: Report, coupon: Coupon) -> TransactionSpec:
        base = self.broker.subtree_base(report.account, coupon.currency)
        cash_acct = f"{base}:{self.broker.cash_leaf}"
        self._require_account(cash_acct)
        self._require_account(self.broker.coupon_account)
        return TransactionSpec(
            date=coupon.op_dt,
            description=coupon.comment,
            num="",
            currency=coupon.currency,
            splits=[
                SplitSpec(cash_acct, value=coupon.amount, quantity=coupon.amount),
                SplitSpec(self.broker.coupon_account, value=-coupon.amount,
                          quantity=-coupon.amount),
            ],
        )

    def run(self, reports: list[Report]) -> Plan:
        for report in reports:
            for u in report.unknown_ops:
                self.plan.warnings.append(
                    f"[{report.account}] unhandled cash operation {u.op_dt.date()} "
                    f"{u.name!r} {u.comment!r} {u.amount} — NOT imported"
                )
            for trade in report.trades:
                txn = self._trade_txn(report, trade)
                if txn.imbalance() != 0:
                    self.plan.warnings.append(
                        f"[{report.account}] trade {trade.trade_id} unbalanced by {txn.imbalance()}"
                    )
                self.plan.transactions.append(txn)
            for coupon in report.coupons:
                self.plan.transactions.append(self._coupon_txn(report, coupon))
        return self.plan


def build_plan(reports: list[Report], idx: BookIndex, broker: Broker) -> Plan:
    return _Planner(idx, broker).run(reports)
