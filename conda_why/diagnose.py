from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from conda.models.channel import Channel
from conda.models.match_spec import MatchSpec
from conda.models.records import PackageRecord
from conda.models.version import VersionOrder

from .adapters.base import CandidateInfo, SolveResult, SolverAdapter
from .provenance import InstalledInfo
from .schema import (
    ConflictSet,
    Constraint,
    Diagnosis,
    Finding,
    NextStep,
    ProvenanceRecord,
)

MAX_FINDINGS = 3
MAX_NEXT_STEPS = 2
_OTHER_SUBDIRS = ("linux-64", "osx-64", "osx-arm64", "win-64")


@dataclass(frozen=True)
class DiagnosisRequest:
    operation: str
    specs: tuple[str, ...]
    prefix: Path | None
    flags: str
    constraints: Sequence[ProvenanceRecord]
    installed: dict[str, InstalledInfo]
    adapter: SolverAdapter
    result: SolveResult
    conflict: ConflictSet


def diagnose(request: DiagnosisRequest) -> Diagnosis:
    if request.result.error_kind == "not_found":
        missing = _missing(request)
        if missing is not None:
            return missing
    if request.result.pinned_conflict is not None:
        return _pinned_conflict(request)
    for rule in (
        _unsupported_python,
        _incompatible_specs,
        _pinned_constraint,
        _installed_constraint,
        _build_variant,
    ):
        diagnosis = rule(request)
        if diagnosis is not None:
            return diagnosis
    return _unknown(request)


def _unsupported_python(request: DiagnosisRequest) -> Diagnosis | None:
    python = _python_in_play(request)
    if python is None:
        return None
    version, python_record = python

    blocked: tuple[ProvenanceRecord, CandidateInfo, str] | None = None
    for record in request.conflict.provenance:
        if record.constraint.kind not in {"user", "history"}:
            continue
        name = _spec_name(record.constraint.spec)
        if name is None or name == "python":
            continue
        candidates = request.adapter.candidates(record.constraint.spec)
        if not candidates:
            continue
        if request.adapter.candidates(record.constraint.spec, python=version):
            continue
        dependency = _lowest_python_requirement(candidates)
        if dependency is None:
            continue
        blocked = (record, candidates[0], dependency)
        break
    if blocked is None:
        return None

    record, candidate, dependency = blocked
    name = _spec_name(record.constraint.spec) or candidate.name
    display = _display(record.constraint.spec, candidate.version)
    floor = _lower_bound(dependency) or str(MatchSpec(dependency).version or "")
    headline = (
        f"{display} is not compatible with Python {version} in this environment."
    )
    findings = [
        Finding(
            subject=name,
            claim=f"{display} requires Python {floor} or newer",
            provenance=_metadata_provenance(candidate, dependency),
        ),
        Finding(
            subject="python",
            claim=_python_claim(version, python_record),
            provenance=python_record,
        ),
    ]
    steps = []
    compatible = request.adapter.candidates(name, python=version)
    if compatible:
        upper = _version(record.constraint.spec) or candidate.version
        steps.append(
            NextStep(
                text=(
                    f'conda {_install_verb(request.operation)} '
                    f'"{name}<{upper}" {request.flags}'
                ).strip(),
                note=f"keep Python {version}",
            )
        )
    steps.append(
        NextStep(
            text=(
                f'conda {_install_verb(request.operation)} '
                f'"python>={floor}" "{record.constraint.spec}" '
                f"{request.flags}"
            ).strip(),
            note="move Python; other packages may need updates",
        )
    )
    return Diagnosis(
        kind="unsupported_python",
        headline=headline,
        findings=findings[:MAX_FINDINGS],
        next_steps=steps[:MAX_NEXT_STEPS],
    )


def _incompatible_specs(request: DiagnosisRequest) -> Diagnosis | None:
    users = [
        record
        for record in request.conflict.provenance
        if record.constraint.kind == "user"
    ]
    if len(users) < 2:
        return None

    for index, first in enumerate(users):
        first_candidate = _newest(request.adapter.candidates(first.constraint.spec))
        if first_candidate is None:
            continue
        for second in users[index + 1 :]:
            second_candidate = _newest(
                request.adapter.candidates(second.constraint.spec)
            )
            if second_candidate is None:
                continue
            for first_dep in first_candidate.depends:
                name = MatchSpec(first_dep).name
                if name is None:
                    continue
                second_dep = next(
                    (
                        dependency
                        for dependency in second_candidate.depends
                        if MatchSpec(dependency).name == name
                    ),
                    None,
                )
                if second_dep is None:
                    continue
                if _jointly_satisfiable(name, first_dep, second_dep, request):
                    continue
                return _incompatible_diagnosis(
                    request, first, second, first_candidate, second_candidate,
                    name, first_dep, second_dep,
                )
    return None


