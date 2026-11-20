from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from conda_why.adapters.base import SolveRequest
from conda_why.adapters.crs import CrsAdapter
from conda_why.mus import minimize
from conda_why.provenance import collect_constraints
from fixtures.synth_channel import channel_url


@pytest.fixture
def adapter(synth_channel: Path) -> CrsAdapter:
    return CrsAdapter(
        channels=[channel_url(synth_channel)],
        subdirs=("osx-arm64", "noarch"),
    )


def constraints(
    prefix: Path, operation: str, specs: Sequence[str]
) -> list:
    return collect_constraints(mode=operation, prefix=prefix, user_specs=specs)


def kinds(conflict) -> set[tuple[str, str]]:
    return {
        (record.constraint.kind, record.constraint.spec)
        for record in conflict.provenance
    }


def test_user_conflict_minimal_set(
    adapter: CrsAdapter, tmp_path: Path
) -> None:
    prefix = tmp_path / "newenv"
    specs = ("python=3.9", "numpy=2.1")

    conflict = minimize(
        adapter,
        SolveRequest(operation="create", prefix=prefix, specs_to_add=specs),
        constraints(prefix, "create", specs),
    )

    assert kinds(conflict) == {("user", "python=3.9"), ("user", "numpy=2.1")}
    assert conflict.solver == "rattler"
    assert conflict.solver_calls == 3


def test_history_constraint_is_part_of_the_set(
    adapter: CrsAdapter, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        history=[("create", ["libbaz=1.0"])],
        installed=[("python", "3.9.0"), ("libbaz", "1.0")],
    )

    conflict = minimize(
        adapter,
        SolveRequest(operation="install", prefix=prefix, specs_to_add=("foo=2.0",)),
        constraints(prefix, "install", ("foo=2.0",)),
    )

    found = kinds(conflict)
    assert ("user", "foo=2.0") in found
    assert ("history", "libbaz=1.0") in found
    assert ("installed", "libbaz=1.0=h0_0") in found
    assert conflict.solver_calls == 3


def test_pinned_constraint_is_part_of_the_set(
    adapter: CrsAdapter, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        installed=[("python", "3.9.0")],
        pinned=["libbaz 1.0"],
    )

    conflict = minimize(
        adapter,
        SolveRequest(operation="install", prefix=prefix, specs_to_add=("foo=2.0",)),
        constraints(prefix, "install", ("foo=2.0",)),
    )

    found = kinds(conflict)
    assert ("user", "foo=2.0") in found
    assert ("pinned", "libbaz 1.0") in found
    assert conflict.solver_calls == 3


def test_seed_reduces_solver_calls(
    adapter: CrsAdapter, tmp_path: Path
) -> None:
    prefix = tmp_path / "newenv"
    specs = ("foo=2.0", "zlib", "python=3.9", "numpy=2.1")
    records = constraints(prefix, "create", specs)
    request = SolveRequest(operation="create", prefix=prefix, specs_to_add=specs)

    unseeded = minimize(adapter, request, records, seed=())
    seeded = minimize(
        adapter, request, records, seed=("numpy=2.1", "python=3.9")
    )

    assert kinds(seeded) == kinds(unseeded) == {
        ("user", "numpy=2.1"),
        ("user", "python=3.9"),
    }
    assert seeded.solver_calls < unseeded.solver_calls
    assert seeded.solver_calls == 4


def test_wrong_seed_falls_back_to_the_full_set(
    adapter: CrsAdapter, tmp_path: Path
) -> None:
    prefix = tmp_path / "newenv"
    specs = ("foo=2.0", "zlib", "python=3.9", "numpy=2.1")
    records = constraints(prefix, "create", specs)
    request = SolveRequest(operation="create", prefix=prefix, specs_to_add=specs)

    unseeded = minimize(adapter, request, records, seed=())
    wrong = minimize(adapter, request, records, seed=("foo=2.0",))

    assert kinds(wrong) == kinds(unseeded)
    assert wrong.solver_calls == unseeded.solver_calls + 1


def test_partial_seed_falls_back_to_the_full_set(
    adapter: CrsAdapter, tmp_path: Path
) -> None:
    prefix = tmp_path / "newenv"
    specs = ("foo=2.0", "zlib", "python=3.9", "numpy=2.1")
    records = constraints(prefix, "create", specs)
    request = SolveRequest(operation="create", prefix=prefix, specs_to_add=specs)

    unseeded = minimize(adapter, request, records, seed=())
    partial = minimize(adapter, request, records, seed=("numpy=2.1",))

    assert kinds(partial) == kinds(unseeded)
    assert partial.solver_calls == unseeded.solver_calls + 1


def test_special_case_joins_the_conflict_when_the_seed_names_it(
    adapter: CrsAdapter, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(installed=[("python", "3.9.0")])

    conflict = minimize(
        adapter,
        SolveRequest(operation="install", prefix=prefix, specs_to_add=("numpy=2.1",)),
        constraints(prefix, "install", ("numpy=2.1",)),
        seed=("numpy=2.1", "python=3.9.*"),
    )

    found = kinds(conflict)
    assert ("user", "numpy=2.1") in found
    assert ("special_case", "python 3.9.*") in found
    assert ("installed", "python=3.9.0=h0_0") in found
    assert conflict.solver_calls == 2


def test_conflict_set_keeps_precedence_order(
    adapter: CrsAdapter, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(
        history=[("create", ["libbaz=1.0"])],
        installed=[("python", "3.9.0"), ("libbaz", "1.0")],
        pinned=["zlib 1.0"],
    )

    conflict = minimize(
        adapter,
        SolveRequest(operation="install", prefix=prefix, specs_to_add=("foo=2.0",)),
        constraints(prefix, "install", ("foo=2.0",)),
    )

    order = [
        "user",
        "history",
        "pinned",
        "installed",
        "special_case",
        "transitive",
    ]
    indexes = [order.index(record.constraint.kind) for record in conflict.provenance]
    assert indexes == sorted(indexes)
    assert len(conflict.provenance) == len(conflict.constraints)


def test_no_relaxable_constraints_means_no_probes(
    adapter: CrsAdapter, build_prefix: Callable[..., Path]
) -> None:
    prefix = build_prefix(installed=[("python", "3.9.0")])

    conflict = minimize(
        adapter,
        SolveRequest(operation="install", prefix=prefix, specs_to_add=("numpy=2.1",)),
        [record for record in constraints(prefix, "install", ())],
    )

    assert conflict.constraints == []
    assert conflict.solver_calls == 1
