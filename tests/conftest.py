from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest


@pytest.fixture
def build_prefix(tmp_path: Path) -> Callable[..., Path]:
    from fixtures.prefix import write_prefix

    def build(
        name: str = "env",
        *,
        history: Sequence = (),
        installed: Sequence = (),
        pinned: Sequence[str] = (),
    ) -> Path:
        return write_prefix(
            tmp_path / name, history=history, installed=installed, pinned=pinned
        )

    return build


@pytest.fixture
def synth_channel(tmp_path: Path) -> Path:
    from fixtures.synth_channel import write_channel

    return write_channel(tmp_path / "channel")


@pytest.fixture
def cli_environment(tmp_path: Path) -> dict[str, str]:
    """Environment for a conda child process that cannot see the user's config."""
    home = tmp_path / "home"
    home.mkdir()
    condarc = tmp_path / "condarc"
    condarc.write_text("{}\n", encoding="utf-8")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CONDA_") and key not in {"CONDARC", "PYTHONPATH"}
    }
    environment.update(
        HOME=str(home),
        USERPROFILE=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        CONDARC=str(condarc),
        CONDA_NO_PLUGINS="false",
        CONDA_JSON="false",
        CONDA_AUTO_UPDATE_CONDA="false",
    )
    return environment


@pytest.fixture
def run_conda(
    tmp_path: Path, cli_environment: dict[str, str]
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(
        *arguments: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "conda", *arguments],
            cwd=tmp_path,
            env={**cli_environment, **(env or {})},
            capture_output=True,
            text=True,
            timeout=120,
        )

    return run
