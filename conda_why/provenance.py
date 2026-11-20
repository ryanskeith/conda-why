from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from conda.base.context import context
from conda.core.prefix_data import PrefixData
from conda.history import History
from conda.models.match_spec import MatchSpec

from .schema import Constraint, ProvenanceRecord

PRECEDENCE = {
    "user": 0,
    "history": 1,
    "pinned": 2,
    "installed": 3,
    "special_case": 4,
    "transitive": 5,
}

_HISTORY_MODES = frozenset({"install", "update", "query"})
_PIN_MODES = frozenset({"install", "update", "query"})
_INSTALLED_MODES = frozenset({"install", "update", "query", "remove"})


def collect_constraints(
    mode: str,
    prefix: Path | None = None,
    user_specs: Sequence[str] = (),
) -> list[ProvenanceRecord]:
    """Collect the constraints a solve for ``mode`` would see, in precedence order."""
    prefix_path = Path(prefix) if prefix is not None else None
    installed = _installed_map(prefix_path)

    records: list[ProvenanceRecord] = []
    if mode != "query":
        records.extend(_user_records(user_specs))
    if mode in _HISTORY_MODES:
        records.extend(_history_records(prefix_path))
    if mode in _PIN_MODES:
        records.extend(_prefix_pin_records(prefix_path))
        records.extend(_config_pin_records())
    elif mode == "create":
        records.extend(_config_pin_records())
    if mode in _INSTALLED_MODES:
        records.extend(_installed_records(prefix_path, installed, mode, user_specs))
    if installed.get("python") is not None:
        records.extend(_special_case_records(installed, user_specs))
    return _dedupe(records)


def _user_records(user_specs: Sequence[str]) -> list[ProvenanceRecord]:
    records = []
    for spec in user_specs:
        if _is_valid_spec(spec):
            records.append(
                ProvenanceRecord(
                    constraint=Constraint.create(spec, "user"),
                    source="command line",
                    detail="requested in this invocation",
                )
            )
    return records


def _history_records(prefix: Path | None) -> list[ProvenanceRecord]:
    if prefix is None:
        return []
    path = prefix / "conda-meta" / "history"
    if not path.is_file():
        return []

    history = History(prefix)
    requested = history.get_requested_specs_map()
    positions: dict[str, tuple[int | None, datetime | None]] = {}
    for index, request in enumerate(history.get_user_requests(), 1):
        timestamp = _parse_timestamp(request.get("date"))
        for spec in request.get("remove_specs", ()):
            positions.pop(MatchSpec(spec).name, None)
        for spec in request.get("update_specs", ()):
            name = MatchSpec(spec).name
            if name in requested:
                positions[name] = (index, timestamp)

    records = []
    for name in sorted(requested):
        revision, timestamp = positions.get(name, (None, None))
        detail = (
            f"requested in revision {revision}" if revision is not None else "requested"
        )
        records.append(
            ProvenanceRecord(
                constraint=Constraint.create(str(requested[name]), "history"),
                source=str(path),
                revision=revision,
                timestamp=timestamp,
                detail=detail,
            )
        )
    return records


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.astimezone()


def _prefix_pin_records(prefix: Path | None) -> list[ProvenanceRecord]:
    if prefix is None:
        return []
    path = prefix / "conda-meta" / "pinned"
    if not path.is_file():
        return []

    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        spec = line.strip()
        if not spec or spec.startswith("#") or not _is_valid_spec(spec):
            continue
        records.append(
            ProvenanceRecord(
                constraint=Constraint.create(spec, "pinned"),
                source=str(path),
                detail="conda-meta/pinned",
            )
        )
    return records


def _config_pin_records() -> list[ProvenanceRecord]:
    records = []
    for spec in context.pinned_packages:
        if not _is_valid_spec(spec):
            continue
        records.append(
            ProvenanceRecord(
                constraint=Constraint.create(spec, "pinned"),
                source="pinned_packages",
                detail="condarc pinned_packages",
            )
        )
    return records


@dataclass(frozen=True)
class InstalledInfo:
    version: str
    depends: tuple[str, ...]


def installed_dependencies(prefix: Path | None) -> dict[str, InstalledInfo]:
    return {
        name: InstalledInfo(version=record.version, depends=tuple(record.depends))
        for name, record in _installed_map(prefix).items()
    }


def _installed_map(prefix: Path | None):
    if prefix is None or not (prefix / "conda-meta").is_dir():
        return {}
    return {record.name: record for record in PrefixData(prefix).iter_records()}


def _installed_records(
    prefix: Path | None,
    installed: dict,
    mode: str,
    user_specs: Sequence[str],
) -> list[ProvenanceRecord]:
    if prefix is None:
        return []
    removed_names = set()
    if mode == "remove":
        removed_names = {MatchSpec(spec).name for spec in user_specs}

    records = []
    for name in sorted(installed):
        record = installed[name]
        if mode == "remove" and not any(
            MatchSpec(dependency).name in removed_names for dependency in record.depends
        ):
            continue
        records.append(_installed_record(prefix, record))
    return records


def _installed_record(prefix: Path, record) -> ProvenanceRecord:
    spec = f"{record.name}={record.version}={record.build}"
    filename = f"{record.name}-{record.version}-{record.build}.json"
    return ProvenanceRecord(
        constraint=Constraint.create(spec, "installed"),
        source=str(prefix / "conda-meta" / filename),
        detail=f"installed package {record.name} {record.version} ({record.build})",
    )


def _special_case_records(
    installed: dict, user_specs: Sequence[str]
) -> list[ProvenanceRecord]:
    if any(_spec_name(spec) == "python" for spec in user_specs):
        return []
    python = installed["python"]
    minor = ".".join(python.version.split(".")[:2])
    return [
        ProvenanceRecord(
            constraint=Constraint.create(f"python {minor}.*", "special_case"),
            source="python-minor-pin",
            detail=(
                f"python {python.version} is installed and no python spec was requested"
            ),
        )
    ]


def _dedupe(records: list[ProvenanceRecord]) -> list[ProvenanceRecord]:
    merged: dict[tuple[str, bool], ProvenanceRecord] = {}
    for record in records:
        key = (_normalize(record.constraint.spec), record.constraint.relaxable)
        if key not in merged:
            merged[key] = record
            continue
        kept = merged[key]
        note = f"also {record.constraint.kind} in {record.source}"
        detail = f"{kept.detail}; {note}" if kept.detail else note
        merged[key] = replace(kept, detail=detail)
    return list(merged.values())


def _normalize(spec: str) -> str:
    try:
        return str(MatchSpec(spec))
    except Exception:
        return spec


def _spec_name(spec: str) -> str | None:
    try:
        return MatchSpec(spec).name
    except Exception:
        return None


def _is_valid_spec(spec: str) -> bool:
    if not spec or not spec.strip():
        return False
    try:
        return MatchSpec(spec).name is not None
    except Exception:
        return False
