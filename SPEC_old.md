# SPEC: conda-why (MVP)

## Summary
`conda why` is a conda plugin that explains solver failures (`UnsatisfiableError`) by
identifying why the solve fails and citing the provenance of each constraint involved
(user request, environment history, pins, solver special-cases). The goal is to help
users get themselves unstuck, ideally by narrowing the failure to a minimal conflicting
set of constraints. It also answers "why is X constrained here?" for healthy environments.

Repo: `ryanskeith/conda-why` (may graduate to `conda-incubator`)
Scaffold: `conda/conda-plugin-template` (cookiecutter)

## Goals
- Explain *why* a create/install/update/remove fails, in terms a user can act on
- Surface implicit constraints (history, pinned_packages, special-cases) users can't see today
- Define a solver-agnostic conflict/provenance schema (prototype for a future standardized `UnsatisfiableError` payload)
- Explore saving the last failed solve so conda-why can re-explain it without retyping

## Stretch goal
- `--last`: re-explain the most recent failed solve. Depends on Spike B. Capturing failures from normal conda commands may not be feasible from a plugin.
  - Fallback UX: up-arrow to the failed command and insert `why` after `conda`.

## Non-goals (MVP)
- Multiple solver adapters (interface + one implementation). Target conda-rattler-solver.
- `--compare-solvers` (stubbed)
- Performance budgets on the MUS loop
- Supporting conda versions older than the latest release (26.10.0)
- Upstreaming into conda proper

## CLI

    conda why <spec>                              # provenance query, no solve
    conda why create  <specs...> [-n|-p ...]      # dry-run solve + explain
    conda why install <specs...> [-n|-p ...]
    conda why update  <specs...> [-n|-p ...]
    conda why remove  <specs...> [-n|-p ...]
    conda why --last                              # STRETCH: re-explain last failed solve

Flags (all modes): `--json`, `-c/--channel`, `--override-channels`
Stubbed: `--compare-solvers` → exits non-zero with "not implemented: only one adapter available"
Alias: `conda wtf` (easter egg). Registered as a second `CondaSubcommand` with the same
action and parser, and hidden from the summary if conda allows it.

Behavior:
- Never mutates any environment. All solves are dry-run.
- Job mode on success: report the solved state and the implicit constraints that shaped it.
- Job mode on failure: report the minimal conflict set with per-constraint provenance.
- Use `CondaSubcommand(configure_parser=...)` so argparse is integrated with conda's.

## Architecture

    conda_why/
      plugin.py          # hookimpl: conda_subcommands (why + wtf), + failure hook if Spike B finds one
      cli.py             # parser config + dispatch
      schema.py          # dataclasses: Constraint, ProvenanceRecord, ConflictSet, SolveReport, FailedSolve
      provenance.py      # resolves where each constraint came from
      adapters/
        base.py          # SolverAdapter protocol
        crs.py           # wraps conda-rattler-solver
      mus.py             # deletion-based MUS extraction (optionally seeded)
      last.py            # STRETCH: last failed-solve record read/write
      render.py          # text output (decoupled from schema)

Rule: nothing outside `adapters/` imports solver internals.

### schema.py (the artifact that matters most)
- `Constraint`: `spec: str` (MatchSpec string), `kind: Literal["user", "history", "pinned", "special_case", "transitive"]`, `relaxable: bool`
- `ProvenanceRecord`: `constraint`, `source` (file path / config key / rule id), `revision: int | None`, `timestamp: datetime | None`, `detail: str`
- `ConflictSet`: `constraints: list[Constraint]`, `provenance: list[ProvenanceRecord]` (one per constraint), `solver: str`, `solver_calls: int`
- `SolveReport`: `ok: bool`, `conflict: ConflictSet | None`, `implicit: list[ProvenanceRecord]`, `schema_version: str`
- `FailedSolve`: see `last.py` below
- `--json` output is a direct serialization of `SolveReport`. Include `schema_version` from day one.

### adapters/base.py

    class SolverAdapter(Protocol):
        name: str
        def solve(self, prefix, channels, subdirs, constraints: list[Constraint]) -> SolveResult: ...
        def conflict_hint(self, result: SolveResult) -> list[str]: ...   # specs the solver itself blamed

`SolveResult` is either success (records) or failure (raw error retained for debugging).
The adapter is responsible for honoring the exclusion of `relaxable` constraints (see Spike A).
`conflict_hint` parses the solver's native conflict explanation into specs. It returns
an empty list if parsing isn't possible.

### provenance.py
Sources, in order:
1. User specs from the current invocation → `user`
2. `conda-meta/history` requested specs (revision + timestamp) → `history`
3. `pinned_packages` (condarc) and `conda-meta/pinned` → `pinned`
4. Hardcoded rules table for solver special-cases (e.g. python minor pinning on update) → `special_case`, `relaxable=False`
5. Subdir/channel mismatch detection → reported as provenance notes, not constraints

