from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, cast

SCHEMA_VERSION = "0.1"

ConstraintKind = Literal[
    "user",
    "history",
    "pinned",
    "installed",
    "special_case",
    "transitive",
]

RELAXABLE: dict[str, bool] = {
    "user": True,
    "history": True,
    "pinned": True,
    "installed": False,
    "special_case": False,
    "transitive": False,
}

DiagnosisKind = Literal[
    "unsupported_python",
    "unsupported_platform",
    "incompatible_specs",
    "pinned_constraint",
    "installed_constraint",
    "channel_priority",
    "virtual_package",
    "build_variant",
    "unknown",
]


@dataclass(frozen=True)
class Constraint:
    spec: str
    kind: ConstraintKind
    relaxable: bool

    @classmethod
    def create(cls, spec: str, kind: str) -> Constraint:
        if kind not in RELAXABLE:
            raise ValueError(f"unknown constraint kind: {kind!r}")
        return cls(spec=spec, kind=cast(ConstraintKind, kind), relaxable=RELAXABLE[kind])

    def to_dict(self) -> dict[str, Any]:
        return {"spec": self.spec, "kind": self.kind, "relaxable": self.relaxable}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Constraint:
        return cls(
            spec=data["spec"],
            kind=data["kind"],
            relaxable=bool(data["relaxable"]),
        )


@dataclass(frozen=True)
class ProvenanceRecord:
    constraint: Constraint
    source: str
    revision: int | None = None
    timestamp: datetime | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint": self.constraint.to_dict(),
            "source": self.source,
            "revision": self.revision,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProvenanceRecord:
        timestamp = data["timestamp"]
        return cls(
            constraint=Constraint.from_dict(data["constraint"]),
            source=data["source"],
            revision=data["revision"],
            timestamp=datetime.fromisoformat(timestamp) if timestamp else None,
            detail=data.get("detail", ""),
        )


@dataclass(frozen=True)
class ConflictSet:
    constraints: list[Constraint]
    provenance: list[ProvenanceRecord]
    solver: str
    solver_calls: int
    raw_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraints": [constraint.to_dict() for constraint in self.constraints],
            "provenance": [record.to_dict() for record in self.provenance],
            "solver": self.solver,
            "solver_calls": self.solver_calls,
            "raw_error": self.raw_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConflictSet:
        return cls(
            constraints=[
                Constraint.from_dict(constraint) for constraint in data["constraints"]
            ],
            provenance=[
                ProvenanceRecord.from_dict(record) for record in data["provenance"]
            ],
            solver=data["solver"],
            solver_calls=data["solver_calls"],
            raw_error=data.get("raw_error", ""),
        )


@dataclass(frozen=True)
class Finding:
    subject: str
    claim: str
    provenance: ProvenanceRecord

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "claim": self.claim,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        return cls(
            subject=data["subject"],
            claim=data["claim"],
            provenance=ProvenanceRecord.from_dict(data["provenance"]),
        )


@dataclass(frozen=True)
class NextStep:
    text: str
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "note": self.note}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NextStep:
        return cls(text=data["text"], note=data.get("note"))


@dataclass(frozen=True)
class Diagnosis:
    kind: DiagnosisKind
    headline: str
    findings: list[Finding] = field(default_factory=list)
    next_steps: list[NextStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "headline": self.headline,
            "findings": [finding.to_dict() for finding in self.findings],
            "next_steps": [step.to_dict() for step in self.next_steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Diagnosis:
        return cls(
            kind=data["kind"],
            headline=data["headline"],
            findings=[Finding.from_dict(finding) for finding in data["findings"]],
            next_steps=[NextStep.from_dict(step) for step in data["next_steps"]],
        )


@dataclass(frozen=True)
class WhyReport:
    mode: Literal["query", "job"]
    ok: bool
    diagnosis: Diagnosis | None = None
    conflict: ConflictSet | None = None
    implicit: list[ProvenanceRecord] = field(default_factory=list)
    matches: list[Finding] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "ok": self.ok,
            "diagnosis": self.diagnosis.to_dict() if self.diagnosis else None,
            "conflict": self.conflict.to_dict() if self.conflict else None,
            "implicit": [record.to_dict() for record in self.implicit],
            "matches": [finding.to_dict() for finding in self.matches],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WhyReport:
        if "schema_version" not in data:
            raise ValueError("report is missing schema_version")
        diagnosis = data.get("diagnosis")
        conflict = data.get("conflict")
        return cls(
            mode=data["mode"],
            ok=bool(data["ok"]),
            diagnosis=Diagnosis.from_dict(diagnosis) if diagnosis else None,
            conflict=ConflictSet.from_dict(conflict) if conflict else None,
            implicit=[
                ProvenanceRecord.from_dict(record) for record in data.get("implicit", [])
            ],
            matches=[Finding.from_dict(finding) for finding in data.get("matches", [])],
            schema_version=data["schema_version"],
        )
