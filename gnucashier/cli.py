"""GnuCashier — import transactions into GnuCash from multiple sources.

`import`, `backfill`, and `merge` need the GnuCash bindings (run on Linux or via
the Docker image); `validate` is pure Python and runs anywhere.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated, Optional

import typer

app = typer.Typer(
    name="gnucashier",
    help=__doc__.strip().splitlines()[0],
    no_args_is_help=True,
    add_completion=True,
)

# --- shared argument / option types -------------------------------------------
Book = Annotated[Path, typer.Argument(exists=True, dir_okay=False,
                                      help="Target GnuCash book (.gnucash / .xac)")]
Reports = Annotated[list[Path], typer.Argument(exists=True,
                    help="Broker report(s): a .zip archive or one or more .xls files")]
BrokerOpt = Annotated[Optional[str], typer.Option(
    "--broker", "-b", help="Broker profile from the config (default: the only one configured)")]
ConfigOpt = Annotated[Optional[Path], typer.Option(
    "--config", "-c", exists=True, dir_okay=False, help="Path to gnucashier.toml")]


def _resolve_broker(name: Optional[str], config: Optional[Path]):
    """Pick the broker: the named one, or the sole configured one, else ask."""
    from .config import load_broker, load_config
    cfg_path = str(config) if config else None
    if name:
        return load_broker(name, cfg_path)
    names = list(load_config(cfg_path).get("brokers", {}))
    if len(names) == 1:
        return load_broker(names[0], cfg_path)
    if not names:
        raise typer.BadParameter("no [brokers.*] sections found in the config")
    raise typer.BadParameter(
        f"multiple brokers configured — pass --broker (one of: {', '.join(sorted(names))})")


# --- commands -----------------------------------------------------------------
@app.command("import")
def import_(
    book: Book,
    reports: Reports,
    broker: BrokerOpt = None,
    config: ConfigOpt = None,
    force: Annotated[bool, typer.Option(help="import even if commodities are missing ISINs")] = False,
):
    """Import broker report(s) into the book (dry-run → confirm → save)."""
    from .importer import BrokerImporter
    b = _resolve_broker(broker, config)
    # BrokerImporter expands any .zip itself.
    BrokerImporter(str(book), [str(r) for r in reports], b).run(require_isins=not force)


@app.command()
def backfill(book: Book, reports: Reports, broker: BrokerOpt = None, config: ConfigOpt = None):
    """One-time: fill missing commodity ISINs (cmdty:xcode) from the report(s)."""
    from . import backfill as backfill_mod
    from .loader import expand_report_paths
    b = _resolve_broker(broker, config)
    with tempfile.TemporaryDirectory() as workdir:
        xls = expand_report_paths([str(r) for r in reports], workdir)
        backfill_mod.run(str(book), xls, b, confirm=True)


@app.command()
def validate(book: Book, reports: Reports, broker: BrokerOpt = None, config: ConfigOpt = None):
    """Check report(s) against the broker's own control totals (no bindings needed)."""
    from .bookindex import XmlBookIndex
    from .loader import expand_report_paths
    from .validate import validate_paths
    b = _resolve_broker(broker, config)
    idx = XmlBookIndex(str(book))
    with tempfile.TemporaryDirectory() as workdir:
        xls = expand_report_paths([str(r) for r in reports], workdir)
        ok = validate_paths(xls, idx, b)
    raise typer.Exit(0 if ok else 1)


@app.command()
def merge(
    book: Book,
    export: Annotated[Path, typer.Argument(exists=True, dir_okay=False,
                      help="GnuCash book/export to merge in (.xac / .gnucash)")],
):
    """Merge another GnuCash book/export (e.g. GnuCash Mobile) into the book."""
    from .xac import GnuCashImporter
    GnuCashImporter(str(book), str(export)).run(confirm=True)


def main():
    app()


if __name__ == "__main__":
    main()
