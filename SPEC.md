# SPEC: conda-why (MVP)

## Summary
`conda why` is a conda plugin that turns a solver failure into a short diagnosis: what
actually blocks the solve, which constraint put it there, and what the user can try next.
It also answers "why is X constrained here?" for healthy environments.  The goal is to help
the user get themselves unstuck.

The solver's output is evidence, not the product. A typical `UnsatisfiableError` is a
long tree of candidate versions ending in "conflicts with the versions reported
above." conda-why reduces that to one headline, at most three findings, and zero to two
next steps, while surfacing the constraints users cannot see: environment history,
`pinned_packages`, `conda-meta/pinned`, installed records, and solver special cases.

Repo: `ryanskeith/conda-why` (may graduate to `conda-incubator`)
Scaffold: `conda/conda-plugin-template` (cookiecutter)

## Goals
- Explain *why* a create/install/update/remove would fail, in terms a user can act on
- Name the two or three packages or specs that actually cause the failure, not the
  solver's full exploration
- Surface hidden constraints and say where each came from
- Offer a safe next step or two when the cause is unambiguous
- Explain missing build coverage: the package exists, but not for the requested Python or
  platform
- Define a solver-agnostic diagnosis and evidence schema (beta prototype for a future
  standardized `UnsatisfiableError` payload)
- Explore saving the last failed solve so conda-why can re-explain it without retyping

## Stretch goal
- `--last`: re-explain the most recent failed solve. Depends on Spike B. Capturing
  failures from normal conda commands may not be feasible from a plugin.
  - Fallback UX: up-arrow to the failed command and insert `why` after `conda`.

## Non-goals (MVP)
- Plain package-not-found (a typo, a renamed package, a channel that is not enabled).
  conda's own message already names the missing package; conda-why exits 1 with a one-line
  pointer instead of diagnosing it.
