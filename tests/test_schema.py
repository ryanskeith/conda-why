from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from conda_why.schema import (
    SCHEMA_VERSION,
    ConflictSet,
    Constraint,
    Diagnosis,
    Finding,
    NextStep,
    ProvenanceRecord,
    WhyReport,
)


def provenance(
    spec: str = "python=3.9",
    kind: str = "history",
    *,
    source: str = "/env/conda-meta/history",
    revision: int | None = 1,
    timestamp: datetime | None = None,
    detail: str = "",
) -> ProvenanceRecord:
    return ProvenanceRecord(
        constraint=Constraint.create(spec, kind),
        source=source,
        revision=revision,
        timestamp=timestamp,
        detail=detail,
    )


def full_report() -> WhyReport:
    user = provenance("numpy=2.1", "user", source="command line", revision=None)
    history = provenance(
        "python=3.9",
        "history",
        revision=3,
        timestamp=datetime(2026, 11, 19, 12, 0, tzinfo=timezone(timedelta(hours=-8))),
        detail="create specs",
    )
    finding = Finding(
        subject="numpy",
        claim="numpy 2.1 requires Python >=3.10.",
        provenance=user,
    )
    diagnosis = Diagnosis(
        kind="unsupported_python",
        headline="numpy 2.1 is not compatible with Python 3.9 in this environment.",
        findings=[finding],
        next_steps=[
            NextStep(text='conda install "numpy<2.1"', note="keep Python 3.9"),
        ],
    )
    conflict = ConflictSet(
        constraints=[user.constraint, history.constraint],
        provenance=[user, history],
        solver="rattler",
        solver_calls=4,
    )
    return WhyReport(
        mode="job",
        ok=False,
        diagnosis=diagnosis,
        conflict=conflict,
        implicit=[history],
    )


@pytest.mark.parametrize(
    ("kind", "relaxable"),
    [
        ("user", True),
        ("history", True),
        ("pinned", True),
        ("installed", False),
        ("special_case", False),
        ("transitive", False),
    ],
)
def test_constraint_relaxable_mapping(kind: str, relaxable: bool) -> None:
    constraint = Constraint.create("python=3.9", kind)
    assert constraint.relaxable is relaxable


def test_constraint_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        Constraint.create("python=3.9", "guessed")


def test_constraint_is_frozen() -> None:
    constraint = Constraint.create("python=3.9", "history")
    with pytest.raises(FrozenInstanceError):
        setattr(constraint, "spec", "python=3.10")


def test_query_report_shape() -> None:
    report = WhyReport(mode="query", ok=True, matches=[])
    data = report.to_dict()

    assert data == {
        "schema_version": SCHEMA_VERSION,
        "mode": "query",
        "ok": True,
        "diagnosis": None,
        "conflict": None,
        "implicit": [],
        "matches": [],
    }


def test_full_report_round_trips() -> None:
    report = full_report()
    data = report.to_dict()

    assert data["schema_version"] == "0.1"
    assert data["conflict"]["solver"] == "rattler"
    assert data["conflict"]["provenance"][1]["timestamp"] == "2026-11-19T12:00:00-08:00"
    assert data["diagnosis"]["next_steps"][0]["note"] == "keep Python 3.9"

    restored = WhyReport.from_dict(data)
    assert restored == report
    assert restored.to_dict() == data


def test_from_dict_requires_schema_version() -> None:
    data = WhyReport(mode="query", ok=True).to_dict()
    del data["schema_version"]

    with pytest.raises(ValueError):
        WhyReport.from_dict(data)


def test_list_order_is_preserved() -> None:
    first = provenance("python=3.9", "history")
    second = provenance("numpy=1.24", "installed", revision=None)
    report = WhyReport(
        mode="query",
        ok=True,
        matches=[
            Finding(subject="python", claim="first", provenance=first),
            Finding(subject="numpy", claim="second", provenance=second),
        ],
    )

    restored = WhyReport.from_dict(report.to_dict())

    assert [finding.subject for finding in restored.matches] == ["python", "numpy"]
