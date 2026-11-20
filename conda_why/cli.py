from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from argparse import ArgumentParser, Namespace

    from .schema import ConflictSet

MODES = ("query", "create", "install", "update", "remove")

_MODE_HELP = {
    "query": "Report every constraint on a spec in the target environment (no solve).",
    "create": "Dry-run a create and explain it.",
    "install": "Dry-run an install and explain it.",
    "update": "Dry-run an update and explain it.",
    "remove": "Dry-run a remove and explain it.",
}


def configure_parser(parser: ArgumentParser) -> None:
    _add_shared_options(parser, last=True)
    parser.set_defaults(mode=None, specs=())
    parser.epilog = (
        "Explains conflicts and missing builds for the requested Python or platform. "
        "Plain missing packages are left to conda's own message."
    )
    subparsers = parser.add_subparsers(
        dest="mode",
        metavar="MODE",
        title="modes",
    )
    for mode in MODES:
        _add_mode_parser(subparsers, mode)


def _add_shared_options(
    parser: ArgumentParser, *, last: bool = False, suppress_defaults: bool = False
) -> None:
    default = argparse.SUPPRESS if suppress_defaults else False
    parser.add_argument(
        "--json",
        action="store_true",
        default=default,
        help="Report the complete result as one JSON document.",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        default=default,
        help="Include the minimal conflict set and raw solver evidence.",
    )
    parser.add_argument(
        "-c",
        "--channel",
        action="append",
        metavar="CHANNEL",
        default=argparse.SUPPRESS if suppress_defaults else None,
        help="Additional channel to use for the solve, in search order.",
    )
    parser.add_argument(
        "--override-channels",
        action="store_true",
        default=default,
        help="Do not search default or .condarc channels.",
    )
    parser.add_argument(
        "--compare-solvers",
        action="store_true",
        default=default,
        help="Compare solver results (not implemented).",
    )
    if last:
        parser.add_argument(
            "--last",
            action="store_true",
            help="Re-explain the most recent failed solve (not implemented).",
        )


def _add_mode_parser(subparsers, mode: str) -> None:
    from conda.cli.helpers import add_parser_prefix

    mode_parser = subparsers.add_parser(mode, help=_MODE_HELP[mode])
    add_parser_prefix(mode_parser)
    _add_shared_options(mode_parser, suppress_defaults=True)
    mode_parser.add_argument(
        "specs",
        nargs="*",
        metavar="SPEC",
        help="The spec whose constraints are reported."
        if mode == "query"
        else "MatchSpecs for the dry-run operation.",
    )


class _RequestError(Exception):
    pass


def execute(args: Namespace) -> int:
    if args.compare_solvers:
        return _not_implemented("only one adapter available")
    if args.last:
        if args.mode is not None or args.specs:
            return _invalid("--last cannot be combined with a mode or spec")
        return _not_implemented("--last")
    if args.mode is None:
        return _invalid("a mode is required: query, create, install, update, remove")
    if args.mode == "query":
        if len(args.specs) != 1:
            return _invalid(
                "query mode requires exactly one spec: conda why query <spec>"
            )
        return _query(args)
    return _job(args)


def _query(args: Namespace) -> int:
    from conda.models.match_spec import MatchSpec

    spec = args.specs[0]
    try:
        query = MatchSpec(spec)
    except Exception as exc:
        return _fail(args, f"invalid spec {spec!r}: {exc}")
    if query.name is None:
        return _fail(args, f"invalid spec {spec!r}: no package name")

    try:
        prefix = _resolve_prefix(args)
    except _RequestError as exc:
        return _fail(args, str(exc))

    from .provenance import collect_constraints
    from .render import render_query
    from .schema import WhyReport

    records = collect_constraints(mode="query", prefix=prefix)
    matches = [record for record in records if _constraint_name(record) == query.name]
    if not matches:
        return _fail(args, f"no constraint on {query.name!r} was found in {prefix}")

    report = WhyReport(
        mode="query",
        ok=True,
        matches=[_finding(record, query) for record in matches],
    )
    if args.json:
        _write_json(report)
    else:
        sys.stdout.write(render_query(report, prefix))
    return 0


def _resolve_prefix(args: Namespace) -> Path:
    from conda.base.context import context, locate_prefix_by_name

    if args.name:
        try:
            return Path(locate_prefix_by_name(args.name))
        except Exception as exc:
            raise _RequestError(str(exc)) from exc

    path = (
        Path(args.prefix).expanduser()
        if args.prefix
        else Path(context.default_prefix)
    )
    if not (path / "conda-meta").is_dir():
        raise _RequestError(f"environment not found: {path}")
    return path