- Multiple solver adapters (interface + one implementation). Target conda-rattler-solver.
- `--compare-solvers` (stubbed)
- Computing exact fallback versions (for example "the newest numpy for Python 3.9 is
  2.0.x"). The diagnosis states the cause; suggestions stay generic.
- `conda env create` / environment.yml inputs
- Performance budgets on the MUS loop
- Supporting conda versions older than the latest release (26.10.0)
- Upstreaming into conda proper

## Product contract (text output)

Default output is a diagnosis:

    $ conda why install "numpy=2.1"
    numpy 2.1 is not compatible with Python 3.9 in this environment.

      numpy 2.1 requires Python >=3.10                (package metadata)
      Python 3.9 was requested when this environment  (history, revision 3)
        was created

    Options:
      conda install "numpy<2.1"                   keep Python 3.9
      conda install "python>=3.10" "numpy=2.1"    move Python; other packages may need updates

    $ conda why install foo bar
    foo 2.0 and bar 1.0 need incompatible versions of libbaz.

      foo 2.0 (your request) requires libbaz >=2
      bar 1.0 (your request) requires libbaz <2

    Options:
      conda install "foo<2" bar
      conda install foo "bar<1"

Rules:
- One headline sentence that names the real blocker, not the solver's symptom.
- Up to three findings; an actionable diagnosis has at least one. Each finding names its
  subject and labels the source. Hidden sources (`history`, `pinned`, `installed`,
  `special_case`) are always labeled.
- Zero to two next steps, only when the cause is unambiguous. They are things to try,
  never a promise the solve will succeed. conda-why itself never mutates anything.
- Next-step commands echo the target environment (`-n`/`-p`) and the channel flags from
  the request, so a copied command cannot modify a different environment.
- Version advice uses a range derived from package metadata, never an invented exact
  version.
- Evidence (minimal conflict set, dependency edges, solver name and call count, raw
  solver text) is opt-in: `--explain` in text mode, always included in `--json`.
- At most three diagnosis findings in every mode, including the unknown fallback. The full
  minimal set stays in `--explain` and in `conflict`.
- When no rule matches, say conda-why could not identify a single cause, show up to three
  findings with sources, and offer no advice.

### Not a diagnosis

Plain missing packages are conda's message to deliver. When no requested spec, and no
direct dependency of one, has a candidate in any enabled channel or subdir, conda-why
prints one line and exits 1:

    conda-why: "nope" was not found in any enabled channel or subdir. conda-why explains
    missing builds for the requested Python or platform, not missing packages.

The same message names the requesting package when the missing name is a dependency
(`"broken" requires "missingdep", ...`). No diagnosis, findings, or next steps are
produced, and `--json` writes `ok: false`.

Exit codes:
- `0`: a diagnosis or query answer was produced (healthy or unsatisfiable)
- `1`: no answer (invalid invocation or spec, missing environment, no query match,
  missing package, internal error)
- `2`: not implemented (`--compare-solvers`, `--last` before Phase 6) or an argparse
  usage error such as an unknown option
- `--json` writes one complete JSON document to stdout on exit 0, and on exit 1 for a
  no-match or invalid request (`ok: false`, empty results). Internal errors write to
  stderr and emit no JSON.
- Warnings and logs go to stderr.
- Channel tokens are redacted in every output mode, text and JSON, including raw evidence.

## CLI

    conda why query   <spec>                      # provenance query, no solve
    conda why create  <specs...> [-n|-p ...]      # dry-run solve + explain
    conda why install <specs...> [-n|-p ...]
    conda why update  <specs...> [-n|-p ...]
    conda why remove  <specs...> [-n|-p ...]
    conda why --last                              # STRETCH: re-explain last failed solve

Flags (all modes): `--json`, `--explain`, `-c/--channel`, `--override-channels`
Stubbed: `--compare-solvers` exits 2 with "not implemented: only one adapter available".
`--last` is stubbed with the same exit code until Phase 6.
Alias: `conda wtf`, registered with `CondaSubcommand(aliases=("wtf",))`. Conda has no
hidden-subcommand flag, so `wtf` appears in `conda --help` with a terse summary.

Dispatch: the first positional is always the mode and must be exactly one of
`query|create|install|update|remove`. `--last` is the only invocation without a mode.
A bare spec is treated as an unknown mode and fails as an argparse usage error (exit 2)
that lists the valid modes, so a package named `install` is queried as
`conda why query install`; mode names are reserved words and never mean a package.
`--json`, `--explain`, `-c/--channel`, `--override-channels`, and `--compare-solvers`
are accepted before or after the mode; `-n`/`-p` follow the mode, as they do for
conda's own commands.

Behavior:
- Never mutates any environment. All solves are dry-run.
- Job mode on success: report the solved state and the implicit constraints that shaped it.
- Job mode on failure: produce a diagnosis, with the minimal conflict set as evidence.
- Query mode: no solve. Search resolved constraints in the target environment (history,
  pins, installed records, special cases). Report every constraint whose package name
  matches the query, regardless of version; when the query carries a version or build,
  annotate each finding with whether that constraint admits it (`allows 2.1`, `excludes
  2.1`). Conda has no spec-vs-spec matcher, so compare fields against a record synthesized
  from the query; annotations therefore cover concrete versions and prefixes (`numpy=2.1`)
  but not range-only queries. Print every matching constraint with its source. A query that matches
  nothing exits 1.
- Use `CondaSubcommand(configure_parser=...)` so argparse is integrated with conda's.

## Architecture

    conda_why/
      plugin.py          # hookimpl: conda_subcommands (why + wtf), + failure hook if Spike B finds one
      cli.py             # parser config, dispatch, exit codes
      schema.py          # dataclasses: Constraint, ProvenanceRecord, ConflictSet, Diagnosis, WhyReport, FailedSolve
      provenance.py      # resolves where each constraint came from
      diagnose.py        # conflict set + provenance + candidate metadata -> Diagnosis
      adapters/
        base.py          # SolverAdapter protocol
        crs.py           # wraps conda-rattler-solver
      mus.py             # deletion-based MUS extraction (optionally seeded)
      last.py            # STRETCH: last failed-solve record read/write
      render.py          # text output (decoupled from schema)

Rule: nothing outside `adapters/` imports solver internals. `diagnose.py` works only from
schema types and candidate metadata the adapter exposes.

### schema.py (the artifact that matters most)

Tentative.

- `Constraint`: `spec: str` (MatchSpec string), `kind: Literal["user", "history",
  "pinned", "installed", "special_case", "transitive"]`, `relaxable: bool`
  - `relaxable` mapping: `user`, `history`, `pinned` are True (a different command can
    change them); `installed`, `special_case`, `transitive` are False.
- `ProvenanceRecord`: `constraint`, `source` (file path / config key / rule id / record),
  `revision: int | None`, `timestamp: datetime | None`, `detail: str`
- `ConflictSet`: `constraints: list[Constraint]`, `provenance: list[ProvenanceRecord]`
  (same length and order as `constraints`), `solver: str`, `solver_calls: int`,
  `raw_error: str` (solver text, kept for `--explain` and `--json`)
- `Finding`: `subject: str` (package or spec), `claim: str` (one plain sentence),
  `provenance: ProvenanceRecord`
- `NextStep`: `text: str` (a command or plain action), `note: str | None`
- `Diagnosis`: `kind: Literal["unsupported_python", "unsupported_platform",
  "incompatible_specs", "pinned_constraint", "installed_constraint",
  "channel_priority", "virtual_package", "build_variant", "unknown"]`,
  `headline: str`, `findings: list[Finding]` (0-3), `next_steps: list[NextStep]` (0-2)
- `WhyReport`: `mode: Literal["query", "job"]`, `ok: bool`, `diagnosis: Diagnosis | None`,
  `conflict: ConflictSet | None`, `implicit: list[ProvenanceRecord]`,
  `matches: list[Finding]`, `schema_version: str`
  - Query mode fills `matches`; job failure fills `conflict` and `diagnosis`; job success
    fills `implicit`.
  - Duplicate constraints keep one `Constraint` and one `ProvenanceRecord`, with the
    highest-precedence source as `source` and the rest listed in `detail`.
- `FailedSolve`: see `last.py` below
- `--json` output is a direct serialization of `WhyReport`, beta. `schema_version` starts
  at `"0.1"`; datetimes are ISO 8601 with offset; list order is defined by the rules
  below, not by dict insertion. `schema.py` owns `to_dict`/`from_dict` round-tripping.

### adapters/base.py

    class SolverAdapter(Protocol):
        name: str
        def solve(self, request: SolveRequest) -> SolveResult: ...
        def candidates(self, spec: str, python: str | None = None) -> list[CandidateInfo]: ...
        def conflict_hint(self, result: SolveResult) -> list[str]: ...   # specs the solver itself blamed

`SolveRequest` carries the operation (`create|install|update|remove`), prefix (which may
not exist for `create`), channels, subdirs, specs to add, specs to remove, update
modifier, and `ignored_constraints: tuple[IgnoredConstraint, ...]` for MUS.
`IgnoredConstraint` is `(kind, spec, source)`: user specs are omitted from the request,
history and `conda-meta/pinned` entries are relaxed by solving against an overlay prefix
with those entries rewritten, and condarc `pinned_packages` can only be dropped as a
group via `ignore_pinned`. This is why the adapter is command-aware:
`install`/`update`/`remove` read the target environment, `create` does not.

`SolveResult` is either success (`records: list[PackageRecord]`) or failure with
`error_kind: Literal["unsatisfiable", "other"]` and `raw_error: str`. A
requested-versus-pin conflict also fills `pinned_conflict` with the requested specs,
pinned specs, and prefix, because conda raises it before the solver runs. A missing
package arrives as a failed solve, like any other; `diagnose.missing_build()` decides
whether it is missing build coverage or out of scope. `solver_calls` counts every
`solve()` the adapter performs, including seeded probes; `conflict_hint` and `candidates`
do not count.

`candidates` returns the metadata the diagnosis needs: version, build, subdir, channel,
and `depends` for each candidate, optionally filtered to candidates compatible with a
Python version. It is read-only metadata lookup, never a solve.

`conflict_hint` parses the solver's native conflict explanation into specs. It returns an
empty list if parsing isn't possible.

### provenance.py

Sources, in precedence order:

1. User specs from the current invocation -> `user`
2. `conda-meta/history` requested specs (revision + timestamp) -> `history`
3. `pinned_packages` (condarc) and `conda-meta/pinned` -> `pinned`
4. Installed records in the target prefix -> `installed`
5. Hardcoded rules table for solver special-cases (e.g. python minor pinning on update)
   -> `special_case`, `relaxable=False`
6. Dependency edges from candidate metadata (`a 1.0 depends on c <2`) -> `transitive`,
   used only as evidence for a finding

Subdir/channel mismatch detection is reported as a provenance note on a finding, not as a
constraint.

Command-aware collection:

| Mode | user | history | pins | installed | special_case |
| --- | --- | --- | --- | --- | --- |
| `create` | yes | no | condarc only | no | yes |
| `install` / `update` | yes | yes | yes | yes | yes |
| `remove` | specs to remove | no | no | yes (dependents) | yes |
| query | query spec | yes | yes | yes | yes |

Parsing rules:
- History: read the requested specs recorded by conda, not every installed package;
  capture revision and timestamp when present. Conda only keeps a spec when its package
  is still installed, so a requested package that was later removed contributes nothing.
  Skip malformed lines and note the skip in `detail`.
- Pins: `conda-meta/pinned` wins over `pinned_packages`; the `source` string names the
  file or config key.
- Missing files are not errors; the source simply contributes nothing.
- A constraint that appears in several sources is reported once with its highest
  precedence source, and the others are noted in `detail`.

### diagnose.py

A pure function from the request, the minimal conflict set, its provenance, and the
adapter's candidate metadata to a `Diagnosis`. Rules are checked in order; the first
match wins. No LLM calls, no solver imports.

| Kind | Evidence required | Headline shape |
| --- | --- | --- |
| `unsupported_python` | package candidate requires a Python range that excludes the Python constraint in play | "<pkg> <ver> is not compatible with Python <x> in this environment." |
| `unsupported_platform` | candidates exist for another subdir but none for the current subdirs | "<pkg> has no build for <subdir>." |
| `incompatible_specs` | two constraints require disjoint ranges of one package | "<a> and <b> need incompatible versions of <pkg>." |
| `pinned_constraint` | a pin is in the minimal set | "<pkg> is held by a conda pin in <source>." |
| `installed_constraint` | an installed record's requirement is in the minimal set | "<installed> currently requires <pkg> <range>." |
| `channel_priority` | a viable candidate exists only in a channel excluded by priority | "<pkg> is only available from <channel>, which your channel priority excludes." |
| `virtual_package` | requirement on `__glibc`, `__cuda`, or `__osx` beyond the detected value | "<pkg> needs <virtual> >=<v>; this system provides <v>." |
| `build_variant` | same package name, incompatible build strings or variants | "<a> and <b> need different builds of <pkg>." |
| `unknown` | nothing above matched | "conda-why could not identify a single cause." |

`unsupported_platform` skips MUS: there is no conflict set to minimize, so job mode
branches before the MUS loop when a requested spec, or a direct dependency, has no build
for the current subdirs but does have one elsewhere.

MVP implements every kind except `channel_priority` and `virtual_package`; those
failures fall through to `unknown` until a later phase. `incompatible_specs` compares two
user constraints; when the blocking requirement belongs to an installed package, the
diagnosis is `installed_constraint` instead.

Findings always carry the provenance that made the constraint visible, so the hidden
sources (history, pins, installed records, special cases) appear by name in the default
output. Next steps come from per-kind templates: `unsupported_python` offers "pin the
package back" and "move Python to the range the package requires",
`installed_constraint` offers "update or remove the blocking package", and `unknown`
offers nothing. Every command echoes the request's `-n`/`-p` and channel flags.

### mus.py

Evidence extraction, not the product. Job mode skips MUS when `missing_build` finds no
build for the current subdirs, or when the result carries a `pinned_conflict`; those
diagnoses come from candidate metadata or the structured pin error alone. Otherwise, deletion-based over `user | history | pinned`
constraints in that order: for each relaxable constraint, re-solve without it. If the
solve is still UNSAT, drop the constraint permanently; otherwise keep it. The result is a
minimal UNSAT subset. `installed` and `special_case` constraints are never dropped; they join the
set when their package name appears in the minimal set or in the seed. Relaxation goes
through `IgnoredConstraint` records (user omitted, history and prefix pins via overlay,
condarc pins as a group). No call or time limit for MVP; `solver_calls` includes the
initial failed solve and every probe. When several irreducible sets exist, the first one found in that iteration
order is reported.

Seeding: if `conflict_hint` (or a `FailedSolve.seed`) is available, try the seeded subset
first. If the seeded subset is UNSAT on its own, run deletion only within the seed.
Otherwise, fall back to the full constraint set. Skip the seed probe when the seed covers
every relaxable constraint. Treat the seed as a hint, never as the result.

### last.py (STRETCH)
- Location: conda's user-level data directory, `~/.conda/why/last_failed_solve.json` by
  default, resolved from conda's data path when available. The record is global, not
  per-env, because a failed `create` has no `conda-meta/`.
- Single record, overwritten on each failure. No history accumulates.
- Write atomically (temp file + `os.replace`) with file mode `0600`.
- `FailedSolve` contents:
  - Reproduce: `argv`, `command`, `specs`, `prefix`, `channels`, `subdirs`,
    `solver` (`{name, version}`), `conda_version`, `timestamp`
  - Head start: `seed` (specs from the solver's conflict output), `constraints` (resolved
    history/pinned/installed/special-case constraints with `kind`, snapshotted at failure)
  - Staleness check: `repodata_fingerprint` (`{channel_url: {mtime, etag}}`)
  - Debug: `raw_error` (truncated to 8 KiB)
- Never store: full repodata, or channel tokens. Redact `/t/<token>/` in URLs and `argv`,
  and any token in `raw_error`.
- `--last` loads the record, warns if the repodata fingerprint or env constraints changed
  since the failure, then runs job mode seeded with `seed`. A missing or corrupt record
  exits 1 with a plain message. Failures produced by `conda why` itself are not recorded;
  the diagnosis is already on screen.
- Only in scope if Spike B finds a hook that fires on `UnsatisfiableError` for normal
  commands. If not, document the needed upstream hook instead.

## Spikes (resolve before implementing the dependent phase)

**A — Constraint relaxation and diagnostics through the solver API.**
History and pinned specs are injected inside the solver, not passed in by the caller.
Investigate: `context.ignore_pinned` (`--no-pin`), `UpdateModifier` options, and whether
`crs.py` must construct the solve so history specs are passed explicitly. Also check what
conda-rattler-solver exposes for its native conflict explanation (feeds `conflict_hint`)
and for candidate dependency metadata (feeds `candidates`). Output: `docs/spike-a.md`
with the exact mechanism for ignoring a constraint, the available diagnostic structure,
and a recommended approach. Timebox one working session.

**B — Capturing failures from normal `conda install/create/...`. (STRETCH gate)**
Check which plugin hooks (pre/post-command, exception handling) fire on
`UnsatisfiableError` and what context they can see (argv, prefix, exception text). Output:
`docs/spike-b.md` with a yes/no verdict and evidence. If none fire, drop `--last` from
the MVP and document the needed upstream hook. Timebox one working session.

## Testing
- Synthetic local channel generated by `tests/fixtures/synth_channel.py` (repodata.json, no
  network). Required diagnosis scenarios:
  1. Requested package unsupported by the Python requested in the same command
  2. Requested package unsupported by the environment's history Python
  3. Update of a package whose new version's dependency needs a newer Python
  4. Two user specs directly conflict on a shared dependency
  5. User spec conflicts with an installed record's requirement
  6. `pinned_packages` or `conda-meta/pinned` blocks the request
  7. Missing package is out of scope: exit 1, one-line pointer, no diagnosis
  8. No build for the current subdir, for the request or a direct dependency
  9. Healthy solve with implicit history pin reported
  10. Unknown fallback: up to three findings shown, no advice
- Real solver output corpus: collect `UnsatisfiableError` texts from public GitHub issues
  and pull requests, reproduce the ones that still solve against the synthetic channel,
  and keep the texts as fixtures for `conflict_hint` parsing. Prefer these over
  hand-written solver text when extending diagnosis rules.
- For every scenario assert `Diagnosis.kind`, the culprit subjects, the provenance kinds,
  and whether next steps are present.
- MUS seeding: a correct seed reduces `solver_calls`; a wrong or partial seed still yields
  the correct minimal set.
- Query mode: name match, a version query annotated as allows/excludes, nothing matched
  (exit 1).
- `--json` output validated by round-tripping through `schema.py`, including the
  `ok: false` report for a no-match query.
- Read-only: assert the prefix and `conda-meta` are unchanged after every job-mode test.
- Plugin smoke tests: `conda why --help` and `conda wtf --help` exit 0.
- Real-channel tests (`tests/test_real_channels.py`): written,
  `@pytest.mark.skip(reason="network/flaky; enable manually")`
- STRETCH: `last.py` overwrite semantics, atomic write, `0600` mode, token redaction,
  staleness warning.

## Agent workflow (tests-first, gated)
For each phase:
1. Write tests for the phase. **Stop. Request approval.**
2. After approval, implement until tests pass.
3. Summarize changes and any spec deviations. **Stop. Request approval.**

Phases:
0. Treat the current repository as the cookiecutter baseline; register `why` + `wtf`;
   CLI skeleton; `--compare-solvers` stub
1. Spike A (findings may reshape `schema.py` or the adapter protocol, so it comes before
   schema is frozen)
2. `schema.py` + `provenance.py` + query mode
3. `adapters/` (base + `crs.py`, including `conflict_hint` and `candidates`)
4. `mus.py` (with seeding)
5. `diagnose.py` + `render.py` + job modes
6. STRETCH: Spike B → `last.py` + `--last` (only if Spike B finds a viable hook)

Do not start phase N+1 without approval. Do not change `schema.py` public fields after
Phase 2 without flagging it in the change summary.

## Conventions
- Python 3.11+; follow the cookiecutter template defaults for tooling
- `conda>=26.10.0` and `conda-rattler-solver>=26.10,<27` pinned in pyproject
- No other new runtime dependencies without asking
- Type hints throughout; frozen dataclasses for schema
- JSON is a beta contract: versioned, documented, no compatibility promise yet
- Read-only: any code path that could mutate a prefix is a bug

## MVP acceptance
- The nine synthetic diagnosis scenarios produce the expected diagnosis kind, culprits,
  and provenance kinds; actionable kinds include at least one next step
- The out-of-scope missing package exits 1 with the pointer and produces no diagnosis
- Hidden constraints (history, pins, installed records, special cases) are named with
  their source in default output
- Suggested commands echo the request's `-n`/`-p` and channel flags
- `conda why query python` in a real environment reports history and pin provenance
- `--json` is beta, versioned, and round-trips through `schema.py`
- The unknown fallback shows at most three findings and never invents advice
- `conda wtf` behaves identically to `conda why`
- No code path mutates an environment
- STRETCH (not required): `--last` works for failures from normal conda commands

## Open questions (post-MVP)
- Which additional real-world failure cases get first-class diagnoses (solver gave up,
  multiple independent conflicts)
- Computing exact fallback versions for suggestions
- `conda env create` and environment.yml support
