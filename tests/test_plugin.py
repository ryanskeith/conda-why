from __future__ import annotations

import argparse
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path


def check_installed_discovery() -> None:
    from conda.plugins.hookspec import CondaSpecs
    from conda.plugins.manager import CondaPluginManager

    entry_points = [
        entry
        for entry in distribution("conda-why").entry_points
        if entry.group == "conda"
    ]
    assert [(entry.name, entry.value) for entry in entry_points] == [
        ("conda-why", "conda_why.plugin")
    ]

    manager = CondaPluginManager()
    manager.add_hookspecs(CondaSpecs)
    assert manager.load_entrypoints("conda", name="conda-why") == 1

    commands = manager.get_hook_results("subcommands")
    assert [command.name for command in commands] == ["why"]
    command = commands[0]
    assert command.aliases == ("wtf",)

    assert "conda_why.cli" not in sys.modules
    assert command.configure_parser is not None
    command.configure_parser(argparse.ArgumentParser())
    assert "conda_why.cli" in sys.modules


def test_installed_discovery() -> None:
    check_installed_discovery()


def test_installed_discovery_keeps_command_unloaded(
    tmp_path: Path, cli_environment: dict[str, str]
) -> None:
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=tmp_path,
        env=cli_environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


if __name__ == "__main__":
    check_installed_discovery()
