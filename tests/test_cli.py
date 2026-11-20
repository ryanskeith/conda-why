from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest


@pytest.mark.parametrize("command", ["why", "wtf"])
def test_help_exits_zero(
    run_conda: Callable[..., subprocess.CompletedProcess[str]], command: str
) -> None:
    result = run_conda(command, "--help")
    assert result.returncode == 0, result.stderr
    assert "MODE" in result.stdout
    assert "query" in result.stdout
    assert "install" in result.stdout


@pytest.mark.parametrize(
    ("arguments", "status", "message"),
    [
        (("--last",), 2, "not implemented: --last"),
        (("--compare-solvers",), 2, "not implemented: only one adapter available"),
        (
            ("--compare-solvers", "install", "numpy"),
            2,
            "not implemented: only one adapter available",
        ),
        ((), 1, "mode"),
        (("python",), 2, "invalid choice"),
        (("query",), 1, "exactly one"),
        (("query", "numpy", "scipy"), 1, "exactly one"),
        (("--last", "numpy"), 2, "invalid choice"),
        (("install", "--unknown-option"), 2, "unrecognized arguments"),
        (("install", "-n", "a", "-p", "b"), 2, "not allowed with argument"),
    ],
    ids=[
        "last-stub",
        "compare-solvers-stub",
        "compare-solvers-with-mode-stub",
        "missing-mode",
        "bare-spec",
        "query-missing-spec",
        "query-extra-spec",
        "last-with-spec",
        "unknown-option",
        "name-and-prefix",
    ],
)
def test_cli_result(
    run_conda: Callable[..., subprocess.CompletedProcess[str]],
    arguments: tuple[str, ...],
    status: int,
    message: str,
) -> None:
    result = run_conda("why", *arguments)
    assert result.returncode == status, result.stderr
    output = result.stdout if status == 0 else result.stderr
    assert message in output
    if status != 0:
        assert result.stdout == ""


@pytest.mark.parametrize(
    "arguments",
    [
        (
            "--compare-solvers",
            "install",
            "--json",
            "-n",
            "base",
            "-c",
            "conda-forge",
            "--override-channels",
            "numpy",
        ),
        ("--json", "--compare-solvers", "install", "-n", "base", "numpy"),
        ("--explain", "--compare-solvers", "install", "numpy"),
        (
            "--compare-solvers",
            "install",
            "numpy",
            "-p",
            "/tmp/conda-why-missing-prefix",
        ),
    ],
    ids=["flags-after-mode", "flags-before-mode", "explain-before", "prefix-after-spec"],
)
def test_flags_parse_in_any_position(
    run_conda: Callable[..., subprocess.CompletedProcess[str]],
    arguments: tuple[str, ...],
) -> None:
    result = run_conda("why", *arguments)
    assert result.returncode == 2
    assert "not implemented: only one adapter available" in result.stderr
    assert "unrecognized arguments" not in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [("--last",), ("--compare-solvers",)],
    ids=["last", "compare-solvers"],
)
def test_alias_matches_why(
    run_conda: Callable[..., subprocess.CompletedProcess[str]],
    arguments: tuple[str, ...],
) -> None:
    why = run_conda("why", *arguments)
    wtf = run_conda("wtf", *arguments)
    assert wtf.returncode == why.returncode
    assert wtf.stdout == why.stdout


def test_alias_is_listed_in_conda_help(
    run_conda: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = run_conda("--help")
    assert result.returncode == 0, result.stderr
    assert "wtf" in result.stdout


def test_global_json_flag_is_accepted(
    run_conda: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = run_conda("--json", "why", "--compare-solvers")
    assert result.returncode == 2
    assert "not implemented: only one adapter available" in result.stderr
