from __future__ import annotations

from typing import TYPE_CHECKING

from conda.plugins import hookimpl

if TYPE_CHECKING:
    from argparse import ArgumentParser, Namespace
    from collections.abc import Iterator

    from conda.plugins.types import CondaSubcommand


def configure_parser(parser: ArgumentParser) -> None:
    from .cli import configure_parser as configure_command_parser

    configure_command_parser(parser)


def run(args: Namespace) -> int:
    from .cli import execute

    return execute(args)


@hookimpl
def conda_subcommands() -> Iterator[CondaSubcommand]:
    from conda.plugins.types import CondaSubcommand

    yield CondaSubcommand(
        name="why",
        summary="Explain why a conda operation fails or why a package is constrained.",
        action=run,
        configure_parser=configure_parser,
        aliases=("wtf",),
    )
