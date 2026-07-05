# Portable environment with the GnuCash Python bindings, so GnuCashier runs
# identically on Linux and on macOS (via Docker Desktop). The bindings are not on
# PyPI and the macOS GnuCash app omits them, so we use Ubuntu's prebuilt
# python3-gnucash. Multi-arch: works on amd64 (Linux) and arm64 (Apple Silicon).
#
# Build:  docker build -t gnucashier .
# Run:    docker run --rm -it -v "$PWD":/work gnucashier import <book> <report.zip|xls...>
#
# GnuCashier is installed into the image; your book, reports, and config are
# bind-mounted at /work at run time.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    GSETTINGS_BACKEND=memory \
    LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu/gnucash:/usr/lib/aarch64-linux-gnu/gnucash

# On Ubuntu 24.04 python3-gnucash does NOT depend on the engine libraries, so
# the 'gnucash' package must be installed too (it provides libgnc-*.so under
# /usr/lib/<triplet>/gnucash).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gnucash \
        python3-gnucash \
        python3-pip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install GnuCashier and its dependencies (from pyproject.toml — the single
# source of truth). Only the code is copied in; your book, reports, and config
# are bind-mounted at /work at run time.
COPY pyproject.toml README.md /src/
COPY gnucashier /src/gnucashier
RUN pip install --no-cache-dir --break-system-packages /src && rm -rf /src

WORKDIR /work
ENTRYPOINT ["gnucashier"]
