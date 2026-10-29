"""parbake/arguments.py - command-line flags shared by parbakery.py and the utilities

Every parser reads flags from @files too; later flags win.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import argparse
import shlex
from pathlib import Path

from settings import DEFAULT_AIDRIN_COMMAND, DEFAULT_WORKER_LIMIT


def read_argument_file_line(line):
    """Split an @file line as a shell would; # starts a comment."""
    return shlex.split(line, comments=True)


def make_parser(description):
    """Return a parser that also reads flags from @files."""
    parser = argparse.ArgumentParser(description=description, fromfile_prefix_chars="@")
    parser.convert_arg_line_to_args = read_argument_file_line
    return parser


def directory(text):
    """argparse type: return an existing directory, resolved."""
    path = Path(text).resolve()
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"not a directory: {path}")
    return path


def add_separator(parser):
    """Add --separator."""
    parser.add_argument("--separator", default=",", help="field separator (default: ,)")


def add_workers(parser):
    """Add --workers."""
    parser.add_argument(
        "--workers", type=int, default=None, metavar="N",
        help=f"files read at once, one process each (default: up to "
             f"{DEFAULT_WORKER_LIMIT}, capped by CPU count; never more than the files; "
             "more than the CPUs is allowed, with a warning)")


def add_staging(parser):
    """Add --batch-local-copies and --local-copy-dir."""
    parser.add_argument(
        "--batch-local-copies", action="store_true",
        help="each worker copies its file to local disk and reads it there; for "
             "Lustre and similar, where opening a file is slow")
    parser.add_argument(
        "--local-copy-dir", metavar="DIR", default=None,
        help="where --batch-local-copies puts the copies (default: $TMPDIR)")


def add_enrich(parser):
    """Add --enrich, --enrich-profile and --aidrin."""
    parser.add_argument(
        "--enrich", action="store_true",
        help="after the pass, run AIDRIN readiness metrics over each documented "
             "CSV and add an _enrich block to its par-baked Croissant")
    parser.add_argument(
        "--enrich-profile", metavar="FILE", default=None,
        help="YAML or JSON naming the column roles AIDRIN needs (target, "
             "quasi-identifiers, sensitive, timestamp, path columns...). "
             "Without it, only the metrics that need no roles run")
    parser.add_argument(
        "--aidrin", metavar="CMD", default=DEFAULT_AIDRIN_COMMAND,
        help="the aidrin command to run (default: %(default)s); AIDRIN stays "
             "out of parbake's own environment")
