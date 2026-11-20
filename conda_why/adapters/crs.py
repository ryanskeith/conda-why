from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Sequence

from conda.base.constants import UpdateModifier
from conda.base.context import context
from conda.core.index import Index
from conda.exceptions import (
    PackagesNotFoundError,
    SpecsConfigurationConflictError,
    UnsatisfiableError,
)
from conda.history import History
from conda.models.channel import Channel
from conda.models.match_spec import MatchSpec
from conda.models.records import PackageRecord
from conda.models.version import VersionOrder

from .base import (
    CandidateInfo,
    ErrorKind,
    IgnoredConstraint,
    PinConflict,
    SolveRequest,
    SolveResult,
)

if TYPE_CHECKING:
    from conda.models.records import PackageRecord as PackageRecordType

BACKEND_NAME = "rattler"
_BOX_CHARS = "─│└├"


class CrsAdapter:
    name = BACKEND_NAME

    def __init__(
        self,
        channels: Sequence[str] = (),
        subdirs: Sequence[str] = (),
        repodata_fn: str | None = None,
    ) -> None:
        self.channels = tuple(channels)
        self.subdirs = tuple(subdirs) or tuple(context.subdirs)
        self.repodata_fn = repodata_fn
        self.solver_calls = 0
        self._indexes: dict[tuple[tuple[str, ...], tuple[str, ...]], Index] = {}

    def solve(self, request: SolveRequest) -> SolveResult:
        self.solver_calls += 1
        ignored = tuple(request.ignored_constraints)
        try:
            with _relaxed_prefix(request.prefix, ignored) as prefix:
                records = self._solve(prefix, request, ignored)
        except PackagesNotFoundError as exc:
            return _failure("unsatisfiable", exc)
        except SpecsConfigurationConflictError as exc:
            return _failure("unsatisfiable", exc, pin_conflict=_pin_conflict(exc))
        except UnsatisfiableError as exc:
            return _failure("unsatisfiable", exc)
        except Exception as exc:
            return _failure("other", exc)
        return SolveResult(ok=True, records=tuple(records))

    def _solve(
        self,
        prefix: Path,
        request: SolveRequest,
        ignored: tuple[IgnoredConstraint, ...],
    ) -> Sequence[PackageRecordType]:
        ignored_user = {
            _normalize(item.spec) for item in ignored if item.kind == "user"
        }
        specs_to_add = _filter_specs(request.specs_to_add, ignored_user)
        specs_to_remove = _filter_specs(request.specs_to_remove, ignored_user)

        backend = context.plugin_manager.get_solver_backend(BACKEND_NAME)
        kwargs: dict[str, Any] = {
            "prefix": str(prefix),
            "channels": [Channel(url) for url in request.channels or self.channels],
            "subdirs": tuple(request.subdirs or self.subdirs),
            "specs_to_add": tuple(specs_to_add),
            "specs_to_remove": tuple(specs_to_remove),
            "command": request.operation,
        }
        repodata_fn = request.repodata_fn or self.repodata_fn
        if repodata_fn:
            kwargs["repodata_fn"] = repodata_fn
        solver = backend(**kwargs)

        solve_kwargs: dict = {}
        if any(
            item.kind == "pinned" and item.source == "pinned_packages"
            for item in ignored
        ):
            solve_kwargs["ignore_pinned"] = True
        if request.update_modifier:
            modifier = request.update_modifier
            solve_kwargs["update_modifier"] = (
                UpdateModifier[modifier] if isinstance(modifier, str) else modifier
            )
        return solver.solve_final_state(**solve_kwargs)

    def candidates(
        self,
        spec: str,
        python: str | None = None,
        *,
        subdirs: tuple[str, ...] | None = None,
    ) -> list[CandidateInfo]:
        match = MatchSpec(spec)
        target_subdirs = tuple(subdirs or self.subdirs)
        index = self._index(target_subdirs)
        python_record = _python_record(python, self.channels, target_subdirs)
        candidates = []
        for record in index.values():
            if not match.match(record):
                continue
            if python_record is not None and not _python_compatible(
                record, python_record
            ):
                continue
            candidates.append(_candidate(record))
        return sorted(
            candidates,
            key=lambda candidate: (VersionOrder(candidate.version), candidate.build),
            reverse=True,
        )

    def _index(self, subdirs: tuple[str, ...]) -> Index:
        key = (self.channels, subdirs)
        index = self._indexes.get(key)
        if index is None:
            index = Index(
                channels=[Channel(url) for url in self.channels],
                subdirs=subdirs,
                prepend=False,
            )
            self._indexes[key] = index
        return index

    def conflict_hint(self, result: SolveResult) -> list[str]:
        if result.ok or not result.raw_error:
            return []
        hints: dict[str, str] = {}
        for line in result.raw_error.splitlines():
            parsed = _parse_line(line)
            if parsed is None:
                continue
            try:
                match = MatchSpec(parsed)
            except Exception:
                continue
            if match.name is None:
                continue
            hints.setdefault(match.name, str(match))
        return list(hints.values())


def _failure(
    error_kind: ErrorKind,
    exc: BaseException,
    *,
    pin_conflict: PinConflict | None = None,
) -> SolveResult:
    return SolveResult(
        ok=False,
        error_kind=error_kind,
        raw_error=str(exc),
        pinned_conflict=pin_conflict,
    )