### mus.py
Deletion-based. For each relaxable constraint, re-solve without it. If the solve is still
UNSAT, drop the constraint permanently; otherwise keep it. The result is a minimal UNSAT
subset over `user | history | pinned` constraints. `special_case` constraints are never
dropped, but are always reported if implicated. No call or time limit for MVP; record
`solver_calls`.

Seeding: if `conflict_hint` (or a `FailedSolve.seed`) is available, try the seeded subset
first. If the seeded subset is UNSAT on its own, run deletion only within the seed.
Otherwise, fall back to the full constraint set. Treat the seed as a hint, never as
the result.

### last.py (STRETCH)
- Location: user-level conda data dir, e.g. `~/.conda/why/last_failed_solve.json`. The
  record is global, not per-env, because a failed `create` has no `conda-meta/`.
- **Single record, overwritten on each failure.** No history accumulates.
- Write atomically (temp file + `os.replace`) with file mode `0600`.
- `FailedSolve` contents:
  - Reproduce: `argv`, `command`, `specs`, `prefix`, `channels`, `subdirs`, `solver` (name + version), `conda_version`, `timestamp`
  - Head start: `seed` (specs from the solver's conflict output), `constraints` (resolved history/pinned/special-case constraints with `kind`, snapshotted at failure time)
  - Staleness check: `repodata_fingerprint` (per channel: URL + cached repodata mtime/etag)
  - Debug: `raw_error` (truncated)
- Never store: full repodata, or channel tokens. Redact `/t/<token>/` in URLs and `argv`.
- `--last` loads the record, warns if the repodata fingerprint or env constraints changed
  since the failure, then runs job mode seeded with `seed`.

## Spikes (resolve before implementing the dependent phase)
**A — Relaxing implicit constraints through the solver API.**
History and pinned specs are injected inside the solver, not passed in by the caller.
Investigate: `context.ignore_pinned` (`--no-pin`), `UpdateModifier` options, and
whether `crs.py` must construct the solve so history specs are passed explicitly.
Also check what conda-rattler-solver exposes for its native conflict explanation (feeds
`conflict_hint`). Output: `docs/spike-a.md` with a recommended approach.

**B — Capturing failures from normal `conda install/create/...`. (STRETCH gate)**
Check which plugin hooks (pre/post-command, exception handling) fire on
`UnsatisfiableError` and what context they can see. If none fire, drop `--last` from
the MVP and document the needed upstream hook. Output: `docs/spike-b.md`.

## Testing
- Synthetic local channel generated by `tests/fixtures/synth_channel.py` (repodata.json, no network). Required scenarios:
  1. Two user specs directly conflict
  2. User spec conflicts with a history-requested spec
  3. User spec conflicts with `pinned_packages`
  4. Transitive conflict (A→C<2, B→C>=2)
  5. Healthy solve with implicit history pin reported
- MUS seeding: a correct seed reduces `solver_calls`; a wrong or partial seed still yields the correct minimal set
- Real-channel tests (`tests/test_real_channels.py`): written, `@pytest.mark.skip(reason="network/flaky; enable manually")`
- `--json` output validated by round-tripping through `schema.py`
- Plugin smoke tests: `conda why --help` and `conda wtf --help` exit 0
- STRETCH: `last.py` overwrite semantics, atomic write, `0600` mode, token redaction, staleness warning

## Agent workflow (tests-first, gated)
For each phase:
1. Write tests for the phase. **Stop. Request approval.**
2. After approval, implement until tests pass.
3. Summarize changes and any spec deviations. **Stop. Request approval.**

Phases:
0. Scaffold from cookiecutter; register `why` + `wtf`; CLI skeleton; `--compare-solvers` stub
1. Spike A (findings may reshape `schema.py` or the adapter protocol, so it comes before schema is frozen)
2. `schema.py` + `provenance.py` + bare query mode
3. `adapters/` (base + `crs.py`, including `conflict_hint`)
4. `mus.py` (with seeding) + job modes
5. `render.py` text output polish
6. STRETCH: Spike B → `last.py` + `--last` (only if Spike B finds a viable hook)

Do not start phase N+1 without approval. Do not change `schema.py` public fields after
Phase 2 without flagging it.

## Conventions
- Python version + tooling: follow the cookiecutter template defaults
- `conda>=26.10.0` pinned in pyproject
- No new runtime dependencies without asking
- Type hints throughout; frozen dataclasses for schema
- Read-only: any code path that could mutate a prefix is a bug

## MVP acceptance
- All 5 synthetic scenarios produce correct minimal conflict sets with correct provenance kinds
- `conda why python` in a real env reports history/pin provenance
- `--json` output is stable and schema-validated
- `conda wtf` behaves identically to `conda why`
- STRETCH (not required): `--last` works for failures from normal conda commands