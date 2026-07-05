"""Load the per-user broker layout from a TOML config file.

The config maps your private account details (numbers, book paths) that must not
live in the code. Search order for the file:
  1. an explicit path (--config / the `path` argument)
  2. $GNUCASHIER_CONFIG
  3. ./gnucashier.toml
  4. ~/.config/gnucashier/config.toml
See gnucashier.example.toml for the format.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .brokers import PARSERS, Broker

_SEARCH = [
    lambda: os.environ.get("GNUCASHIER_CONFIG"),
    lambda: "gnucashier.toml",
    lambda: str(Path.home() / ".config" / "gnucashier" / "config.toml"),
]


def find_config(path: str | None = None) -> Path:
    candidates = [path] if path else [c() for c in _SEARCH]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    raise SystemExit(
        "No config file found. Create gnucashier.toml (see gnucashier.example.toml) "
        "or pass --config PATH / set GNUCASHIER_CONFIG."
    )


def load_config(path: str | None = None) -> dict:
    with open(find_config(path), "rb") as f:
        return tomllib.load(f)


def load_broker(name: str, path: str | None = None) -> Broker:
    """Build a Broker from its code parser + its config layout section."""
    key = name.lower()
    if key not in PARSERS:
        known = ", ".join(sorted(PARSERS))
        raise SystemExit(f"Unknown broker {name!r}. Known brokers: {known}")

    brokers_cfg = load_config(path).get("brokers", {})
    if key not in brokers_cfg:
        raise SystemExit(
            f"Broker {name!r} has no [brokers.{key}] section in your config."
        )
    cfg = brokers_cfg[key]

    try:
        subaccounts = {
            (str(s["account"]), s["currency"]): s["path"]
            for s in cfg.get("subaccount", [])
        }
        broker = Broker(
            name=name,
            parse=PARSERS[key],
            subaccounts=subaccounts,
            broker_root=cfg["broker_root"],
            commission_account=cfg["commission_account"],
        )
    except KeyError as e:
        raise SystemExit(f"Config for broker {name!r} is missing key: {e}")

    # Optional overrides.
    for opt in ("coupon_account", "commodity_namespace", "cash_leaf",
                "bond_fraction", "default_fraction"):
        if opt in cfg:
            setattr(broker, opt, cfg[opt])
    return broker
