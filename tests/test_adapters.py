from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from conda_why.adapters.base import IgnoredConstraint, Operation, SolveRequest
from conda_why.adapters.crs import CrsAdapter
from fixtures.synth_channel import channel_url


@pytest.fixture
def adapter(synth_channel: Path) -> CrsAdapter:
    return CrsAdapter(
        channels=[channel_url(synth_channel)],
        subdirs=("osx-arm64", "noarch"),
    )


def request(
    operation: Operation,
    prefix: Path,
    *,
    add: Sequence[str] = (),
    remove: Sequence[str] = (),
    ignore: Sequence[IgnoredConstraint] = (),
) -> SolveRequest:
    return SolveRequest(
        operation=operation,
        prefix=prefix,
        specs_to_add=tuple(add),
        specs_to_remove=tuple(remove),
        ignored_constraints=tuple(ignore),
    )


def test_adapter_name(adapter: CrsAdapter) -> None:
    assert adapter.name == "rattler"


def test_create_success(adapter: CrsAdapter, tmp_path: Path) -> None:
    prefix = tmp_path / "newenv"

    result = adapter.solve(
        request("create", prefix, add=("python=3.9", "numpy=1.24"))
    )

    assert result.ok
    assert result.error_kind is None
    assert {"python", "numpy"} <= {record.name for record in result.records}
    assert not prefix.exists()


def test_unsatisfiable_create(adapter: CrsAdapter, tmp_path: Path) -> None:
    result = adapter.solve(
        request("create", tmp_path / "newenv", add=("python=3.9", "numpy=2.1"))
    )

    assert not result.ok
    assert result.error_kind == "unsatisfiable"
    assert "Cannot solve" in result.raw_error
    assert result.records == ()


def test_unsatisfiable_does_not_create_the_prefix(
    adapter: CrsAdapter, tmp_path: Path
) -> None:
    prefix = tmp_path / "newenv"

    adapter.solve(request("create", prefix, add=("python=3.9", "numpy=2.1")))

    assert not prefix.exists()


def test_conflict_hint_parses_the_diagnosis(
    adapter: CrsAdapter, tmp_path: Path
) -> None:
    result = adapter.solve(
        request("create", tmp_path / "newenv", add=("python=3.9", "numpy=2.1"))
    )

    hints = adapter.conflict_hint(result)

    assert any(hint.startswith("numpy") and "2.1" in hint for hint in hints)
    assert any(hint.startswith("python") for hint in hints)


def test_conflict_hint_is_empty_on_success(
    adapter: CrsAdapter, tmp_path: Path
) -> None:
    result = adapter.solve(request("create", tmp_path / "newenv", add=("numpy=1.24",)))

    assert adapter.conflict_hint(result) == []


def test_missing_package_is_a_failed_solve(
    adapter: CrsAdapter, tmp_path: Path
) -> None:
    result = adapter.solve(request("create", tmp_path / "newenv", add=("nope",)))

    assert not result.ok
    assert result.error_kind == "unsatisfiable"
    assert "nope" in result.raw_error


def test_pin_conflict_is_reported_as_a_pinned_conflict(
    adapter: CrsAdapter, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        installed=[("python", "3.9.0")],
        pinned=["numpy 1.24.*"],
    )

    result = adapter.solve(request("install", prefix, add=("numpy=2.1",)))

    assert not result.ok
    assert result.error_kind == "unsatisfiable"
    assert result.pinned_conflict is not None
    assert any("numpy" in spec for spec in result.pinned_conflict.pinned_specs)
    assert "numpy=2.1" in " ".join(result.pinned_conflict.requested_specs)


def test_history_constraint_blocks_and_can_be_ignored(
    adapter: CrsAdapter, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        history=[("create", ["libbaz=1.0"])],
        installed=[("python", "3.9.0"), ("libbaz", "1.0")],
    )

    blocked = adapter.solve(request("install", prefix, add=("foo=2.0",)))
    assert not blocked.ok
    assert blocked.error_kind == "unsatisfiable"

    relaxed = adapter.solve(
        request(
            "install",
            prefix,
            add=("foo=2.0",),
            ignore=(
                IgnoredConstraint(
                    kind="history",
                    spec="libbaz=1.0",
                    source=str(prefix / "conda-meta" / "history"),
                ),
            ),
        )
    )
    assert relaxed.ok
    assert {"foo", "libbaz"} <= {record.name for record in relaxed.records}


def test_prefix_pin_blocks_and_can_be_ignored(
    adapter: CrsAdapter, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        installed=[("python", "3.9.0")],
        pinned=["libbaz 1.0"],
    )

    blocked = adapter.solve(request("install", prefix, add=("foo=2.0",)))
    assert not blocked.ok

    relaxed = adapter.solve(
        request(
            "install",
            prefix,
            add=("foo=2.0",),
            ignore=(
                IgnoredConstraint(
                    kind="pinned",
                    spec="libbaz 1.0",
                    source=str(prefix / "conda-meta" / "pinned"),
                ),
            ),
        )
    )
    assert relaxed.ok
    assert any(record.name == "libbaz" and record.version == "2.0" for record in relaxed.records)


def test_ignored_user_spec_is_omitted(
    adapter: CrsAdapter, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(installed=[("python", "3.9.0")])

    result = adapter.solve(
        request(
            "install",
            prefix,
            add=("foo=2.0", "bar"),
            ignore=(IgnoredConstraint(kind="user", spec="foo=2.0"),),
        )
    )

    assert result.ok
    names = {record.name for record in result.records}
    assert "bar" in names
    assert "foo" not in names


def test_remove_drops_the_package(
    adapter: CrsAdapter, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        installed=[
            ("python", "3.9.0"),
            ("libbaz", "1.0"),
            ("foo", "1.0", ("libbaz <2",)),
        ]
    )

    result = adapter.solve(request("remove", prefix, remove=("foo",)))

    assert result.ok
    assert "foo" not in {record.name for record in result.records}


def test_candidates_return_metadata(adapter: CrsAdapter) -> None:
    candidates = adapter.candidates("numpy")

    assert {candidate.version for candidate in candidates} == {
        "1.24.0",
        "2.1.0",
        "2.1.1",
    }
    candidate = candidates[0]
    assert candidate.name == "numpy"
    assert candidate.subdir == "osx-arm64"
    assert candidate.channel
    assert candidate.depends


def test_candidates_filter_by_python_version(adapter: CrsAdapter) -> None:
    candidates = adapter.candidates("numpy", python="3.9.0")

    assert {candidate.version for candidate in candidates} == {"1.24.0"}


def test_candidates_for_unknown_package(adapter: CrsAdapter) -> None:
    assert adapter.candidates("does-not-exist") == []


def test_solver_calls_count_only_solves(
    adapter: CrsAdapter, tmp_path: Path
) -> None:
    assert adapter.solver_calls == 0
    result = adapter.solve(
        request("create", tmp_path / "newenv", add=("python=3.9", "numpy=2.1"))
    )
    adapter.candidates("numpy")
    adapter.conflict_hint(result)

    assert adapter.solver_calls == 1


def test_failing_solve_does_not_modify_the_prefix(
    adapter: CrsAdapter, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        history=[("create", ["libbaz=1.0"])],
        installed=[("python", "3.9.0"), ("libbaz", "1.0")],
    )

    def snapshot(root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    before = snapshot(prefix)
    adapter.solve(request("install", prefix, add=("foo=2.0",)))

    assert snapshot(prefix) == before
