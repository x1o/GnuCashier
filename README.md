# GnuCashier

Ring transactions into [GnuCash](https://www.gnucash.org/) from many sources. A
cashier takes money-movements from any till and records them in the books —
GnuCashier does the same for your GnuCash book: point it at an external export,
review a dry run, confirm, and it merges the transactions in.

Built on the official `python3-gnucash` bindings, so it drives GnuCash's own
object model rather than editing files by hand.

For the report format, accounting model, instrument-resolution logic, and
maintenance notes, see [docs/DESIGN.md](docs/DESIGN.md).

## Sources

| Source | Subcommand | Notes |
|--------|-----------|-------|
| **Broker reports** (Alfa-Bank / MOEX `.xls`, zipped) | `import` | Trades and coupons from Moscow Exchange statements. Bonds, accrued interest, commission. |
| **GnuCash exports** (`.xac` / `.gnucash`, incl. GnuCash Mobile) | `merge` | Merge another GnuCash book into the base book, creating missing accounts/commodities. |

Each source is a parser producing the same normalized model
(`gnucashier/model.py`); the planner turns that into balanced GnuCash
transactions. Adding a broker = add a parser + a config section, no engine
changes.

## Install

The GnuCash Python bindings aren't on PyPI and the macOS GnuCash app omits them,
so either install them from your distro **or use the Docker image** (recommended
on macOS; identical on Linux).

**Docker (any OS):**
```bash
docker build -t gnucashier .        # or let ./gnucashier-docker.sh build on first use
```

**Linux (native):** the `gnucash` module isn't on PyPI, so the venv must see the
system `python3-gnucash` — create it with `--system-site-packages` (a plain
`uv sync` makes an isolated venv where `import gnucash` fails):
```bash
sudo apt-get install gnucash python3-gnucash
uv venv --system-site-packages      # venv can see system python3-gnucash
uv sync                             # install GnuCashier + its PyPI deps (keeps the flag)

# the bindings' shared libs aren't on the default loader path, so export this
# (validate needs no bindings; import/backfill/merge do):
export LD_LIBRARY_PATH=/usr/lib/$(uname -m)-linux-gnu/gnucash
uv run gnucashier backfill <book> <report>
```
If you ever see `ModuleNotFoundError: No module named 'gnucash'`, your `.venv`
was created isolated — `rm -rf .venv` and redo the `uv venv --system-site-packages`
step. (Docker sidesteps all of this.)

## Configure

Your account mapping (numbers + book paths) is private and lives in a gitignored
`gnucashier.toml`, never in the code:
```bash
cp gnucashier.example.toml gnucashier.toml   # then edit with your accounts
```
The file maps each brokerage sub-account (number + currency) to a base account
path in your book, plus the commission/coupon accounts. See
`gnucashier.example.toml` for the format.

## Use

```bash
# via Docker (bind-mounts the repo, builds on first run):
./gnucashier-docker.sh backfill books/MyBook.gnucash "imports/report.zip"  # once, first
./gnucashier-docker.sh validate books/MyBook.gnucash "imports/report.zip"  # optional pre-check
./gnucashier-docker.sh import   books/MyBook.gnucash "imports/report.zip"  # then import
```

The order matters: run `backfill` **before** the first `import` (it tags existing
commodities with ISINs, and `import` aborts while fillable ISINs are missing).
`validate` is an optional pre-flight; `import` is the final step.

```bash
# or the CLI directly (Linux / inside the container):
gnucashier backfill    <book> <report.zip|.xls ...>                        # one-time
gnucashier validate    <book> <report.zip|.xls ...>   # pure Python, runs anywhere
gnucashier import      <book> <report.zip|.xls ...> [--broker alfa] [--config P] [--force]
gnucashier merge       <book> <export.xac>            # merge a GnuCash book/export (separate)
```

Every `import`/`merge` run is **dry-run → confirm → save**: it lists the
commodities/accounts it will create and the transactions it will import, then
asks before writing. GnuCash keeps the pre-save state itself — on each save it
writes a timestamped backup of the previous version next to the book
(`<book>.<timestamp>.gnucash`, for XML books, per its retain-backup setting).

### One-time: `backfill`

Older book commodities may lack an ISIN (`cmdty:xcode`). `backfill` fills them in
from the reports (matching by name, folding Latin/Cyrillic homoglyphs) so the
importer matches by ISIN and never creates duplicates. Run it once; re-running is
a no-op. The importer refuses to run while fillable commodities are missing ISINs
(`--force` overrides).

## How the broker import models trades

- One transaction per trade: security account (units + clean value), cash, the
  commission account, and — for bonds — an `Income:Coupons` split for the accrued
  interest (НКД). One transaction per coupon: cash + `Income:Coupons`.
- Per-unit price is derived as `principal / quantity` (robust to amortized
  bonds); the trade date is the posting date; the instrument name is put in
  descriptions so per-bond income stays filterable under the flat coupon account.
- Instruments are resolved by ISIN → exact name → create-new; the importer never
  modifies existing commodities.
- `validate` cross-checks the generated transactions against the broker's own
  control totals (net cash `Итого`, coupons `Купоны`, fees) with no bindings.

## Layout

```
gnucashier/
  model.py        normalized Trade/Coupon/Holding/Report
  parsers/alfa.py Alfa .xls report parser (add siblings for new brokers)
  brokers.py      Broker profile + parser registry
  config.py       loads the private layout from gnucashier.toml
  bookindex.py    read-only book views + homoglyph name folding
  planner.py      reports + book + broker -> balanced transactions
  importer.py     executes the plan via the GnuCash bindings
  backfill.py     one-time ISIN backfill
  validate.py     self-check vs. broker control totals (no bindings)
  xac.py          GnuCash-export merger (the `merge` command)
  cli.py          unified `gnucashier` command
```

## Safety & limits

- **Backups** are GnuCash's own: each save leaves a timestamped copy of the
  previous version (`<book>.<timestamp>.gnucash`, XML books, per the retain-backup
  setting), so the pre-import state is kept. There's still **no duplicate
  detection** — importing the same source twice imports it twice; pick
  non-overlapping periods (broker periods overlap at the edges, e.g. `01.06–04.07`).
- **Opening balances**: only the period's activity is posted. If a bond held at
  the period start lands in a *new* account (your book tracks it under a different
  name/commodity, or not at all), that account starts from zero — so period sells
  make it negative. The dry run **warns** for each such account (with the starting
  quantity) so you can reconcile it (redirect to the existing account, or add an
  opening balance) instead of getting a surprise negative holding.
- Realized gain/loss on sells isn't booked (reports carry no cost basis); a sell's
  security value is the proceeds. A clean per-unit price *is* written to the price
  DB per traded instrument (dated at the trade), so GnuCash can value the holdings;
  run Finance::Quote for up-to-date marks.
- Unmodeled cash operations (deposits, taxes, dividends, FX) are reported as
  warnings and skipped, never silently dropped.
- `gnucashier.toml`, `imports/`, and `books/Personal.gnucash` are gitignored —
  keep your real data and account numbers out of the repo.
