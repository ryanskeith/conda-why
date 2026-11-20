from __future__ import annotations

from pathlib import Path

from .schema import ProvenanceRecord, WhyReport


def render_query(report: WhyReport, prefix: Path) -> str:
    count = len(report.matches)
    noun = "source" if count == 1 else "sources"
    subject = report.matches[0].subject if report.matches else "the package"
    lines = [f"{subject} is constrained by {count} {noun} in {prefix}.", ""]
    for finding in report.matches:
        lines.append(f"  {finding.claim}")
        lines.append(f"    {source_label(finding.provenance)}")
    return "\n".join(lines) + "\n"


def render_job(report: WhyReport, prefix: Path, *, explain: bool = False) -> str:
    if report.ok:
        lines = [f"The request can be solved in {prefix}.", ""]
        if report.implicit:
            lines.append("Implicit constraints that shaped the solve:")
            for record in report.implicit:
                lines.append(f"  {record.constraint.spec}  [{source_label(record)}]")
        return "\n".join(lines) + "\n"

    diagnosis = report.diagnosis
    assert diagnosis is not None
    lines = [diagnosis.headline, ""]
    for finding in diagnosis.findings:
        lines.append(f"  {finding.claim}  [{source_label(finding.provenance)}]")
    if diagnosis.next_steps:
        lines.extend(("", "Options:"))
        for step in diagnosis.next_steps:
            line = f"  {step.text}"
            if step.note:
                line += f"    {step.note}"
            lines.append(line)
    if explain and report.conflict is not None:
        conflict = report.conflict
        lines.extend(
            (
                "",
                "Evidence:",
                "  minimal conflict set: "
                + (", ".join(constraint.spec for constraint in conflict.constraints) or "none"),
                f"  solver: {conflict.solver}, {conflict.solver_calls} solves",
            )
        )
        if conflict.raw_error:
            lines.append("  raw error:")
            lines.extend(f"    {line}" for line in conflict.raw_error.splitlines())
    return "\n".join(lines) + "\n"


def source_label(record: ProvenanceRecord) -> str:
    parts = [record.constraint.kind, record.source]
    if record.revision is not None:
        parts.append(f"revision {record.revision}")
    if record.timestamp is not None:
        parts.append(record.timestamp.strftime("%Y-%m-%d %H:%M:%S"))
    return ", ".join(parts)
