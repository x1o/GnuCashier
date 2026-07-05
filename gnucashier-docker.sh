#!/usr/bin/env bash
# Run GnuCashier via the Dockerized GnuCash bindings — works identically on
# macOS (Docker Desktop) and Linux. Forwards all arguments to the `gnucashier`
# CLI (see `gnucashier --help`). Run in this order — backfill before the first
# import (import aborts while fillable ISINs are missing):
#
#   ./gnucashier-docker.sh backfill <book> <report.zip|.xls ...>   # once, first
#   ./gnucashier-docker.sh validate <book> <report.zip|.xls ...>   # optional pre-check
#   ./gnucashier-docker.sh import   <book> <report.zip|.xls ...>   # then import
#
# Paths are relative to this repo (bind-mounted at /work). Builds the image on
# first use. Interactive (-it) so confirmation prompts work; back up the book
# first — the importer does not detect duplicates.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

# Keep the image (which bakes the code) in sync with the repo. Cached, so this is
# a fast no-op when nothing changed; the first build takes a minute.
echo "Building/updating gnucashier image..." >&2
docker build -q -t gnucashier "$here" >/dev/null

exec docker run --rm -it \
    --user "$(id -u):$(id -g)" \
    -v "$here":/work \
    gnucashier "$@"
