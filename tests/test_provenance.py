from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from conda_why.provenance import collect_constraints

PRECEDENCE = ("user", "history", "pinned", "installed", "special_case", "transitive")


def by_kind(records: Sequence, kind: str) -> list:
    return [record for record in records if record.constraint.kind == kind]


def by_name(records: Sequence, name: str) -> list:
    return [record for record in records if _name(record.constraint.spec) == name]


def _name(spec: str) -> str:
    return spec.split("=")[0].split()[0]


def test_history_specs_carry_revision_and_timestamp(
    build_prefix: Callable[..., Path],
) -> None:
    prefix = build_prefix(
        history=[
            ("create", ["python=3.9"], "2026-11-01 10:00:00"),
            ("install", ["numpy=1.24"], "2026-11-02 11:30:00"),
        ],
        installed=[("python", "3.9.0"), ("numpy", "1.24.0")],
    )

    records = collect_constraints(mode="query", prefix=prefix)
    history = by_kind(records, "history")

    assert {record.constraint.spec for record in history} == {"python=3.9", "numpy=1.24"}
    python = next(record for record in history if record.constraint.spec == "python=3.9")
    numpy = next(record for record in history if record.constraint.spec == "numpy=1.24")
    assert python.revision == 1
    assert numpy.revision == 2
    assert python.source.endswith("conda-meta/history")
    assert python.timestamp is not None
    assert python.timestamp.tzinfo is not None
    assert python.timestamp.strftime("%Y-%m-%d %H:%M:%S") == "2026-11-01 10:00:00"
    assert numpy.timestamp.strftime("%Y-%m-%d %H:%M:%S") == "2026-11-02 11:30:00"


def test_history_remove_revision_drops_the_spec(
    build_prefix: Callable[..., Path],
) -> None:
    prefix = build_prefix(
        history=[
            ("create", ["numpy=1.24"]),
            ("remove", ["numpy"]),
        ],
        installed=[("numpy", "1.24.0")],
    )

    records = collect_constraints(mode="query", prefix=prefix)

    assert [record.constraint.spec for record in by_kind(records, "history")] == []


def test_prefix_pins_are_parsed(build_prefix: Callable[..., Path]) -> None:
    prefix = build_prefix(pinned=["numpy 1.24.*", "# comment", ""])

    records = collect_constraints(mode="query", prefix=prefix)
    pins = by_kind(records, "pinned")

    assert [record.constraint.spec for record in pins] == ["numpy 1.24.*"]
    assert pins[0].source.endswith("conda-meta/pinned")
    assert pins[0].constraint.relaxable is True


def test_installed_records_are_constraints(build_prefix: Callable[..., Path]) -> None:
    prefix = build_prefix(
        installed=[
            ("python", "3.9.0"),
            ("numpy", "1.24.0", ("python >=3.9,<3.10",)),
        ]
    )

    records = collect_constraints(mode="query", prefix=prefix)
    installed = by_kind(records, "installed")

    assert {record.constraint.spec for record in installed} == {
        "python=3.9.0=h0_0",
        "numpy=1.24.0=h0_0",
    }
    assert all(record.constraint.relaxable is False for record in installed)
    assert all("conda-meta" in record.source for record in installed)


def test_python_minor_pin_is_a_special_case(build_prefix: Callable[..., Path]) -> None:
    prefix = build_prefix(
        history=[("create", ["python=3.9"])],
        installed=[("python", "3.9.0")],
    )

    records = collect_constraints(mode="query", prefix=prefix)
    special = by_kind(records, "special_case")

    assert len(special) == 1
    assert special[0].constraint.spec == "python 3.9.*"
    assert special[0].source == "python-minor-pin"
    assert special[0].constraint.relaxable is False
    assert {record.constraint.spec for record in by_kind(records, "history")} == {
        "python=3.9"
    }


def test_python_minor_pin_is_absent_when_python_is_requested(
    build_prefix: Callable[..., Path],
) -> None:
    prefix = build_prefix(installed=[("python", "3.9.0")])

    records = collect_constraints(
        mode="install", prefix=prefix, user_specs=["python>=3.10"]
    )

    assert by_kind(records, "special_case") == []
    user = by_kind(records, "user")
    assert [record.constraint.spec for record in user] == ["python>=3.10"]


def test_duplicate_constraint_keeps_highest_precedence_source(
    build_prefix: Callable[..., Path],
) -> None:
    prefix = build_prefix(
        history=[("create", ["numpy=1.24"])],
        pinned=["numpy=1.24"],
        installed=[("numpy", "1.24.0")],
    )

    records = collect_constraints(mode="query", prefix=prefix)
    numpy = [
        record for record in by_name(records, "numpy")
        if record.constraint.spec == "numpy=1.24"
    ]

    assert len(numpy) == 1
    assert numpy[0].constraint.kind == "history"
    assert "conda-meta/pinned" in numpy[0].detail


def test_query_mode_ignores_user_specs(build_prefix: Callable[..., Path]) -> None:
    prefix = build_prefix(installed=[("python", "3.9.0")])

    records = collect_constraints(mode="query", prefix=prefix, user_specs=["numpy"])

    assert by_kind(records, "user") == []


def test_create_mode_without_a_prefix_has_no_environment_constraints(
    tmp_path: Path,
) -> None:
    records = collect_constraints(mode="create", prefix=tmp_path / "missing")

    assert records == []


def test_records_are_ordered_by_precedence(build_prefix: Callable[..., Path]) -> None:
    prefix = build_prefix(
        history=[("create", ["python=3.9"])],
        pinned=["numpy 1.24.*"],
        installed=[("python", "3.9.0")],
    )

    records = collect_constraints(
        mode="install", prefix=prefix, user_specs=["foo=2.0"]
    )

    indexes = [PRECEDENCE.index(record.constraint.kind) for record in records]
    assert indexes == sorted(indexes)
    assert set(indexes) == {0, 1, 2, 3, 4}
