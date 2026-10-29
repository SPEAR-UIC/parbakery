#!/usr/bin/env python3
"""parbake/describe_csv.py - describe what is in one CSV file

Usage: python3 describe_csv.py data.csv [--preview N] [--json out.json]
All values are read as text: "NA" stays a word.
A preview and a full scan measure the same things.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import argparse
import json
import sys
import time
from pathlib import Path

import pandas

from arguments import add_separator
from checkpoints import fingerprint_settings
from identifiers import IdentifierCheck
from measuring import ColumnSummary
from reporting import print_report
from settings import (
    DEFAULT_PREVIEW_ROWS,
    DEFAULT_VALUES_SHOWN,
    DEFAULT_VALUES_TRACKED,
    LITERAL_READ_SETTINGS,
)
from sources import dataset_stem, read_header, rows_per_batch


def batches_from(path, separator, column_names, batch_rows, skip_rows, preview_rows):
    """Yield batches of rows after the first skip_rows, stopping at preview_rows."""
    # skiprows alone would eat the header and promote the next line to it.
    resume = {"header": None, "names": column_names, "skiprows": skip_rows + 1} if skip_rows else {}
    rows_read = skip_rows
    for batch in pandas.read_csv(path, sep=separator, chunksize=batch_rows,
                                 **resume, **LITERAL_READ_SETTINGS):
        if preview_rows is not None:
            batch = batch.iloc[: preview_rows - rows_read]
        if batch.empty:
            return
        rows_read += len(batch)
        yield batch
        if preview_rows is not None and rows_read >= preview_rows:
            return


def describe_csv(path, separator=",", preview_rows=None, batch_size=None,
                 values_tracked=DEFAULT_VALUES_TRACKED, values_shown=DEFAULT_VALUES_SHOWN,
                 identifier_check=None, on_progress=None, checkpoints=None, record_as=None):
    """Read a CSV, measure every column, return the summary dict.
    preview_rows=None reads all rows; record_as names the file when path is a local copy."""
    path = Path(path)
    started_at = time.monotonic()
    column_names = read_header(path, separator)
    summaries = {name: ColumnSummary(name, position, values_tracked)
                 for position, name in enumerate(column_names)}
    batch_rows = batch_size or rows_per_batch(len(column_names))
    if preview_rows is not None:
        batch_rows = min(batch_rows, preview_rows)

    stem = dataset_stem(path)
    fingerprint = fingerprint_settings(separator, values_tracked, preview_rows)
    rows_read, resumed_from, checkpoint_note = 0, None, None
    if checkpoints is not None:
        saved, checkpoint_note = checkpoints.load(stem, path, fingerprint)
        if saved:
            rows_read, summaries, identifier_state = checkpoints.rebuild(
                saved, column_names, values_tracked)
            if identifier_state and identifier_check is not None:
                identifier_check.restore(identifier_state)
            resumed_from = rows_read
    rows_at_last_save = rows_read

    for batch in batches_from(path, separator, column_names, batch_rows, rows_read,
                              preview_rows):
        for name in column_names:
            summaries[name].add_batch(batch[name])
            if identifier_check is not None:
                identifier_check.check_batch(name, batch[name])
        rows_read += len(batch)
        if checkpoints is not None and checkpoints.is_due(rows_read, rows_at_last_save):
            checkpoints.save(stem, path, fingerprint, rows_read, summaries, identifier_check)
            rows_at_last_save = rows_read
        if on_progress is not None:
            on_progress(rows_read, time.monotonic() - started_at)

    if checkpoints is not None:
        checkpoints.discard(stem)

    described = Path(record_as or path)
    return {
        "file": {
            "path": str(described.resolve()),
            "size_in_bytes": described.stat().st_size,
            "column_names": column_names,
            "column_count": len(column_names),
            "rows_read": rows_read,
            "scope": ("full scan" if preview_rows is None
                      else f"preview, first {preview_rows} rows"),
            "seconds_taken": round(time.monotonic() - started_at, 2),
            "resumed_from_row": resumed_from,
            "checkpoint_note": checkpoint_note,
        },
        "columns": {name: summary.as_dict(values_shown) for name, summary in summaries.items()},
        "identifiers": {
            "columns_of_concern": identifier_check.columns_of_concern() if identifier_check else [],
            "checked": identifier_check is not None,
        },
        # Live ColumnSummary objects, for callers needing more than as_dict().
        "_summaries": summaries,
    }


def show_progress(rows_read, seconds):
    """Print rows read and rate on one stderr line."""
    rate = rows_read / seconds if seconds else 0
    print(f"\r  read {rows_read:,} rows  ({rate:,.0f} rows/s)", end="", file=sys.stderr)


def main():
    """Parse arguments, describe the file, print the report, write JSON if asked."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv_path", type=Path, help="the CSV file to describe")
    parser.add_argument("--preview", nargs="?", type=int, const=DEFAULT_PREVIEW_ROWS,
                        default=None, metavar="N",
                        help=f"read only the first N rows (default {DEFAULT_PREVIEW_ROWS}); "
                             "without it the whole file is read")
    parser.add_argument("--batch-size", type=int, default=None, metavar="N",
                        help="rows read at a time (default: from file width)")
    parser.add_argument("--values-tracked", type=int, default=DEFAULT_VALUES_TRACKED,
                        help=f"distinct values counted per column (default {DEFAULT_VALUES_TRACKED:,})")
    parser.add_argument("--values-shown", type=int, default=DEFAULT_VALUES_SHOWN,
                        help=f"values listed per column (default {DEFAULT_VALUES_SHOWN})")
    parser.add_argument("--no-identifier-check", action="store_true",
                        help="skip the un-anonymised data check")
    add_separator(parser)
    parser.add_argument("--json", dest="json_path", type=Path,
                        help="also write the summary as JSON")
    parser.add_argument("--quiet", action="store_true", help="no progress while reading")
    arguments = parser.parse_args()
    if not arguments.csv_path.is_file():
        raise SystemExit(f"No such file: {arguments.csv_path}")

    result = describe_csv(
        arguments.csv_path,
        separator=arguments.separator,
        preview_rows=arguments.preview,
        batch_size=arguments.batch_size,
        values_tracked=arguments.values_tracked,
        values_shown=arguments.values_shown,
        identifier_check=None if arguments.no_identifier_check else IdentifierCheck(),
        on_progress=None if arguments.quiet else show_progress,
    )
    if not arguments.quiet:
        print(file=sys.stderr)
    print_report(result, arguments.values_shown)

    if arguments.json_path:
        payload = {key: value for key, value in result.items() if key != "_summaries"}
        arguments.json_path.write_text(json.dumps(payload, indent=2, default=str),
                                       encoding="utf-8")
        print(f"\nJSON written to {arguments.json_path}")


if __name__ == "__main__":
    main()