def _pin_conflict(exc: SpecsConfigurationConflictError) -> PinConflict:
    kwargs = getattr(exc, "_kwargs", None) or {}
    requested = kwargs.get("requested_specs")
    pinned = kwargs.get("pinned_specs")
    if requested is None or pinned is None:
        requested_strs, pinned_strs = _parse_pin_message(str(exc))
    else:
        requested_strs = [str(spec) for spec in requested]
        pinned_strs = [str(spec) for spec in pinned]
    return PinConflict(
        requested_specs=tuple(requested_strs),
        pinned_specs=tuple(pinned_strs),
        prefix=str(kwargs.get("prefix") or ""),
    )


def _parse_pin_message(message: str) -> tuple[list[str], list[str]]:
    requested: list[str] = []
    pinned: list[str] = []
    current: list[str] | None = None
    for line in message.splitlines():
        stripped = line.strip()
        if stripped.startswith("requested specs:"):
            current = requested
        elif stripped.startswith("pinned specs:"):
            current = pinned
        elif stripped.startswith("- ") and current is not None:
            current.append(stripped[2:].strip())
        elif stripped.startswith("Use '"):
            current = None
    return requested, pinned


def _filter_specs(specs: Sequence[str], ignored: set[str]) -> list[str]:
    return [spec for spec in specs if _normalize(spec) not in ignored]


def _normalize(spec: str) -> str:
    try:
        return str(MatchSpec(spec))
    except Exception:
        return spec


@contextmanager
def _relaxed_prefix(
    prefix: Path | None, ignored: tuple[IgnoredConstraint, ...]
) -> Iterator[Path]:
    if prefix is None:
        with tempfile.TemporaryDirectory(prefix="conda-why-prefix-") as tmp:
            yield Path(tmp) / "env"
        return

    prefix = Path(prefix)
    if not _needs_overlay(prefix, ignored):
        yield prefix
        return

    with tempfile.TemporaryDirectory(prefix="conda-why-overlay-") as tmp:
        overlay = Path(tmp) / "env"
        meta = overlay / "conda-meta"
        meta.mkdir(parents=True)
        for item in (prefix / "conda-meta").iterdir():
            if item.name in {"history", "pinned"} or not item.is_file():
                continue
            shutil.copy2(item, meta / item.name)
        _write_overlay_history(prefix, meta / "history", ignored)
        _write_overlay_pinned(prefix, meta / "pinned", ignored)
        yield overlay


def _needs_overlay(prefix: Path, ignored: tuple[IgnoredConstraint, ...]) -> bool:
    if not (prefix / "conda-meta").is_dir():
        return False
    for item in ignored:
        if item.kind == "history":
            return True
        if item.kind == "pinned" and item.source != "pinned_packages":
            return True
    return False


def _write_overlay_history(
    prefix: Path, target: Path, ignored: tuple[IgnoredConstraint, ...]
) -> None:
    ignored_specs = {
        _normalize(item.spec) for item in ignored if item.kind == "history"
    }
    kept = [
        str(spec)
        for _, spec in sorted(History(prefix).get_requested_specs_map().items())
        if _normalize(str(spec)) not in ignored_specs
    ]
    lines = [
        f"==> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} <==",
        "# cmd: conda why",
    ]
    if kept:
        lines.append(f"# install specs: {kept!r}")
    target.write_text("".join(f"{line}\n" for line in lines))


def _write_overlay_pinned(
    prefix: Path, target: Path, ignored: tuple[IgnoredConstraint, ...]
) -> None:
    source = prefix / "conda-meta" / "pinned"
    if not source.is_file():
        return
    ignored_specs = {
        _normalize(item.spec)
        for item in ignored
        if item.kind == "pinned" and item.source != "pinned_packages"
    }
    kept = []
    for line in source.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _normalize(stripped) in ignored_specs:
            continue
        kept.append(stripped)
    if kept:
        target.write_text("".join(f"{spec}\n" for spec in kept))


def _python_record(
    python: str | None, channels: Sequence[str], subdirs: Sequence[str]
) -> PackageRecord | None:
    if not python:
        return None
    channel = Channel(channels[0]) if channels else Channel("https://conda-why.invalid")
    return PackageRecord(
        name="python",
        version=python,
        build="0",
        build_number=0,
        channel=channel,
        subdir=subdirs[0] if subdirs else "noarch",
    )


def _python_compatible(
    record: PackageRecord, python_record: PackageRecord
) -> bool:
    for dependency in record.depends:
        spec = MatchSpec(dependency)
        if spec.name == "python" and not spec.match(python_record):
            return False
    return True


def _candidate(record: PackageRecord) -> CandidateInfo:
    return CandidateInfo(
        name=record.name,
        version=record.version,
        build=record.build,
        subdir=record.subdir,
        channel=str(record.channel),
        depends=tuple(record.depends),
    )


def _parse_line(line: str) -> str | None:
    for char in _BOX_CHARS:
        line = line.replace(char, "")
    line = line.strip()
    if not line:
        return None
    if line.startswith("Cannot solve the request because of:"):
        line = line.split(":", 1)[1].strip()

    if "is locked, but another version is required" in line:
        words = line.split()
        return f"{words[0]} {words[1]}"
    if "cannot be installed because there are no viable options" in line:
        words = line.split()
        return f"{words[0]} {words[1].rstrip(',')}"
    if "the constraint" in line and "cannot be fulfilled" in line:
        words = line.split()
        return " ".join(words[2:-3])
    if (
        "can be installed with any of the following options" in line
        and "which" not in line
    ):
        return line[: line.index(" can be installed with")]
    if "No candidates were found for" in line:
        return line.split("No candidates were found for ", 1)[1].rstrip(".")
    if ", for which no candidates were found" in line:
        return line.split(", for which no candidates were found", 1)[0]
    return None
