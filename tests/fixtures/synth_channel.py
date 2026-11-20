"""Write a tiny local conda channel that needs no package artifacts."""

from __future__ import annotations

import json
from pathlib import Path

SUBIDRS = ("osx-arm64", "noarch", "linux-64")

Package = tuple[str, str, tuple[str, ...]] | tuple[str, str, tuple[str, ...], str]

OSX_PACKAGES: tuple[Package, ...] = (
    ("python", "3.9.0", ()),
    ("python", "3.10.0", ()),
    ("pip", "1.0", ("python",)),
    ("numpy", "1.24.0", ("python >=3.9,<3.10",)),
    ("numpy", "2.1.0", ("python >=3.10",)),
    ("numpy", "2.1.1", ("python >=3.10,<3.11.0a0",), "h0_0"),
    ("numpy", "2.1.1", ("python >=3.13,<3.14.0a0",), "h1_0"),
    ("libbaz", "1.0", ()),
    ("libbaz", "2.0", ()),
    ("foo", "1.0", ("libbaz <2",)),
    ("foo", "2.0", ("libbaz >=2",)),
    ("bar", "1.0", ("libbaz <2",)),
    ("zlib", "1.0", ()),
    ("broken", "1.0", ("missingdep",)),
)

LINUX_PACKAGES: tuple[Package, ...] = (
    ("onlylinux", "1.0", ()),
)

OSX64_PACKAGES: tuple[Package, ...] = (
    ("onlyosx64", "1.0", ()),
)


def _record(
    subdir: str,
    name: str,
    version: str,
    depends: tuple[str, ...],
    build: str = "h0_0",
) -> dict:
    return {
        "build": build,
        "build_number": 0,
        "depends": list(depends),
        "license": "MIT",
        "md5": "0" * 32,
        "name": name,
        "sha256": "0" * 64,
        "size": 1024,
        "subdir": subdir,
        "timestamp": 1700000000000,
        "version": version,
    }


def _write_repodata(root: Path, subdir: str, packages: tuple[Package, ...]) -> None:
    directory = root / subdir
    directory.mkdir(parents=True, exist_ok=True)
    records = {}
    for package in packages:
        name, version, depends = package[0], package[1], package[2]
        build = package[3] if len(package) > 3 else "h0_0"
        filename = f"{name}-{version}-{build}.tar.bz2"
        records[filename] = _record(subdir, name, version, depends, build)
    (directory / "repodata.json").write_text(
        json.dumps(
            {
                "info": {"subdir": subdir},
                "packages": records,
                "packages.conda": {},
                "repodata_version": 1,
            },
            indent=1,
        )
    )


def write_channel(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _write_repodata(root, "osx-arm64", OSX_PACKAGES)
    _write_repodata(root, "noarch", ())
    _write_repodata(root, "linux-64", LINUX_PACKAGES)
    _write_repodata(root, "osx-64", OSX64_PACKAGES)
    return root


def channel_url(root: Path) -> str:
    return f"file://{root}"
