#!/usr/bin/env python
"""Recreate the offline cases from docs/real-issues.md and optionally run them."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from fixtures.prefix import write_prefix  # noqa: E402
from fixtures.synth_channel import channel_url, write_channel  # noqa: E402

DEFAULT_BASE = Path("/tmp/conda-why-cases")


def build(base: Path) -> Path:
    shutil.rmtree(base, ignore_errors=True)
    (base / "home").mkdir(parents=True)
    (base / "condarc").write_text("{}\n", encoding="utf-8")
    channel = write_channel(base / "channel")
    prefixes = base / "prefixes"
    write_prefix(
        prefixes / "history",
        history=[("create", ["python=3.9"], "2026-11-01 10:00:00")],
        installed=[("python", "3.9.0")],
    )
    write_prefix(
        prefixes / "installed",
        installed=[("python", "3.9.0"), ("numpy", "1.24.0")],
    )
    write_prefix(
        prefixes / "pin",
        installed=[("python", "3.9.0")],
        pinned=["numpy 1.24.*"],
    )
    write_prefix(
        prefixes / "blocked",
        history=[("create", ["foo=1.0"])],
        installed=[
            ("python", "3.9.0"),
            ("libbaz", "1.0"),
            ("foo", "1.0", ("libbaz <2",)),
        ],
    )
    write_prefix(
        prefixes / "healthy",
        history=[("create", ["python=3.9"], "2026-11-01 10:00:00")],
        installed=[("python", "3.9.0")],
    )
    return channel


def cases(channel: Path, base: Path) -> list[tuple[str, list[str], str]]:
    common = ["-c", channel_url(channel), "--override-channels"]
    prefixes = base / "prefixes"

    def case(label: str, argv: list[str], expected: str):
        return (label, [*argv, *common], expected)

    return [
        case(
            "unsupported_python from the request",
            ["create", "python=3.9", "numpy=2.1", "-p", str(base / "envs" / "python")],
            "unsupported_python",
        ),
        case(
            "unsupported_python from history",
            ["install", "numpy=2.1", "-p", str(prefixes / "history")],
            "unsupported_python",
        ),
        case(
            "unsupported_python from the installed python",
            ["update", "numpy=2.1", "-p", str(prefixes / "installed")],
            "unsupported_python",
        ),
        case(
            "incompatible user specs",
            ["create", "foo=2.0", "bar", "-p", str(base / "envs" / "foo")],
            "incompatible_specs",
        ),
        case(
            "installed requirement blocks the request",
            ["install", "libbaz=2.0", "-p", str(prefixes / "blocked")],
            "installed_constraint",
        ),
        case(
            "conda-meta/pinned blocks the request",
            ["install", "numpy=2.1", "-p", str(prefixes / "pin")],
            "pinned_constraint",
        ),
        case(
            "package not found",
            [
                "create",
                "definitely-not-a-package",
                "-p",
                str(base / "envs" / "missing"),
            ],
            "package_not_found",
        ),
        case(
            "package exists only for osx-64",
            ["create", "onlyosx64", "-p", str(base / "envs" / "osx64")],
            "unsupported_platform",
        ),
        case(
            "healthy solve",
            ["install", "zlib", "-p", str(prefixes / "healthy")],
            "healthy",
        ),
        case(
            "unknown fallback (missing dependency)",
            ["create", "broken", "-p", str(base / "envs" / "broken")],
            "unknown",
        ),
    ]


def environment(base: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CONDA_")
        and key not in {"CONDARC", "PYTHONPATH", "PYTHONHOME"}
    }
    env.update(
        HOME=str(base / "home"),
        CONDARC=str(base / "condarc"),
        CONDA_NO_PLUGINS="false",
        CONDA_JSON="false",
        CONDA_AUTO_UPDATE_CONDA="false",
    )
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--run", action="store_true", help="run the cases")
    parser.add_argument("--json", action="store_true", help="add --json to each case")
    parser.add_argument("--only", help="run cases whose label contains this text")
    args = parser.parse_args()

    channel = build(args.dir)
    selected = [
        case for case in cases(channel, args.dir) if not args.only or args.only in case[0]
    ]
    if not args.run:
        print(f"channel: {channel}")
        print(f"prefixes: {args.dir / 'prefixes'}")
        print("Set up by this script; run with --run to execute.\n")
        for label, argv, expected in selected:
            command = ["conda", "why", *argv]
            if args.json:
                command.append("--json")
            print(f"# {label} (expect {expected})")
            print(" ".join(shlex.quote(part) for part in command))
            print()
        return 0

    env = environment(args.dir)
    failures = 0
    for label, argv, expected in selected:
        command = [sys.executable, "-m", "conda", "why", *argv]
        if args.json:
            command.append("--json")
        print("=" * 78)
        print(label)
        print("$ " + " ".join(shlex.quote(part) for part in command[3:]))
        result = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            print(result.stderr.strip())
            failures += 1
            continue
        if args.json:
            payload = json.loads(result.stdout)
            diagnosis = payload.get("diagnosis")
            kind = diagnosis["kind"] if diagnosis else "healthy"
            print(json.dumps(diagnosis or {"ok": payload["ok"]}, indent=2))
        else:
            print(result.stdout.strip())
            kind = expected
        if kind != expected:
            print(f"[expected {expected}, got {kind}]")
            failures += 1
    print("=" * 78)
    print(f"{len(selected) - failures}/{len(selected)} cases matched")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
