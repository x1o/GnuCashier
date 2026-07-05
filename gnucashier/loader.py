"""Resolve import arguments (``.zip`` or ``.xls``) to a list of ``.xls`` paths."""
from __future__ import annotations

import os
import re
import zipfile


def expand_report_paths(args: list[str], workdir: str) -> list[str]:
    """Return .xls paths, extracting any .zip into ``workdir`` (ASCII-renamed)."""
    paths: list[str] = []
    for arg in args:
        if arg.lower().endswith(".zip"):
            with zipfile.ZipFile(arg) as zf:
                for info in zf.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".xls"):
                        continue
                    acct = re.search(r"(\d{6,})", info.filename)
                    name = f"account_{acct.group(1)}.xls" if acct else os.path.basename(info.filename)
                    target = os.path.join(workdir, name)
                    with open(target, "wb") as out:
                        out.write(zf.read(info))
                    paths.append(target)
        else:
            paths.append(arg)
    return paths