def _incompatible_diagnosis(
    request: DiagnosisRequest,
    first: ProvenanceRecord,
    second: ProvenanceRecord,
    first_candidate: CandidateInfo,
    second_candidate: CandidateInfo,
    name: str,
    first_dep: str,
    second_dep: str,
) -> Diagnosis:
    first_name = _spec_name(first.constraint.spec) or first_candidate.name
    second_name = _spec_name(second.constraint.spec) or second_candidate.name
    first_display = _display(first.constraint.spec, first_candidate.version)
    second_display = _display(second.constraint.spec, second_candidate.version)
    headline = (
        f"{first_display} and {second_display} "
        f"need incompatible versions of {name}."
    )
    findings = [
        Finding(
            subject=first_name,
            claim=f"{first_display} requires {first_dep}",
            provenance=first,
        ),
        Finding(
            subject=second_name,
            claim=f"{second_display} requires {second_dep}",
            provenance=second,
        ),
    ]
    verb = _install_verb(request.operation)
    steps = [
        NextStep(
            text=(
                f'conda {verb} "{first_name}<{first_candidate.version}" '
                f"{second_name} {request.flags}"
            ).strip(),
            note=f"keep {second_name}",
        ),
        NextStep(
            text=(
                f'conda {verb} {first_name} '
                f'"{second_name}<{second_candidate.version}" {request.flags}'
            ).strip(),
            note=f"keep {first_name}",
        ),
    ]
    return Diagnosis(
        kind="incompatible_specs",
        headline=headline,
        findings=findings[:MAX_FINDINGS],
        next_steps=steps[:MAX_NEXT_STEPS],
    )


def _pinned_constraint(request: DiagnosisRequest) -> Diagnosis | None:
    pins = [
        record
        for record in request.conflict.provenance
        if record.constraint.kind == "pinned"
    ]
    if not pins:
        return None
    return _pin_diagnosis(pins[0])


def _pinned_conflict(request: DiagnosisRequest) -> Diagnosis:
    conflict = request.result.pinned_conflict
    assert conflict is not None
    name = _spec_name(conflict.pinned_specs[0]) if conflict.pinned_specs else None
    record = next(
        (
            candidate
            for candidate in request.constraints
            if candidate.constraint.kind == "pinned"
            and _spec_name(candidate.constraint.spec) == name
        ),
        None,
    )
    if record is None:
        spec = conflict.pinned_specs[0] if conflict.pinned_specs else "pinned"
        record = ProvenanceRecord(
            constraint=Constraint.create(spec, "pinned"),
            source=str(Path(conflict.prefix) / "conda-meta" / "pinned"),
            detail="condarc or conda-meta/pinned",
        )
    return _pin_diagnosis(record)


def _pin_diagnosis(record: ProvenanceRecord) -> Diagnosis:
    name = _spec_name(record.constraint.spec) or record.constraint.spec
    headline = f"{name} is held by a conda pin in {record.source}."
    findings = [
        Finding(
            subject=name,
            claim=f"{record.constraint.spec} is pinned in {record.source}",
            provenance=record,
        )
    ]
    steps = [
        NextStep(
            text=(
                f"Remove the pin on {name} from {record.source}, "
                "or relax it to include the requested version."
            )
        )
    ]
    return Diagnosis(
        kind="pinned_constraint",
        headline=headline,
        findings=findings[:MAX_FINDINGS],
        next_steps=steps,
    )


def _installed_constraint(request: DiagnosisRequest) -> Diagnosis | None:
    conflict_names = {
        _spec_name(record.constraint.spec)
        for record in request.conflict.provenance
    }
    for user in request.conflict.provenance:
        if user.constraint.kind != "user":
            continue
        target_name = _spec_name(user.constraint.spec)
        target_version = _version(user.constraint.spec)
        if target_name is None or target_version is None:
            continue
        for installed_name in sorted(request.installed):
            if installed_name == target_name:
                continue
            if installed_name not in conflict_names:
                continue
            info = request.installed[installed_name]
            for dependency in info.depends:
                if MatchSpec(dependency).name != target_name:
                    continue
                if MatchSpec(dependency).match(
                    _record(target_name, target_version)
                ):
                    continue
                installed_record = _find_record(
                    request, installed_name, "installed"
                ) or ProvenanceRecord(
                    constraint=Constraint.create(
                        f"{installed_name}={info.version}", "installed"
                    ),
                    source="installed record",
                    detail=f"{installed_name} {info.version}",
                )
                headline = (
                    f"{installed_name} {info.version} currently requires "
                    f"{dependency}."
                )
                findings = [
                    Finding(
                        subject=installed_name,
                        claim=(
                            f"{installed_name} {info.version} requires {dependency}"
                        ),
                        provenance=installed_record,
                    ),
                    Finding(
                        subject=target_name,
                        claim=f"{user.constraint.spec} was requested",
                        provenance=user,
                    ),
                ]
                steps = [
                    NextStep(
                        text=(
                            f"Update or remove {installed_name}, or relax its "
                            f"requirement on {target_name}."
                        )
                    )
                ]
                return Diagnosis(
                    kind="installed_constraint",
                    headline=headline,
                    findings=findings[:MAX_FINDINGS],
                    next_steps=steps,
                )
    return None


