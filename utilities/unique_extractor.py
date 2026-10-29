#!/usr/bin/env python3
"""parbake/utilities/unique_extractor.py - distinct values of one column across a directory of CSVs

    python3 utilities/unique_extractor.py DIR --column USERNAME --out values.csv
Output: a one-column CSV, header first; values exactly as written, blanks kept.
Written per file as each finishes: a value repeats once per file holding it.
dedupe_values.py removes the repeats.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import csv
import sys
import time
from contextlib import ExitStack
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas  # noqa: E402

from arguments import add_separator, add_staging, add_workers, directory, make_parser  # noqa: E402
from progress import ProgressDisplay  # noqa: E402
from settings import LITERAL_READ_SETTINGS, MAXIMUM_BATCH_ROWS  # noqa: E402
from sources import estimate_row_count, find_files  # noqa: E402
from staging import StagingArea, staged_copy, staging_location  # noqa: E402
from workers import describe_error, run_pool, send, worker_count  # noqa: E402


def distinct_values(job, messages):
    """Return one file's distinct values in the column; runs in a worker."""
    csv_file, column, separator, staging_root = job
    report = partial(send, messages, csv_file.name)
    started = time.monotonic()
    values, rows_read = set(), 0

    if staging_root:
        report("copying")
    with staged_copy(staging_root, csv_file) as (path, copy_problem):
        report("estimate", estimate_row_count(path))
        report("start")
        try:
            # One column kept, so the largest batch parbake allows.
            for batch in pandas.read_csv(path, sep=separator, usecols=[column],
                                         chunksize=MAXIMUM_BATCH_ROWS,
                                         **LITERAL_READ_SETTINGS):
                values.update(batch[column])
                rows_read += len(batch)
                report("progress", rows_read)
            outcome = {"ok": True, "values": values}
        except Exception as problem:
            # Includes a file without the column.
            outcome = {"ok": False, "problem": describe_error(problem)}

    outcome.update(file=csv_file, rows_read=rows_read)
    if copy_problem is not None:
        outcome["staging_problem"] = describe_error(copy_problem)
    if outcome["ok"]:
        report("finished", {"state": "done", "rows_read": rows_read,
                            "seconds": time.monotonic() - started,
                            "note": f"{len(values):,} distinct"})
    else:
        report("finished", {"state": "failed", "note": outcome["problem"][:60]})
    return outcome


def write_values(writer, output, outcomes, job, future):
    """Write one finished file's values and record its outcome; parent only."""
    try:
        outcome = future.result()
    except Exception as problem:
        outcome = {"ok": False, "file": job[0], "problem": f"worker failed: {describe_error(problem)}"}
    if outcome["ok"]:
        writer.writerows([value] for value in outcome["values"])
        output.flush()
    outcome["distinct"] = len(outcome.pop("values", ()))
    outcomes.append(outcome)


def main():
    """Parse the command line, read every file, write the values."""
    parser = make_parser(__doc__.splitlines()[0])
    parser.add_argument("directory", type=directory,
                        help="directory of CSVs (subdirectories are not entered)")
    parser.add_argument("--column", required=True, help="the column to pull values from")
    parser.add_argument("--out", required=True, help="where to write the values (a CSV)")
    add_workers(parser)
    add_separator(parser)
    add_staging(parser)
    arguments = parser.parse_args()

    csv_files, _ = find_files(arguments.directory)
    if not csv_files:
        raise SystemExit(f"No CSV files in {arguments.directory}")
    workers = worker_count(arguments.workers, len(csv_files))
    print(f"Reading column {arguments.column!r} from {len(csv_files):,} CSV file(s) "
          f"in {arguments.directory}")
    print(f"  {workers} worker(s)")
    if arguments.batch_local_copies:
        print(f"  each worker copies its file to "
              f"{staging_location(arguments.local_copy_dir)} first, reads it there, "
              "then deletes it")
    print()

    display = ProgressDisplay([csv_file.name for csv_file in csv_files],
                              stream=sys.stdout, workers=workers)
    display.draw(force=True)
    outcomes = []
    with open(arguments.out, "w", newline="", encoding="utf-8") as output, ExitStack() as cleanup:
        writer = csv.writer(output)
        writer.writerow([arguments.column])
        staging_root = None
        if arguments.batch_local_copies:
            staging_root = cleanup.enter_context(StagingArea(arguments.local_copy_dir)).root
        jobs = [(csv_file, arguments.column, arguments.separator, staging_root)
                for csv_file in csv_files]
        run_pool(distinct_values, jobs, workers, display,
                 on_done=partial(write_values, writer, output, outcomes))
    display.finish()

    failed = [outcome for outcome in outcomes if not outcome["ok"]]
    print(f"\n  {len(outcomes) - len(failed):,} of {len(outcomes):,} file(s) read")
    for outcome in sorted(failed, key=lambda outcome: outcome["file"]):
        print(f"  could not read {outcome['file'].name}: {outcome['problem']}")
    for outcome in outcomes:
        if outcome.get("staging_problem"):
            print(f"  could not copy {outcome['file'].name} locally, read it in place "
                  f"({outcome['staging_problem']})")
    written = sum(outcome["distinct"] for outcome in outcomes)
    print(f"\nWrote {written:,} value(s) to {arguments.out}, with repeats across files; "
          "dedupe_values.py removes them")


if __name__ == "__main__":
    main()
