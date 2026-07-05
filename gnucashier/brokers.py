"""Broker profiles: report format + book layout.

A `Broker` bundles a report parser (which report format to read) with the book
layout it maps into (sub-account paths, commission/coupon accounts, the
ISIN-checked subtree). The *format* lives in code (`PARSERS`); the *layout* is
per-user private data and is loaded from config (see gnucashier/config.py), so
no real account numbers or paths are hardcoded here.

To add a broker: write a parser in gnucashier/parsers/<name>.py returning a
`model.Report`, register it in `PARSERS`, and add a `[brokers.<name>]` section to
the config file.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .model import Report
from .parsers import alfa

CommodityKey = tuple[str, str]

# Report-format handlers, keyed by broker name (the code half of a broker).
PARSERS: dict[str, Callable[[str], Report]] = {
    "alfa": alfa.parse_report,
}


@dataclass
class Broker:
    name: str
    parse: Callable[[str], Report]                 # report file -> Report
    subaccounts: dict[tuple[str, str], str]        # (account, currency) -> base path
    broker_root: str                               # subtree whose securities need ISINs
    commission_account: str
    coupon_account: str = "Income:Coupons"         # coupons + accrued interest
    commodity_namespace: str = "MOEX"
    cash_leaf: str = "Cash"
    bond_fraction: int = 10000
    default_fraction: int = 1

    def subtree_base(self, account: str, currency: str) -> str:
        try:
            return self.subaccounts[(account, currency)]
        except KeyError:
            raise KeyError(
                f"[{self.name}] no sub-account mapping for {account!r}/{currency!r}. "
                f"Add it under [brokers.{self.name.lower()}] in your config."
            )
