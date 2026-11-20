from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from conda_why.schema import WhyReport
from fixtures.synth_channel import channel_url

RunJob = Callable[..., subprocess.CompletedProcess[str]]


@pytest.fixture
def run_job(run_conda: RunJob, synth_channel: Path) -> RunJob:
    def run(*arguments: str, json_output: bool = False, env=None, alias: str = "why"):
        command = [
            alias,
            *arguments,
            "-c",
            channel_url(synth_channel),
            "--override-channels",
        ]
        if json_output:
            command.append("--json")
        return run_conda(*command, env=env)

    return run


def payload(result) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def diagnosis_kinds(data: dict) -> list[str]:
    return [
        finding["provenance"]["constraint"]["kind"]
        for finding in data["diagnosis"]["findings"]
    ]


def test_unsupported_python_from_the_request(
    run_job: RunJob, tmp_path: Path
) -> None:
    result = run_job(
        "create", "python=3.9", "numpy=2.1", "-p", str(tmp_path / "env"), json_output=True
    )
    data = payload(result)

    assert data["mode"] == "job"
    assert data["ok"] is False
    assert data["diagnosis"]["kind"] == "unsupported_python"
    assert "user" in diagnosis_kinds(data)
    assert 1 <= len(data["diagnosis"]["next_steps"]) <= 2
    assert len(data["diagnosis"]["findings"]) <= 3
    assert {constraint["spec"] for constraint in data["conflict"]["constraints"]} >= {
        "python=3.9",
        "numpy=2.1",
    }
    claims = " ".join(finding["claim"] for finding in data["diagnosis"]["findings"])
    steps = " ".join(step["text"] for step in data["diagnosis"]["next_steps"])
    assert "Python 3.10" in claims
    assert ">=3.10" in steps
    assert '"numpy<2.1"' in steps


def test_unsupported_python_from_history(
    run_job: RunJob, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        history=[("create", ["python=3.9"], "2026-11-01 10:00:00")],
        installed=[("python", "3.9.0")],
    )

    result = run_job("install", "numpy=2.1", "-p", str(prefix), json_output=True)
    data = payload(result)

    assert data["diagnosis"]["kind"] == "unsupported_python"
    assert "history" in diagnosis_kinds(data)
    revisions = [
        finding["provenance"]["revision"]
        for finding in data["diagnosis"]["findings"]
        if finding["provenance"]["constraint"]["kind"] == "history"
    ]
    assert revisions == [1]


def test_update_needs_a_newer_python(
    run_job: RunJob, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        installed=[("python", "3.9.0"), ("numpy", "1.24.0")],
    )

    result = run_job("update", "numpy=2.1", "-p", str(prefix), json_output=True)
    data = payload(result)

    assert data["diagnosis"]["kind"] == "unsupported_python"
    assert {"special_case", "installed"} & set(diagnosis_kinds(data))


def test_two_user_specs_are_incompatible(
    run_job: RunJob, tmp_path: Path
) -> None:
    result = run_job(
        "create", "foo=2.0", "bar", "-p", str(tmp_path / "env"), json_output=True
    )
    data = payload(result)

    assert data["diagnosis"]["kind"] == "incompatible_specs"
    subjects = {finding["subject"] for finding in data["diagnosis"]["findings"]}
    assert {"foo", "bar"} <= subjects
    assert data["diagnosis"]["headline"].startswith("foo")
    assert 1 <= len(data["diagnosis"]["next_steps"]) <= 2


def test_installed_requirement_blocks_the_request(
    run_job: RunJob, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        history=[("create", ["foo=1.0"])],
        installed=[
            ("python", "3.9.0"),
            ("libbaz", "1.0"),
            ("foo", "1.0", ("libbaz <2",)),
        ],
    )

    result = run_job("install", "libbaz=2.0", "-p", str(prefix), json_output=True)
    data = payload(result)

    assert data["diagnosis"]["kind"] == "installed_constraint"
    assert "installed" in diagnosis_kinds(data)
    assert data["diagnosis"]["next_steps"]


def test_pinned_package_blocks_the_request(
    run_job: RunJob, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        installed=[("python", "3.9.0")],
        pinned=["numpy 1.24.*"],
    )

    result = run_job("install", "numpy=2.1", "-p", str(prefix), json_output=True)
    data = payload(result)

    assert data["diagnosis"]["kind"] == "pinned_constraint"
    assert "pinned" in diagnosis_kinds(data)
    assert "conda-meta/pinned" in data["diagnosis"]["findings"][0]["provenance"]["source"]
    assert data["diagnosis"]["next_steps"]


