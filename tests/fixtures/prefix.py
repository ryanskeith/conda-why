"""Write synthetic conda prefixes for tests and local case scripts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

HistoryRevisions = Sequence[
    tuple[str, Sequence[str]] | tuple[str, Sequence[str], str]
]
InstalledRecords = Sequence[tuple[str, str] | tuple[str, str, Sequence[str]]]


def write_prefix(
    root: Path,
    *,
    history: HistoryRevisions = (),
    installed: InstalledRecords = (),
    pinned: Sequence[str] = (),
) -> Path:
    """Write a prefix that conda reads: conda-meta records, history, and pins."""
    meta = root / "conda-meta"
    meta.mkdir(parents=True, exist_ok=True)

    records = []
    for entry in installed:
        name, version = entry[0], entry[1]
        depends = list(entry[2]) if len(entry) > 2 else []
        build = "h0_0"
        filename = f"{name}-{version}-{build}.tar.bz2"
        records.append(
            {
                "build": build,
                "build_number": 0,
                "channel": "https://example.invalid/test/osx-arm64",
                "depends": depends,
                "files": [],
                "fn": filename,
                "license": "MIT",
                "md5": "0" * 32,
                "name": name,
                "paths_data": {"paths": [], "paths_version": 1},
                "sha256": "0" * 64,
                "size": 1024,
                "subdir": "osx-arm64",
                "timestamp": 1700000000000,
                "url": f"https://example.invalid/test/osx-arm64/{filename}",
                "version": version,
            }
        )
        (meta / f"{name}-{version}-{build}.json").write_text(
            json.dumps(records[-1], indent=1)
        )

    lines: list[str] = []
    for index, revision in enumerate(history, 1):
        action, specs = revision[0], revision[1]
        date = revision[2] if len(revision) > 2 else f"2026-11-{index:02d} 12:00:00"
        lines.extend(
            (
                f"==> {date} <==",
                f"# cmd: conda {action} -p {root}",
                "# conda version: 26.10.0",
                *(
                    f"+{record['name']}-{record['version']}-{record['build']}"
                    for record in records
                ),
                f"# {action} specs: {list(specs)!r}",
            )
        )
    (meta / "history").write_text("".join(f"{line}\n" for line in lines))

    if pinned:
        (meta / "pinned").write_text("".join(f"{spec}\n" for spec in pinned))

    return root
