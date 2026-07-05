# GnuCashier — design notes

Background and rationale behind the importer. For usage, see the [README](../README.md).

## The broker report format (Alfa-Bank / MOEX `.xls`)

Each report is one legacy BIFF `.xls` per brokerage sub-account. Read them with
`python-calamine` — legacy `.xls` isn't handled by newer `xlrd`/`openpyxl`. The
five sheets; two carry the data GnuCashier imports:

- **`Завершенные сделки`** (Completed trades) — one row per trade. Key columns:
  trade № (exchange id + internal id; the **exchange id** is what the cash sheet
  references), trade datetime, settlement datetime, **ISIN**, `Актив` (display
  name), **quantity** (signed: `+` acquired/buy, `−` disposed/sell), price (% of
  par for bonds), amount (settlement, **incl. accrued interest**), **НКД**
  (accrued coupon interest), settlement currency, commission.
- **` Движение ДС`** (Cash movement — note the leading space) — the authoritative
  cash ledger. Operation types (col `Наименование операции`):
  `Расчеты по сделке {id}` (principal leg = amount − НКД), `НКД по сделке`
  (accrued-interest leg), `Комиссия по сделке {id}` (commission), and
  `Перевод` + comment `погашение купона …` (**coupon income**). The tail holds
  control totals: `Итого:` (net cash change), `Купоны` (coupons),
  `Списано по тарифам Банка` (fees).
- **`Динамика позиций`** (Position dynamics) — holdings snapshot: per instrument,
  start/end quantity, and section headers giving the instrument class
  (`Валюта`/`Облигации`/`Прочее`/`Акции` → currency/bonds/funds/stocks). Used for
  the account category and the "held at period start" check.
- **`Незавершенные сделки`**, **`Неторговые операции`** — unsettled trades /
  non-trade operations; usually empty.

Reconciliation invariants (checked by `validate`):

- `principal = amount − НКД`; the clean **per-unit price = principal / |quantity|**
  — robust to amortized bonds (don't assume par = 1000).
- modeled net cash == `Итого:`; standalone coupon income == `Купоны`;
  commission == fees.
- Report periods are **not** clean calendar months and overlap at the edges, so
  there is no automatic de-duplication — pick non-overlapping periods.

## Accounting model

- **One transaction per trade**, with balanced splits:
  - security account: quantity `±units`, value `±principal` (clean, no НКД);
  - cash: `−(principal + НКД + commission)` for a buy, `+(principal + НКД − commission)` for a sell;
  - commission → the broker commission expense account;
  - НКД → the coupon income account. Accrued interest **paid** on a buy reduces
    coupon income; **received** on a sell adds to it — so it nets out over the
    bond's life against the coupons.
- **One transaction per coupon**: cash + coupon income.
- **A price-DB entry per traded instrument** (clean per-unit price, dated at the
  latest trade). GnuCash values a portfolio from the **price database**, so
  without this, freshly-imported holdings show as ~0 and portfolio totals are far
  too low. Run Finance::Quote afterwards for up-to-date marks.
- Coupon income and НКД default to a single flat income account; the instrument
  name is in each description so per-bond income stays filterable.
- Realized gain/loss on sells is **not** booked (reports carry no cost basis); a
  sell's security value is the proceeds.
- `fraction`/SCU of a security doesn't affect valuation (price = value ÷ quantity
  is fraction-independent).

## Instrument resolution (the hard part)

The report identifies an instrument by **ISIN + display name**. A hand-curated
book may identify the same instrument by a short custom commodity id that matches
*neither*, and Latin/Cyrillic homoglyphs creep in (e.g. Latin `P` vs Cyrillic
`Р`, Latin `O` vs Cyrillic `О`). Resolution order:

1. **ISIN** against the commodity `xcode` (GnuCash's "cusip" field);
2. **exact name** against commodity id / fullname;
3. otherwise **create** a new commodity (id = name = display name, `xcode` = ISIN)
   + account.

The importer never mutates existing commodities. Because older books often have
ISIN-less commodities, the one-time `backfill` fills `xcode` from reports
(matching by name, folding homoglyphs) so subsequent matching is by ISIN. `import`
**refuses to run while fillable ISINs are missing** (that's the situation that
otherwise creates duplicate commodities); `--force` overrides.

Two data-quality pitfalls this surfaces (both flagged in the dry run):

- **Duplicate commodities** — the same bond present twice (once ISIN-less under a
  short name, once with the ISIN). The importer matches the ISIN one and may
  create a *new* account while the real position sits under the other.
- **Missing holdings** — a position held but absent from the book → new account.

When a **new** security account is created for an instrument the report shows was
**held at the period start**, the dry run warns: that account starts from zero, so
period activity alone can make it negative. Reconcile by redirecting the splits to
the existing account, or adding an opening-balance transaction.

## Adding another broker

A broker = a **report parser** (`gnucashier/parsers/<name>.py` returning a
`model.Report`) + a **config section** (book layout). Register the parser in
`brokers.py` `PARSERS`, add a `[brokers.<name>]` section to the config, and select
it with `--broker <name>`. The planner and importer are broker-agnostic; the model
(`model.py`) is the contract between parsers and the engine.

## Maintenance / bindings gotchas

- The `gnucash` Python bindings **aren't on PyPI** and the macOS app omits them.
  On Linux install `python3-gnucash`; the uv venv must be created with
  `uv venv --system-site-packages` (a plain venv is isolated →
  `ModuleNotFoundError: gnucash`), and `LD_LIBRARY_PATH` must include the gnucash
  lib dir (`/usr/lib/$(uname -m)-linux-gnu/gnucash`) at run time. The Docker image
  bundles all of this.
- On Ubuntu, `python3-gnucash` does **not** depend on the engine libraries —
  install `gnucash` too (it provides `libgnc-*.so`).
- GnuCash XML books are **gzip-compressed by default**; the XML reader used by
  `validate` (`bookindex.XmlBookIndex`) detects and decompresses. The bindings
  handle compression natively.
- The book's root account is named **"Root Account"** — exclude it when building
  full account paths.
- On GnuCash 5.x, `GncCommodity(...)` requires all six args
  (book, fullname, namespace, mnemonic, cusip, fraction).
- Prices: `GncPrice(book)` → `set_commodity/currency/value/time64/source_string/typestr`,
  then `book.get_price_db().add_price(...)`.
- In headless containers, set `GSETTINGS_BACKEND=memory` to silence a dconf
  warning; `HOME` must be writable.

## Module layout

See the [README](../README.md#layout). In short: `parsers/*` + `model.py` are the
report side; `bookindex.py` + `brokers.py` + `config.py` resolve against the book;
`planner.py` turns reports into balanced transactions; `importer.py` / `backfill.py`
apply them via the bindings; `validate.py` self-checks with no bindings; `cli.py`
is the unified command.
