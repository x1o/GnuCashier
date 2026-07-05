"""Execute a broker import plan against a live GnuCash book.

This is the only module that needs the GnuCash Python bindings, so it runs on
the machine where they are installed (Linux). Parsing/planning happen in the
pure-Python modules and are validated independently.
"""
from __future__ import annotations

import shutil
import tempfile
from decimal import Decimal, ROUND_HALF_UP

from gnucash import Session, SessionOpenMode, Account, Transaction, Split, GncNumeric
from gnucash.gnucash_core_c import ACCT_TYPE_STOCK, ACCT_TYPE_MUTUAL, ACCT_TYPE_ASSET

from .bookindex import BookIndex, CommodityInfo
from .brokers import Broker
from .loader import expand_report_paths
from .model import Report
from .planner import Plan, build_plan

_ACCT_TYPE = {"STOCK": ACCT_TYPE_STOCK, "MUTUAL": ACCT_TYPE_MUTUAL}


def _num(value: Decimal, denom: int) -> GncNumeric:
    scaled = (value * denom).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return GncNumeric(int(scaled), int(denom))


class BrokerImporter:
    def __init__(self, base_file: str, report_paths: list[str], broker: Broker):
        self.base_file = base_file
        self.broker = broker
        # Accept .zip archives as well as .xls files (extracted to a temp dir).
        self._workdir = tempfile.mkdtemp(prefix="broker-import-")
        self.report_paths = expand_report_paths(report_paths, self._workdir)
        self.session = None
        self.book = None
        self.reports: list[Report] = []
        self.plan: Plan | None = None
        self.isin_fillable = []    # Alfa commodities missing ISIN, fillable from these reports
        self.isin_unfillable = []  # ... missing ISIN, not in these reports

    # ---- session ----
    def open_session(self):
        print("Opening GnuCash book...")
        self.session = Session(self.base_file, SessionOpenMode.SESSION_NORMAL_OPEN)
        self.book = self.session.book
        print("✓ Opened\n")

    def close_session(self):
        if self.session:
            self.session.end()
            self.session.destroy()
            self.session = None
        shutil.rmtree(self._workdir, ignore_errors=True)

    # ---- book index from the live session ----
    def _account_path(self, account) -> str:
        parts, cur = [], account
        while cur:
            name = cur.GetName()
            if name and name != "Root Account":  # exclude the book root
                parts.insert(0, name)
            cur = cur.get_parent()
        return ":".join(parts)

    def _build_index(self) -> BookIndex:
        idx = BookIndex()
        table = self.book.get_table()
        for ns_obj in table.get_namespaces_list():
            ns = ns_obj.get_name()
            if ns in ("CURRENCY", "template"):
                continue
            for c in ns_obj.get_commodity_list():
                idx.add_commodity(CommodityInfo(
                    namespace=ns,
                    mnemonic=c.get_mnemonic(),
                    fullname=c.get_fullname(),
                    xcode=c.get_cusip() or "",
                    fraction=c.get_fraction(),
                ))

        def walk(acc):
            path = self._account_path(acc)
            cm = acc.GetCommodity()
            key = None
            if cm is not None and cm.get_namespace() != "CURRENCY":
                key = (cm.get_namespace(), cm.get_mnemonic())
            if path:
                idx.add_account(path, str(acc.GetType()), key)
            for child in acc.get_children():
                walk(child)

        walk(self.book.get_root_account())
        return idx

    # ---- account lookup / creation ----
    def _account_by_path(self, path: str):
        acc = self.book.get_root_account()
        for part in path.split(":"):
            acc = acc.lookup_by_name(part)
            if acc is None:
                return None
        return acc

    def _ensure_parent(self, path: str):
        acc = self._account_by_path(path)
        if acc is not None:
            return acc
        parent_path, _, leaf = path.rpartition(":")
        parent = self._ensure_parent(parent_path) if parent_path else self.book.get_root_account()
        new_acc = Account(self.book)
        new_acc.SetName(leaf)
        new_acc.SetType(ACCT_TYPE_ASSET)
        new_acc.SetCommodity(self.book.get_table().lookup("CURRENCY", "RUB"))
        parent.append_child(new_acc)
        print(f"  ⚠ Created missing parent account: {path}")
        return new_acc

    # ---- planning / dry run ----
    def prepare(self):
        from .backfill import audit_missing_isins
        self.reports = [self.broker.parse(p) for p in self.report_paths]
        idx = self._build_index()
        self.isin_fillable, self.isin_unfillable = audit_missing_isins(
            idx, self.reports, self.broker)
        self.plan = build_plan(self.reports, idx, self.broker)
        self._print_dry_run()

    def _print_dry_run(self):
        from .report import format_dry_run
        print(format_dry_run(self.reports, self.plan,
                             self.isin_fillable, self.isin_unfillable, self.broker.name))

    # ---- execution ----
    def execute(self):
        table = self.book.get_table()

        if self.plan.commodities:
            print(f"Creating {len(self.plan.commodities)} commodities...")
            from gnucash import GncCommodity
            for c in self.plan.commodities:
                comm = GncCommodity(self.book, c.fullname, c.namespace,
                                    c.mnemonic, c.isin, c.fraction)
                table.insert(comm)
                print(f"  ✓ {c.namespace}:{c.mnemonic}")

        if self.plan.accounts:
            print(f"Creating {len(self.plan.accounts)} accounts...")
            for a in self.plan.accounts:
                parent_path, _, leaf = a.path.rpartition(":")
                parent = self._ensure_parent(parent_path)
                acc = Account(self.book)
                acc.SetName(leaf)
                acc.SetType(_ACCT_TYPE[a.acct_type])
                acc.SetCommodity(table.lookup(a.commodity_key[0], a.commodity_key[1]))
                parent.append_child(acc)
                print(f"  ✓ {a.path}")

        print(f"Importing {len(self.plan.transactions)} transactions...")
        for i, txn in enumerate(self.plan.transactions, 1):
            currency = table.lookup("CURRENCY", txn.currency)
            cur_frac = currency.get_fraction()
            trans = Transaction(self.book)
            trans.BeginEdit()
            trans.SetCurrency(currency)
            trans.SetDescription(txn.description)
            if txn.num:
                trans.SetNum(txn.num)
            trans.SetDate(txn.date.day, txn.date.month, txn.date.year)
            for s in txn.splits:
                acc = self._account_by_path(s.account_path)
                if acc is None:
                    raise RuntimeError(f"account not found: {s.account_path}")
                split = Split(self.book)
                split.SetParent(trans)
                split.SetAccount(acc)
                split.SetValue(_num(s.value, cur_frac))
                split.SetAmount(_num(s.quantity, acc.GetCommodity().get_fraction()))
                if s.memo:
                    split.SetMemo(s.memo)
            trans.CommitEdit()
            if i % 25 == 0:
                print(f"  {i}/{len(self.plan.transactions)}")
        print(f"✓ Imported {len(self.plan.transactions)} transactions\n")

        print("Saving book...")
        self.session.save()
        print("✓ Saved")

    def ask_confirmation(self) -> bool:
        print("\nProceed with the import? (yes/no): ", end="")
        return input().strip().lower() in ("yes", "y")

    def run(self, confirm: bool = True, require_isins: bool = True):
        try:
            self.open_session()
            self.prepare()
            if require_isins and self.isin_fillable:
                print("\n" + "=" * 70)
                print(f"⛔ PREREQUISITE NOT MET — {self.broker.name} commodities are missing ISINs")
                print("=" * 70)
                print(f"{len(self.isin_fillable)} existing commodity(ies) can be ISIN-tagged from "
                      "these reports.\nImporting now risks creating duplicates. Run the one-time "
                      "backfill first:\n")
                print("  python -m broker.backfill <book.gnucash> <report.zip>\n")
                print("then re-run the import. (Override with require_isins=False / --force.)")
                return
            if confirm and not self.ask_confirmation():
                print("Import cancelled.")
                return
            print("\nStarting import...\n")
            self.execute()
        finally:
            self.close_session()
