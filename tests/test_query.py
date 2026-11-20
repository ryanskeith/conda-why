from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from conda_why.schema import WhyReport


@pytest.fixture
def constrained_prefix(build_prefix: Callable[..., Path]) -> Path:
    return build_prefix(
        history=[("create", ["python=3.9"], "2026-11-01 10:00:00")],
        installed=[("python", "3.9.0")],
    )


RunConda = Callable[..., subprocess.CompletedProcess[str]]


def test_query_reports_each_source(run_conda: RunConda, constrained_prefix: Path) -> None:
    result = run_conda("why", "query", "python", "-p", str(constrained_prefix))

    assert result.returncode == 0, result.stderr
    output = result.stdout
    assert "python is constrained by 3 sources" in output
    assert "history" in output
    assert "installed" in output
    assert "special_case" in output
    assert "python-minor-pin" in output
    assert "revision 1" in output
    assert str(constrained_prefix / "conda-meta" / "history") in output
    assert "python=3.9" in output


def test_query_without_version_has_no_annotation(
    run_conda: RunConda, constrained_prefix: Path
) -> None:
    result = run_conda("why", "query", "python", "-p", str(constrained_prefix))

    assert result.returncode == 0
    assert "allows" not in result.stdout
    assert "excludes" not in result.stdout


def test_query_annotates_allows(run_conda: RunConda, constrained_prefix: Path) -> None:
    result = run_conda("why", "query", "python=3.9", "-p", str(constrained_prefix))

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("allows 3.9") == 3


def test_query_annotates_excludes(run_conda: RunConda, constrained_prefix: Path) -> None:
    result = run_conda("why", "query", "python=3.10", "-p", str(constrained_prefix))

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("excludes 3.10") == 3


def test_query_without_a_match_exits_one(
    run_conda: RunConda, constrained_prefix: Path
) -> None:
    result = run_conda("why", "query", "does-not-exist", "-p", str(constrained_prefix))

    assert result.returncode == 1
    assert result.stdout == ""
    assert "no constraint on 'does-not-exist'" in result.stderr


def test_query_without_a_match_writes_ok_false_json(
    run_conda: RunConda, constrained_prefix: Path
) -> None:
    result = run_conda(
        "why", "query", "does-not-exist", "-p", str(constrained_prefix), "--json"
    )

    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["mode"] == "query"
    assert data["matches"] == []
    assert WhyReport.from_dict(data).to_dict() == data


def test_query_json_round_trips(
    run_conda: RunConda, constrained_prefix: Path
) -> None:
    result = run_conda("why", "query", "python", "-p", str(constrained_prefix), "--json")

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["schema_version"] == "0.1"
    assert data["ok"] is True
    kinds = {match["provenance"]["constraint"]["kind"] for match in data["matches"]}
    assert kinds == {"history", "installed", "special_case"}

    history = next(
        match
        for match in data["matches"]
        if match["provenance"]["constraint"]["kind"] == "history"
    )
    assert history["provenance"]["revision"] == 1
    assert history["provenance"]["constraint"]["spec"] == "python=3.9"
    timestamp = datetime.fromisoformat(history["provenance"]["timestamp"])
    assert timestamp.tzinfo is not None

    assert WhyReport.from_dict(data).to_dict() == data


def test_query_reports_pinned_packages_from_condarc(
    run_conda: RunConda, build_prefix: Callable[..., Path], tmp_path: Path
) -> None:
    condarc = tmp_path / "condarc"
    condarc.write_text('pinned_packages:\n  - "numpy 1.24.*"\n', encoding="utf-8")
    prefix = build_prefix(installed=[("numpy", "1.24.0")])

    result = run_conda(
        "why",
        "query",
        "numpy",
        "-p",
        str(prefix),
        env={"CONDARC": str(condarc)},
    )

    assert result.returncode == 0, result.stderr
    assert "pinned" in result.stdout
    assert "pinned_packages" in result.stdout
    assert "numpy 1.24.*" in result.stdout


def test_query_resolves_environment_by_name(
    run_conda: RunConda, build_prefix: Callable[..., Path], tmp_path: Path
) -> None:
    build_prefix(name="envs/myenv", installed=[("python", "3.9.0")])

    result = run_conda(
        "why",
        "query",
        "python",
        "-n",
        "myenv",
        env={"CONDA_ENVS_PATH": str(tmp_path / "envs")},
    )

    assert result.returncode == 0, result.stderr
    assert "installed" in result.stdout


def test_query_missing_environment_exits_one(
    run_conda: RunConda, tmp_path: Path
) -> None:
    missing = tmp_path / "does-not-exist"
    result = run_conda("why", "query", "python", "-p", str(missing))

    assert result.returncode == 1
    assert result.stdout == ""
    assert "environment" in result.stderr.lower()

    json_result = run_conda("why", "query", "python", "-p", str(missing), "--json")
    assert json_result.returncode == 1
    data = json.loads(json_result.stdout)
    assert data["ok"] is False
    assert data["matches"] == []


def test_query_invalid_spec_exits_one(
    run_conda: RunConda, constrained_prefix: Path
) -> None:
    result = run_conda("why", "query", "numpy==", "-p", str(constrained_prefix))

    assert result.returncode == 1
    assert result.stdout == ""
    assert "invalid" in result.stderr.lower()

    json_result = run_conda(
        "why", "query", "numpy==", "-p", str(constrained_prefix), "--json"
    )
    assert json_result.returncode == 1
    assert json.loads(json_result.stdout)["ok"] is False


def test_query_does_not_modify_the_prefix(
    run_conda: RunConda, constrained_prefix: Path
) -> None:
    def snapshot(root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    before = snapshot(constrained_prefix)
    result = run_conda(
        "why", "query", "python=3.10", "-p", str(constrained_prefix), "--json"
    )

    assert result.returncode == 0
    assert snapshot(constrained_prefix) == before
