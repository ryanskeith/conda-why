from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from conda.models.records import PackageRecord

Operation = Literal["create", "install", "update", "remove"]
ErrorKind = Literal["unsatisfiable", "other"]


@dataclass(frozen=True)
class IgnoredConstraint:
    kind: Literal["user", "history", "pinned"]
    spec: str
    source: str = ""


@dataclass(frozen=True)
class SolveRequest:
    operation: Operation
    prefix: Path | None = None
    specs_to_add: tuple[str, ...] = ()
    specs_to_remove: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    subdirs: tuple[str, ...] = ()
    update_modifier: str | None = None
    ignored_constraints: tuple[IgnoredConstraint, ...] = ()
    repodata_fn: str | None = None


@dataclass(frozen=True)
class PinConflict:
    requested_specs: tuple[str, ...]
    pinned_specs: tuple[str, ...]
    prefix: str


@dataclass(frozen=True)
class SolveResult:
    ok: bool
    records: tuple[PackageRecord, ...] = field(default_factory=tuple)
    error_kind: ErrorKind | None = None
    raw_error: str = ""
    pinned_conflict: PinConflict | None = None
    solver_calls: int = 1


@dataclass(frozen=True)
class CandidateInfo:
    name: str
    version: str
    build: str
    subdir: str
    channel: str
    depends: tuple[str, ...] = field(default_factory=tuple)


class SolverAdapter(Protocol):
    name: str
    subdirs: tuple[str, ...]

    def solve(self, request: SolveRequest) -> SolveResult: ...

    def candidates(
        self,
        spec: str,
        python: str | None = None,
        *,
        subdirs: tuple[str, ...] | None = None,
    ) -> list[CandidateInfo]: ...

    def conflict_hint(self, result: SolveResult) -> list[str]: ...