def _build_variant(request: DiagnosisRequest) -> Diagnosis | None:
    grouped: dict[str, list[ProvenanceRecord]] = {}
    for record in request.conflict.provenance:
        name = _spec_name(record.constraint.spec)
        if name is not None:
            grouped.setdefault(name, []).append(record)
    for name, records in grouped.items():
        builds = {
            str(MatchSpec(record.constraint.spec).get_exact_value("build") or "")
            for record in records
        }
        builds.discard("")
        if len(builds) < 2:
            continue
        headline = (
            f"{records[0].constraint.spec} and {records[1].constraint.spec} "
            f"need different builds of {name}."
        )
        findings = [
            Finding(
                subject=name,
                claim=(
                    f"{record.constraint.spec} requires build "
                    f"{MatchSpec(record.constraint.spec).get_exact_value('build')}"
                ),
                provenance=record,
            )
            for record in records[:MAX_FINDINGS]
        ]
        return Diagnosis(
            kind="build_variant",
            headline=headline,
            findings=findings,
            next_steps=[],
        )
    return None


def _missing(request: DiagnosisRequest) -> Diagnosis | None:
    missing = []
    for spec in request.specs:
        if request.adapter.candidates(spec):
            continue
        other_subdirs = [
            subdir
            for subdir in _alternate_subdirs(request.adapter.subdirs)
            if request.adapter.candidates(spec, subdirs=(subdir,))
        ][:1]
        missing.append((spec, other_subdirs))
    if not missing:
        return None

    spec, other_subdirs = missing[0]
    name = _spec_name(spec) or spec
    record = _find_record(request, name, "user") or ProvenanceRecord(
        constraint=Constraint.create(spec, "user"),
        source="command line",
    )
    if other_subdirs:
        target = request.adapter.subdirs[0] if request.adapter.subdirs else "this platform"
        headline = f"{name} has no build for {target}."
        findings = [
            Finding(
                subject=name,
                claim=f"{spec} is only available for {', '.join(other_subdirs)}",
                provenance=record,
            )
        ]
        steps = [
            NextStep(
                text=(
                    f"Use a platform that has a build of {name}, "
                    "or choose another package."
                )
            )
        ]
        return Diagnosis(
            kind="unsupported_platform",
            headline=headline,
            findings=findings,
            next_steps=steps,
        )

    headline = f"{name} was not found in your configured channels."
    findings = [
        Finding(
            subject=name,
            claim=f"{spec} was not found in any enabled channel or subdir",
            provenance=record,
        )
    ]
    steps = [
        NextStep(text=f"Check the spelling of {name}."),
        NextStep(text=f"Add the channel that provides {name} with -c CHANNEL."),
    ]
    return Diagnosis(
        kind="package_not_found",
        headline=headline,
        findings=findings,
        next_steps=steps,
    )


def _alternate_subdirs(subdirs: Sequence[str]) -> list[str]:
    family = {
        "osx-arm64": "osx-64",
        "osx-64": "osx-arm64",
        "win-64": "win-32",
        "linux-64": "linux-aarch64",
        "linux-aarch64": "linux-64",
    }
    ordered: list[str] = []
    for subdir in subdirs[:1]:
        sibling = family.get(subdir)
        if sibling:
            ordered.append(sibling)
    for subdir in _OTHER_SUBDIRS:
        if subdir not in subdirs and subdir not in ordered:
            ordered.append(subdir)
    return ordered


def _unknown(request: DiagnosisRequest) -> Diagnosis:
    findings = [
        Finding(
            subject=_spec_name(record.constraint.spec) or record.constraint.spec,
            claim=_constraint_claim(record),
            provenance=record,
        )
        for record in request.conflict.provenance[:MAX_FINDINGS]
    ]
    return Diagnosis(
        kind="unknown",
        headline="conda-why could not identify a single cause.",
        findings=findings,
        next_steps=[],
    )


def _python_in_play(
    request: DiagnosisRequest,
) -> tuple[str, ProvenanceRecord] | None:
    user = _python_record(request, "user")
    if user is not None:
        return user

    history = _python_record(request, "history")
    installed = request.installed.get("python")
    if history is not None:
        version, _ = history
        if (
            installed is None
            or installed.version == version
            or installed.version.startswith(f"{version}.")
        ):
            return history

    for kind in ("special_case", "installed"):
        record = _python_record(request, kind)
        if record is not None:
            return record
    return None


