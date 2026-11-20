from __future__ import annotations

from dataclasses import replace
from typing import Literal, Sequence, cast

from conda.models.match_spec import MatchSpec

from .adapters.base import IgnoredConstraint, SolveRequest, SolverAdapter
from .provenance import PRECEDENCE
from .schema import ConflictSet, ProvenanceRecord


def minimize(
    adapter: SolverAdapter,
    request: SolveRequest,
    constraints: Sequence[ProvenanceRecord],
    *,
    seed: Sequence[str] = (),
    prior_calls: int = 1,
    raw_error: str = "",
) -> ConflictSet:
    """Extract a minimal unsatisfiable subset of the relaxable constraints.

    ``prior_calls`` is the number of solves already spent finding the conflict,
    normally the initial failed solve. The returned ``solver_calls`` includes it.
    """
    relaxable = [record for record in constraints if record.constraint.relaxable]
    fixed = [record for record in constraints if not record.constraint.relaxable]
    seed_names = {name for spec in seed if (name := _spec_name(spec))}

    probes = 0
    active = list(relaxable)
    seeded = [
        record for record in relaxable if _spec_name(record.constraint.spec) in seed_names
    ]
    if seeded and len(seeded) < len(relaxable):
        probes += 1
        if not _probe(adapter, request, relaxable, seeded).ok:
            active = seeded

    kept, deletion_probes = _delete(adapter, request, relaxable, active)
    probes += deletion_probes

    names = seed_names | {_spec_name(record.constraint.spec) for record in kept}
    context = [record for record in fixed if _spec_name(record.constraint.spec) in names]
    ordered = sorted(
        [*kept, *context],
        key=lambda record: PRECEDENCE.get(record.constraint.kind, len(PRECEDENCE)),
    )
    return ConflictSet(
        constraints=[record.constraint for record in ordered],
        provenance=ordered,
        solver=adapter.name,
        solver_calls=prior_calls + probes,
        raw_error=raw_error,
    )


def _delete(
    adapter: SolverAdapter,
    request: SolveRequest,
    all_relaxable: Sequence[ProvenanceRecord],
    active: Sequence[ProvenanceRecord],
) -> tuple[list[ProvenanceRecord], int]:
    kept = list(active)
    probes = 0
    index = 0
    while index < len(kept):
        trial = kept[:index] + kept[index + 1 :]
        probes += 1
        if not _probe(adapter, request, all_relaxable, trial).ok:
            kept = trial
        else:
            index += 1
    return kept, probes


def _probe(
    adapter: SolverAdapter,
    request: SolveRequest,
    all_relaxable: Sequence[ProvenanceRecord],
    trial: Sequence[ProvenanceRecord],
):
    trial_ids = {id(record) for record in trial}
    ignored = tuple(
        IgnoredConstraint(
            kind=cast(
                Literal["user", "history", "pinned"], record.constraint.kind
            ),
            spec=record.constraint.spec,
            source=record.source,
        )
        for record in all_relaxable
        if id(record) not in trial_ids
    )
    return adapter.solve(replace(request, ignored_constraints=ignored))


def _spec_name(spec: str) -> str | None:
    try:
        return MatchSpec(spec).name
    except Exception:
        return None