def _constraint_name(record) -> str | None:
    from conda.models.match_spec import MatchSpec

    try:
        return MatchSpec(record.constraint.spec).name
    except Exception:
        return None


def _finding(record, query):
    from .schema import Finding

    claim = _claim(record)
    version = _query_version(query)
    build = query.get_exact_value("build")
    target = version or build
    if target:
        try:
            admits = _admits(record.constraint.spec, query, version, build)
        except Exception:
            admits = False
        claim += f"; {'allows' if admits else 'excludes'} {target}"
    return Finding(subject=query.name, claim=f"{claim}.", provenance=record)


def _query_version(query) -> str | None:
    if not query.version:
        return None
    text = str(query.version)
    if text.endswith(".*"):
        text = text[:-2]
    if any(char in text for char in "<>=!~,|*"):
        return None
    return text


def _admits(spec: str, query, version: str | None, build: str | None) -> bool:
    from conda.models.channel import Channel
    from conda.models.match_spec import MatchSpec
    from conda.models.records import PackageRecord

    constraint = MatchSpec(spec)
    probe = PackageRecord(
        name=query.name,
        version=version or "0",
        build=build or constraint.get_exact_value("build") or "0",
        build_number=0,
        channel=Channel("https://conda-why.invalid/noarch"),
        subdir="noarch",
    )
    return constraint.match(probe)


def _claim(record) -> str:
    constraint = record.constraint
    if constraint.kind == "history":
        if record.revision is not None:
            return f"Requested as {constraint.spec} in revision {record.revision}"
        return f"Requested as {constraint.spec} in history"
    if constraint.kind == "pinned":
        return f"Pinned as {constraint.spec}"
    if constraint.kind == "installed":
        return f"Installed as {constraint.spec}"
    if constraint.kind == "special_case":
        return f"Held to {constraint.spec} by the python minor-version rule"
    return f"Constrained as {constraint.spec}"


def _write_json(report) -> None:
    import json

    json.dump(report.to_dict(), sys.stdout, indent=2)
    sys.stdout.write("\n")


def _job(args: Namespace) -> int:
    from conda.base.context import context
    from conda.models.match_spec import MatchSpec

    from .adapters.base import SolveRequest
    from .adapters.crs import CrsAdapter
    from .diagnose import DiagnosisRequest, diagnose, missing_build
    from .mus import minimize
    from .provenance import collect_constraints, installed_dependencies
    from .render import render_job
    from .schema import ConflictSet, WhyReport

    operation = args.mode
    specs = tuple(args.specs)
    for spec in specs:
        try:
            MatchSpec(spec)
        except Exception as exc:
            return _fail(args, f"invalid spec {spec!r}: {exc}", mode="job")
    if operation in {"create", "install", "remove"} and not specs:
        return _fail(args, f"{operation} requires at least one spec", mode="job")

    try:
        prefix = _job_prefix(args, operation)
        channels = _channels(args)
    except _RequestError as exc:
        return _fail(args, str(exc), mode="job")

    subdirs = tuple(context.subdirs)
    constraints = collect_constraints(mode=operation, prefix=prefix, user_specs=specs)
    adapter = CrsAdapter(channels=channels, subdirs=subdirs)
    request = SolveRequest(
        operation=operation,
        prefix=prefix,
        specs_to_add=() if operation == "remove" else specs,
        specs_to_remove=specs if operation == "remove" else (),
        channels=channels,
        subdirs=subdirs,
    )
    with contextlib.redirect_stdout(sys.stderr):
        result = adapter.solve(request)
    if result.error_kind == "other":
        detail = (
            result.raw_error.splitlines()[0]
            if result.raw_error
            else "unknown error"
        )
        print(f"conda-why: solver failed: {detail}", file=sys.stderr)
        return 1

    report: WhyReport | None = None
    scope_message: str | None = None
    with contextlib.redirect_stdout(sys.stderr):
        if result.ok:
            report = WhyReport(
                mode="job",
                ok=True,
                implicit=[
                    record
                    for record in constraints
                    if record.constraint.kind != "user"
                ],
            )
        else:
            missing = None
            if result.pinned_conflict is not None:
                conflict = _pin_conflict_set(result, constraints, adapter)
            else:
                missing = missing_build(adapter, specs)
                if missing is not None and not missing.other_subdirs:
                    scope_message = missing.scope_message()
                elif missing is not None:
                    conflict = ConflictSet(
                        constraints=[],
                        provenance=[],
                        solver=adapter.name,
                        solver_calls=result.solver_calls,
                        raw_error=result.raw_error,
                    )
                else:
                    conflict = minimize(
                        adapter,
                        request,
                        constraints,
                        seed=adapter.conflict_hint(result),
                        prior_calls=result.solver_calls,
                        raw_error=result.raw_error,
                    )
            if report is None and scope_message is None:
                diagnosis = diagnose(
                    DiagnosisRequest(
                        operation=operation,
                        specs=specs,
                        prefix=prefix,
                        flags=_request_flags(args),
                        constraints=list(constraints),
                        installed=installed_dependencies(prefix),
                        adapter=adapter,
                        result=result,
                        conflict=conflict,
                        missing=missing,
                    )
                )
                report = WhyReport(
                    mode="job", ok=False, diagnosis=diagnosis, conflict=conflict
                )

    if report is None:
        return _fail(args, scope_message or "no diagnosis", mode="job")
    if args.json:
        _write_json(report)
    else:
        sys.stdout.write(render_job(report, prefix, explain=args.explain))
    return 0