def _python_record(
    request: DiagnosisRequest, kind: str
) -> tuple[str, ProvenanceRecord] | None:
    for source in (request.conflict.provenance, request.constraints):
        for record in source:
            if record.constraint.kind == kind and _spec_name(
                record.constraint.spec
            ) == "python":
                version = _version(record.constraint.spec)
                if version:
                    return version, record
    return None


def _python_claim(version: str, record: ProvenanceRecord) -> str:
    if record.constraint.kind == "user":
        return f"Python {version} was requested in this command"
    if record.constraint.kind == "history":
        if record.revision is not None:
            return (
                f"Python {version} was requested in revision {record.revision} "
                "when this environment was created"
            )
        return f"Python {version} was requested when this environment was created"
    if record.constraint.kind == "special_case":
        return (
            f"Python {version} is installed and no python update was requested"
        )
    return f"Python {version} is installed"


def _constraint_claim(record: ProvenanceRecord) -> str:
    kind = record.constraint.kind
    if kind == "user":
        return f"{record.constraint.spec} was requested"
    if kind == "history":
        if record.revision is not None:
            return f"revision {record.revision} requested {record.constraint.spec}"
        return f"history requested {record.constraint.spec}"
    if kind == "pinned":
        return f"{record.constraint.spec} is pinned in {record.source}"
    if kind == "installed":
        return f"{record.constraint.spec} is installed"
    if kind == "special_case":
        return f"{record.constraint.spec} is held by {record.source}"
    return f"{record.constraint.spec} is required"


def _metadata_provenance(
    candidate: CandidateInfo, dependency: str
) -> ProvenanceRecord:
    return ProvenanceRecord(
        constraint=Constraint.create(dependency, "transitive"),
        source="package metadata",
        detail=(
            f"{candidate.name} {candidate.version} depends on {dependency}"
        ),
    )


def _lowest_python_requirement(
    candidates: Sequence[CandidateInfo],
) -> str | None:
    best: str | None = None
    best_version: VersionOrder | None = None
    for candidate in candidates:
        dependency = _python_dependency(candidate)
        if dependency is None:
            continue
        bound = _lower_bound(dependency)
        if bound is None:
            continue
        version = VersionOrder(bound)
        if best_version is None or version < best_version:
            best, best_version = dependency, version
    return best


def _lower_bound(dependency: str) -> str | None:
    version = str(MatchSpec(dependency).version or "")
    match = re.search(r">=\s*([0-9][^,<|]*)", version)
    if match:
        return match.group(1).strip()
    match = re.match(r"([0-9][^,<|]*)", version)
    if match:
        return match.group(1).strip().rstrip("*").rstrip(".")
    return None


def _python_dependency(candidate: CandidateInfo) -> str | None:
    return next(
        (
            dependency
            for dependency in candidate.depends
            if MatchSpec(dependency).name == "python"
        ),
        None,
    )


def _jointly_satisfiable(
    name: str, first: str, second: str, request: DiagnosisRequest
) -> bool:
    versions = {
        candidate.version for candidate in request.adapter.candidates(name)
    }
    if not versions:
        return True
    for version in versions:
        record = _record(name, version)
        if MatchSpec(first).match(record) and MatchSpec(second).match(record):
            return True
    return False


def _record(name: str, version: str) -> PackageRecord:
    return PackageRecord(
        name=name,
        version=version,
        build="0",
        build_number=0,
        channel=Channel("https://conda-why.invalid"),
        subdir="noarch",
    )


def _newest(candidates: Sequence[CandidateInfo]) -> CandidateInfo | None:
    return candidates[0] if candidates else None


def _find_record(
    request: DiagnosisRequest, name: str, kind: str
) -> ProvenanceRecord | None:
    for record in request.constraints:
        if record.constraint.kind == kind and _spec_name(
            record.constraint.spec
        ) == name:
            return record
    return None


def _spec_name(spec: str) -> str | None:
    try:
        return MatchSpec(spec).name
    except Exception:
        return None


def _version(spec: str) -> str | None:
    try:
        version = MatchSpec(spec).version
    except Exception:
        return None
    if not version:
        return None
    text = str(version)
    if text.endswith(".*"):
        text = text[:-2]
    if not text or any(char in text for char in "<>=!~,|*"):
        return None
    return text


def _display(spec: str, fallback: str | None = None) -> str:
    name = _spec_name(spec) or spec
    version = _version(spec) or fallback or ""
    return f"{name} {version}".strip()


def _install_verb(operation: str) -> str:
    return "create" if operation == "create" else "install"
