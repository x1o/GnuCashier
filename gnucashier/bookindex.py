"""Read-only views of a GnuCash book used by the planner to resolve instruments.

Two backends with the same query surface:
  * XmlBookIndex  - parses a .gnucash XML file, gzip-compressed (GnuCash's
                    default) or plain; works anywhere, no bindings needed.
  * the GnuCash-session backend lives in gnucashier/importer.py (Linux only).
"""
from __future__ import annotations

import gzip
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

CommodityKey = tuple[str, str]  # (namespace, mnemonic)

# Fold Latin homoglyphs to their Cyrillic look-alikes. Broker names and book
# names mix the two freely (e.g. 'БO-01' with a Latin O, '6P2' vs '6Р2'), which
# would otherwise defeat name matching and create duplicate commodities.
_HOMOGLYPH_FOLD = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")


def norm_name(s: str) -> str:
    return re.sub(r"[^0-9A-ZА-Я]", "", s.upper().translate(_HOMOGLYPH_FOLD))

_NS = {
    "gnc": "http://www.gnucash.org/XML/gnc",
    "act": "http://www.gnucash.org/XML/act",
    "cmdty": "http://www.gnucash.org/XML/cmdty",
}


@dataclass
class CommodityInfo:
    namespace: str
    mnemonic: str
    fullname: str
    xcode: str
    fraction: int


class BookIndex:
    """In-memory index shared by both backends. Backends populate the maps."""

    def __init__(self):
        self._commodities: dict[CommodityKey, CommodityInfo] = {}
        self._by_isin: dict[str, CommodityKey] = {}
        self._by_name: dict[str, CommodityKey] = {}
        self._by_norm: dict[str, CommodityKey] = {}
        # account path -> (type, commodity_key or None for currency accounts)
        self._accounts: dict[str, tuple[str, CommodityKey | None]] = {}

    # ---- population ----
    def add_commodity(self, info: CommodityInfo):
        key = (info.namespace, info.mnemonic)
        self._commodities[key] = info
        if info.xcode:
            self._by_isin.setdefault(info.xcode, key)
        if info.fullname:
            self._by_name.setdefault(info.fullname, key)
            self._by_norm.setdefault(norm_name(info.fullname), key)
        self._by_name.setdefault(info.mnemonic, key)
        self._by_norm.setdefault(norm_name(info.mnemonic), key)

    def add_account(self, path: str, acct_type: str, commodity_key: CommodityKey | None):
        self._accounts[path] = (acct_type, commodity_key)

    # ---- queries ----
    def commodity_by_isin(self, isin: str) -> CommodityKey | None:
        return self._by_isin.get(isin) if isin else None

    def commodity_by_name(self, name: str) -> CommodityKey | None:
        return self._by_name.get(name)

    def commodity_by_norm_name(self, name: str) -> CommodityKey | None:
        """Match ignoring case, punctuation, and Latin/Cyrillic homoglyphs."""
        return self._by_norm.get(norm_name(name))

    def commodity_has_xcode(self, key: CommodityKey) -> bool:
        info = self._commodities.get(key)
        return bool(info and info.xcode)

    def commodity(self, key: CommodityKey) -> CommodityInfo | None:
        return self._commodities.get(key)

    def security_commodity_keys_under(self, prefix: str) -> set[CommodityKey]:
        """Commodity keys held by security accounts under `prefix`."""
        p = prefix + ":"
        return {key for path, (_t, key) in self._accounts.items()
                if key and (path == prefix or path.startswith(p))}

    def account_exists(self, path: str) -> bool:
        return path in self._accounts

    def find_security_account(self, base: str, commodity_key: CommodityKey) -> str | None:
        """First account under `base` holding `commodity_key` (any category)."""
        prefix = base + ":"
        for path, (_type, key) in sorted(self._accounts.items()):
            if key == commodity_key and path.startswith(prefix):
                return path
        return None


def _open_book(path: str):
    """Open a GnuCash XML book, transparently handling gzip compression."""
    with open(path, "rb") as f:
        gzipped = f.read(2) == b"\x1f\x8b"
    return gzip.open(path, "rb") if gzipped else open(path, "rb")


class XmlBookIndex(BookIndex):
    def __init__(self, gnucash_xml_path: str):
        super().__init__()
        with _open_book(gnucash_xml_path) as f:
            root = ET.parse(f).getroot()

        for c in root.iter("{http://www.gnucash.org/XML/gnc}commodity"):
            space = c.findtext("cmdty:space", "", _NS)
            if space in ("template", "ISO4217", "CURRENCY", ""):
                continue
            frac = c.findtext("cmdty:fraction", "1", _NS)
            self.add_commodity(CommodityInfo(
                namespace=space,
                mnemonic=c.findtext("cmdty:id", "", _NS),
                fullname=c.findtext("cmdty:name", "", _NS),
                xcode=c.findtext("cmdty:xcode", "", _NS),
                fraction=int(frac) if frac.isdigit() else 1,
            ))

        # Build paths from the account tree.
        raw = {}  # guid -> (name, type, parent, commodity_key)
        for a in root.iter("{http://www.gnucash.org/XML/gnc}account"):
            guid = a.findtext("act:id", "", _NS)
            if not guid:
                continue
            cm = a.find("act:commodity", _NS)
            key = None
            if cm is not None:
                space = cm.findtext("cmdty:space", "", _NS)
                if space not in ("CURRENCY", "", None):
                    key = (space, cm.findtext("cmdty:id", "", _NS))
            raw[guid] = (
                a.findtext("act:name", "", _NS),
                a.findtext("act:type", "", _NS),
                a.findtext("act:parent", None, _NS),
                key,
            )

        def path(guid):
            parts = []
            cur = guid
            while cur in raw:
                name, _t, parent, _k = raw[cur]
                if name and name != "Root Account":
                    parts.insert(0, name)
                cur = parent
            return ":".join(parts)

        for guid, (_name, acct_type, _parent, key) in raw.items():
            p = path(guid)
            if p:
                self.add_account(p, acct_type, key)