def test_package_not_found(
    run_job: RunJob, tmp_path: Path
) -> None:
    result = run_job(
        "create", "definitely-not-a-package", "-p", str(tmp_path / "env"), json_output=True
    )
    data = payload(result)

    assert data["diagnosis"]["kind"] == "package_not_found"
    assert len(data["diagnosis"]["next_steps"]) == 2


def test_package_for_another_platform(
    run_job: RunJob, tmp_path: Path
) -> None:
    result = run_job("create", "onlylinux", "-p", str(tmp_path / "env"), json_output=True)
    data = payload(result)

    assert data["diagnosis"]["kind"] == "unsupported_platform"
    assert "osx-arm64" in data["diagnosis"]["headline"]
    assert data["diagnosis"]["next_steps"]


def test_healthy_solve_reports_implicit_constraints(
    run_job: RunJob, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        history=[("create", ["python=3.9"])],
        installed=[("python", "3.9.0")],
    )

    result = run_job("install", "zlib", "-p", str(prefix), json_output=True)
    data = payload(result)

    assert data["ok"] is True
    assert data["diagnosis"] is None
    kinds = {record["constraint"]["kind"] for record in data["implicit"]}
    assert {"history", "special_case"} <= kinds
    assert {"python=3.9"} <= {
        record["constraint"]["spec"] for record in data["implicit"]
    }


def test_unknown_fallback_offers_no_advice(
    run_job: RunJob, tmp_path: Path
) -> None:
    result = run_job("create", "broken", "-p", str(tmp_path / "env"), json_output=True)
    data = payload(result)

    assert data["diagnosis"]["kind"] == "unknown"
    assert data["diagnosis"]["next_steps"] == []
    assert len(data["diagnosis"]["findings"]) <= 3


def test_next_steps_echo_environment_and_channel_flags(
    run_job: RunJob, synth_channel: Path, tmp_path: Path
) -> None:
    prefix = tmp_path / "env"
    result = run_job("create", "python=3.9", "numpy=2.1", "-p", str(prefix))

    assert result.returncode == 0, result.stderr
    output = result.stdout
    assert "Options:" in output
    assert f"-p {prefix}" in output
    assert f"-c {channel_url(synth_channel)}" in output
    assert "--override-channels" in output


def test_explain_adds_solver_evidence(
    run_job: RunJob, tmp_path: Path
) -> None:
    base = ("create", "python=3.9", "numpy=2.1", "-p", str(tmp_path / "env"))

    plain = run_job(*base)
    explained = run_job(*base, "--explain")

    assert plain.returncode == explained.returncode == 0
    assert "minimal conflict set" not in plain.stdout
    assert "Cannot solve" not in plain.stdout
    assert "minimal conflict set" in explained.stdout
    assert "Cannot solve" in explained.stdout


def test_job_json_round_trips(
    run_job: RunJob, tmp_path: Path
) -> None:
    result = run_job(
        "create", "python=3.9", "numpy=2.1", "-p", str(tmp_path / "env"), json_output=True
    )
    data = payload(result)

    assert "Cannot solve" in data["conflict"]["raw_error"]
    assert data["conflict"]["solver"] == "rattler"
    assert WhyReport.from_dict(data).to_dict() == data


def test_job_failure_does_not_modify_the_prefix(
    run_job: RunJob, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        history=[("create", ["python=3.9"])],
        installed=[("python", "3.9.0")],
    )

    def snapshot(root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    before = snapshot(prefix)
    run_job("install", "numpy=2.1", "-p", str(prefix), json_output=True)

    assert snapshot(prefix) == before


def test_remove_succeeds(
    run_job: RunJob, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(installed=[("python", "3.9.0"), ("zlib", "1.0")])

    result = run_job("remove", "zlib", "-p", str(prefix), json_output=True)
    data = payload(result)

    assert data["ok"] is True
    assert data["diagnosis"] is None


def test_job_missing_environment_exits_one(
    run_job: RunJob, tmp_path: Path
) -> None:
    result = run_job("install", "python", "-p", str(tmp_path / "missing"))

    assert result.returncode == 1
    assert "environment" in result.stderr.lower()


def test_job_invalid_spec_exits_one(
    run_job: RunJob, tmp_path: Path
) -> None:
    result = run_job(
        "create", "numpy==", "-p", str(tmp_path / "env"), json_output=True
    )

    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["mode"] == "job"


def test_alias_matches_why_for_jobs(
    run_job: RunJob, tmp_path: Path
) -> None:
    arguments = ("create", "python=3.9", "numpy=2.1", "-p", str(tmp_path / "env"))

    why = run_job(*arguments, json_output=True)
    wtf = run_job(*arguments, json_output=True, alias="wtf")

    assert why.returncode == wtf.returncode
    assert why.stdout == wtf.stdout