def _job_prefix(args: Namespace, operation: str) -> Path:
    from conda.base.context import context, locate_prefix_by_name

    if args.prefix:
        path = Path(args.prefix).expanduser()
    elif args.name:
        if operation == "create":
            envs = tuple(context.envs_dirs)
            if not envs:
                raise _RequestError("no writable environments directory")
            path = Path(envs[0]) / args.name
        else:
            try:
                path = Path(locate_prefix_by_name(args.name))
            except Exception as exc:
                raise _RequestError(str(exc)) from exc
    elif operation == "create":
        raise _RequestError("create requires -n/--name or -p/--prefix")
    else:
        path = Path(context.default_prefix)

    if operation != "create" and not (path / "conda-meta").is_dir():
        raise _RequestError(f"environment not found: {path}")
    return path


def _channels(args: Namespace) -> tuple[str, ...]:
    from conda.base.context import context

    if args.override_channels:
        if not args.channel:
            raise _RequestError(
                "--override-channels requires at least one -c/--channel"
            )
        return tuple(args.channel)
    if not args.channel:
        return tuple(context.channels)
    return tuple(_dedupe([*args.channel, *context.channels]))


def _request_flags(args: Namespace) -> str:
    import shlex

    parts: list[str] = []
    if args.name:
        parts.extend(("-n", shlex.quote(args.name)))
    elif args.prefix:
        parts.extend(("-p", shlex.quote(str(Path(args.prefix).expanduser()))))
    for channel in args.channel or ():
        parts.extend(("-c", shlex.quote(channel)))
    if args.override_channels:
        parts.append("--override-channels")
    return " ".join(parts)


def _pin_conflict_set(result, constraints, adapter) -> ConflictSet:
    from .schema import ConflictSet

    conflict = result.pinned_conflict
    assert conflict is not None
    wanted = {
        name
        for spec in (*conflict.requested_specs, *conflict.pinned_specs)
        if (name := _spec_name(spec))
    }
    records = [
        record
        for record in constraints
        if record.constraint.kind in {"pinned", "user"}
        and _spec_name(record.constraint.spec) in wanted
    ]
    return ConflictSet(
        constraints=[record.constraint for record in records],
        provenance=records,
        solver=adapter.name,
        solver_calls=result.solver_calls,
        raw_error=result.raw_error,
    )


def _spec_name(spec: str) -> str | None:
    from conda.models.match_spec import MatchSpec

    try:
        return MatchSpec(spec).name
    except Exception:
        return None


def _dedupe(values):
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _fail(
    args: Namespace, message: str, *, mode: Literal["query", "job"] = "query"
) -> int:
    print(f"conda-why: {message}", file=sys.stderr)
    if args.json:
        from .schema import WhyReport

        _write_json(WhyReport(mode=mode, ok=False))
    return 1


def _not_implemented(detail: str) -> int:
    print(f"conda-why: not implemented: {detail}", file=sys.stderr)
    return 2


def _invalid(message: str) -> int:
    print(f"conda-why: {message}", file=sys.stderr)
    return 1
