#!/usr/bin/env python3
"""parbake/parbakery.py - document every CSV in a directory

    python3 parbakery.py DIR [--full | --preview N] [--out DIR] [@flags.args]
Per CSV: a text report, a par-baked Croissant and its Markdown.
Per directory: an index listing every file.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import contextlib
import sys
import traceback
from contextlib import ExitStack
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Optional

from arguments import add_enrich, add_separator, add_staging, add_workers, directory, make_parser
from checkpoints import CheckpointStore
from describe_csv import describe_csv
from enrich import enrich_documented_files
from identifiers import IdentifierCheck, names_worth_checking
from measuring import quiet_columns
from parbaked_croissant import render_parbaked_markdown, write_parbaked_croissant
from previous_run import expected_outputs, outcome_from_previous_run, split_off_done
from progress import ProgressDisplay, print_key
from reporting import build_index, print_report
from settings import (
    CHECKPOINT_DIRECTORY_NAME,
    CHECKPOINT_EVERY_ROWS,
    DEFAULT_OUTPUT_DIRECTORY,
    DEFAULT_PREVIEW_ROWS,
    DEFAULT_VALUES_SHOWN,
    DEFAULT_VALUES_TRACKED,
    INDEX_FILENAME,
)
from sources import estimate_row_count, find_files
from staging import StagingArea, staged_copy, staging_location
from workers import apply, describe_error, run_pool, send, worker_count


@dataclass
class Run:
    """Settings every file in a run is read with; plain data, so it pickles."""
    output_directory: Path
    settings: dict
    checkpoint_directory: Optional[Path] = None
    checkpoint_every: int = CHECKPOINT_EVERY_ROWS
    staging_root: Optional[Path] = None

    def checkpoints(self):
        """Return a CheckpointStore, disabled when there is no directory."""
        return CheckpointStore(self.checkpoint_directory,
                               enabled=self.checkpoint_directory is not None,
                               every_rows=self.checkpoint_every)


def describe_one_csv(csv_file, output_directory, settings, on_progress=None,
                     checkpoints=None, record_as=None):
    """Describe one CSV, write its outputs, return its outcome; never raise.
    record_as: the original when csv_file is a local copy; outputs are named after it."""
    recorded = record_as or csv_file
    outputs = expected_outputs(output_directory, recorded, not settings.get("skip_croissant"))
    report_path = outputs["report"]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = describe_csv(
            csv_file,
            separator=settings["separator"],
            preview_rows=settings["preview_rows"],
            batch_size=settings["batch_size"],
            values_tracked=settings["values_tracked"],
            values_shown=settings["values_shown"],
            identifier_check=None if settings["skip_identifier_check"] else IdentifierCheck(),
            on_progress=on_progress,
            checkpoints=checkpoints,
            record_as=recorded,
        )
    except Exception as problem:
        report_path.write_text(f"Could not read {recorded}\n\n{traceback.format_exc()}",
                               encoding="utf-8")
        return {"file": recorded, "ok": False, "problem": describe_error(problem),
                "report_path": report_path}

    with open(report_path, "w", encoding="utf-8") as handle, contextlib.redirect_stdout(handle):
        print_report(result, settings["values_shown"])

    # A Croissant failure is recorded, not raised: the report is already written.
    croissant_path = outputs.get("croissant")
    markdown_path = croissant_problem = None
    if croissant_path:
        try:
            document = write_parbaked_croissant(result, croissant_path)
            markdown_path = outputs["markdown"]
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render_parbaked_markdown(document), encoding="utf-8")
        except Exception as problem:
            croissant_problem = describe_error(problem)

    empty, constant = quiet_columns(result["columns"])
    return {
        "file": recorded,
        "ok": True,
        "rows_read": result["file"]["rows_read"],
        "column_count": result["file"]["column_count"],
        "scope": result["file"]["scope"],
        "seconds": result["file"]["seconds_taken"],
        "columns_to_check": names_worth_checking(result["identifiers"]["columns_of_concern"]),
        "empty_columns": empty,
        "constant_columns": constant,
        "report_path": report_path,
        "croissant_path": croissant_path,
        "markdown_path": markdown_path,
        "croissant_problem": croissant_problem,
    }


def finished_fields(outcome):
    """Return the display fields for a finished file."""
    if not outcome["ok"]:
        return {"state": "failed", "note": outcome["problem"][:60]}
    note = [f"{outcome['column_count']} cols"]
    constant = len(outcome["empty_columns"]) + len(outcome["constant_columns"])
    if constant:
        note.append(f"{constant} constant")
    if outcome["columns_to_check"]:
        note.append(f"check {len(outcome['columns_to_check'])}")
    return {"state": "done", "rows_read": outcome["rows_read"],
            "seconds": outcome["seconds"], "note": ", ".join(note)}


def estimated_rows_for(path, settings):
    """Return the row estimate for the bar, capped at the preview limit."""
    estimate = estimate_row_count(path)
    if settings["preview_rows"] is not None and estimate is not None:
        return min(estimate, settings["preview_rows"])
    return estimate


def describe_file(csv_file, run, report):
    """Copy if staging, describe the file, report progress through report()."""
    if run.staging_root:
        report("copying")
    with staged_copy(run.staging_root, csv_file) as (path, problem):
        if run.staging_root:
            # Estimated here: cheap on the local copy, slow on the shared filesystem.
            report("estimate", estimated_rows_for(path, run.settings))
        report("start")
        outcome = describe_one_csv(
            path, run.output_directory, run.settings,
            on_progress=lambda rows, seconds: report("progress", rows, seconds),
            checkpoints=run.checkpoints(), record_as=csv_file)
    if problem is not None:
        outcome["staging_problem"] = describe_error(problem)
    report("finished", finished_fields(outcome))
    return outcome


def describe_in_worker(job, messages):
    """Describe one (csv_file, run) job in a worker process."""
    csv_file, run = job
    return describe_file(csv_file, run, partial(send, messages, csv_file.name))


def run_in_parallel(csv_files, run, workers, display):
    """Describe the files in a worker pool; return outcomes in file order."""
    futures = run_pool(describe_in_worker, [(csv_file, run) for csv_file in csv_files],
                       workers, display)
    results = []
    for csv_file, future in zip(csv_files, futures):
        try:
            results.append(future.result())
        except Exception as problem:
            # The worker died instead of returning a failure.
            report_path = expected_outputs(run.output_directory, csv_file)["report"]
            results.append({"file": csv_file, "ok": False,
                            "problem": f"worker failed: {describe_error(problem)}",
                            "report_path": report_path})
            display.update(csv_file.name, state="failed",
                           note=f"worker failed: {type(problem).__name__}")
    return results


def report_to_display(display, name, kind, payload=0, seconds=0.0):
    """Apply one progress message to the display and redraw."""
    apply(display, (kind, name, payload, seconds))
    display.draw(force=kind in ("copying", "finished"))


def run_one_at_a_time(csv_files, run, display):
    """Describe the files in this process, in order (--workers 1)."""
    return [describe_file(csv_file, run, partial(report_to_display, display, csv_file.name))
            for csv_file in csv_files]


def announce(directory_to_scan, to_read, skipped, other_files, settings, workers,
             checkpoint_directory, staging, skip_existing):
    """Print what the run will do before it starts."""
    # A silent run over a thousand files looks hung.
    print(f"Scanning {directory_to_scan}")
    print(f"  {len(to_read) + len(skipped)} CSV file(s), {len(other_files)} other file(s)")
    if skip_existing:
        print(f"  {len(skipped)} already documented, skipping "
              f"{'it' if len(skipped) == 1 else 'them'}; {len(to_read)} to read")
    print(f"  reading {settings['scope_description']}")
    print(f"  {workers} worker(s)")

    if not checkpoint_directory:
        print("  checkpointing off")
    elif waiting := CheckpointStore(checkpoint_directory).outstanding():
        print(f"  {len(waiting)} checkpoint(s) from an earlier run; those "
              "files will carry on rather than start again")

    if staging:
        print(f"  each worker copies its file to {staging} first, reads it there, "
              "then deletes it")
        print(f"  so never more than {workers} {'copy' if workers == 1 else 'copies'} "
              "on local disk at once")
    print()
    print_key(staging=bool(staging))
    print()


def read_files(csv_files, run, workers, staging_parent, stage_locally):
    """Describe the files with a progress display; return their outcomes."""
    # Staging: each worker estimates from its local copy, not the slow filesystem.
    estimates = {} if stage_locally else {
        csv_file.name: estimated_rows_for(csv_file, run.settings) for csv_file in csv_files}
    display = ProgressDisplay([csv_file.name for csv_file in csv_files], estimates,
                              stream=sys.stdout, workers=workers)
    display.draw(force=True)

    with ExitStack() as cleanup:
        if stage_locally:
            run.staging_root = cleanup.enter_context(StagingArea(staging_parent)).root
        if workers == 1:
            results = run_one_at_a_time(csv_files, run, display)
        else:
            results = run_in_parallel(csv_files, run, workers, display)
    display.finish()

    for outcome in results:
        if outcome.get("staging_problem"):
            print(f"  could not copy {outcome['file'].name} locally, read it in place "
                  f"({outcome['staging_problem']})")
    return results


def document_directory(directory_to_scan, output_directory, settings, workers=1,
                       checkpoint_directory=None, checkpoint_every=CHECKPOINT_EVERY_ROWS,
                       stage_locally=False, staging_parent=None, skip_existing=False,
                       enrich=False, enrich_profile=None, aidrin_command="aidrin"):
    """Document every CSV in a directory, write the index; return (results, other_files)."""
    output_directory.mkdir(parents=True, exist_ok=True)
    csv_files, other_files = find_files(directory_to_scan)
    want_croissant = not settings.get("skip_croissant")
    to_read, skipped = csv_files, []
    if skip_existing:
        to_read, skipped = split_off_done(csv_files, output_directory, want_croissant)

    workers = worker_count(workers, len(to_read)) if to_read else 1
    announce(directory_to_scan, to_read, skipped, other_files, settings, workers,
             checkpoint_directory,
             staging_location(staging_parent) if stage_locally else None, skip_existing)

    run = Run(output_directory, settings, checkpoint_directory, checkpoint_every)
    results = read_files(to_read, run, workers, staging_parent, stage_locally) if to_read else []

    if skipped:
        # Merge in previous-run entries, back in directory order.
        by_file = {outcome["file"]: outcome for outcome in results}
        for csv_file in skipped:
            by_file[csv_file] = outcome_from_previous_run(output_directory, csv_file,
                                                          want_croissant)
        results = [by_file[csv_file] for csv_file in csv_files if csv_file in by_file]

    index_path = output_directory / INDEX_FILENAME
    index_path.write_text(build_index(directory_to_scan, results, other_files, settings),
                          encoding="utf-8")

    failed = [outcome for outcome in results if not outcome["ok"]]
    # Checkpoints are kept after a failure, so the next run carries on.
    if checkpoint_directory and not failed:
        CheckpointStore(checkpoint_directory).clear()

    enrich_summary = None
    if enrich:
        # After the index: the pass is done; enrichment reads the CSVs, not the reports.
        print("  enriching with AIDRIN...")
        enrich_summary = enrich_documented_files(results, output_directory,
                                                 enrich_profile, aidrin_command)

    print()
    read = len(results) - len(failed) - len(skipped)
    if skipped:
        print(f"  {read} CSV file(s) read, {len(skipped)} skipped as already documented")
    else:
        print(f"  {read} of {len(results)} CSV file(s) described")
    if failed:
        print(f"  {len(failed)} could not be read; see the index")
    if enrich_summary:
        print(f"  {enrich_summary['attached']} file(s) enriched with AIDRIN results"
              + (f", {enrich_summary['skipped_files']} left alone"
                 if enrich_summary["skipped_files"] else ""))
        for problem in enrich_summary["problems"]:
            print(f"  enrich: {problem}")
    print(f"\nWrote {index_path}")
    print(f"      and {len(results)} report(s) in {output_directory}")
    return results, other_files


def main():
    """Parse the command line and document the directory."""
    parser = make_parser(__doc__.splitlines()[0])
    parser.add_argument("directory", nargs="?", default=".", type=directory,
                        help="directory to document (default: the current one)")
    parser.add_argument("--out", default=DEFAULT_OUTPUT_DIRECTORY,
                        help=f"where reports go (default: ./{DEFAULT_OUTPUT_DIRECTORY})")
    parser.add_argument("--preview", nargs="?", type=int, const=DEFAULT_PREVIEW_ROWS,
                        default=DEFAULT_PREVIEW_ROWS, metavar="N",
                        help=f"read the first N rows of each file (default {DEFAULT_PREVIEW_ROWS})")
    parser.add_argument("--full", action="store_true", help="read every row of every file")
    add_workers(parser)
    add_separator(parser)
    parser.add_argument("--batch-size", type=int, default=None, metavar="N",
                        help="rows read at a time per worker (default: from file width); "
                             "lower it if runs are killed")
    parser.add_argument("--values-tracked", type=int, default=DEFAULT_VALUES_TRACKED)
    parser.add_argument("--values-shown", type=int, default=DEFAULT_VALUES_SHOWN)
    parser.add_argument("--no-identifier-check", action="store_true",
                        help="skip the un-anonymised data check")
    parser.add_argument("--no-croissant", action="store_true",
                        help="skip the par-baked Croissant and its Markdown")
    parser.add_argument("--no-checkpoints", action="store_true",
                        help="do not save progress; a killed run starts over")
    parser.add_argument("--checkpoint-every", type=int, default=CHECKPOINT_EVERY_ROWS,
                        metavar="N",
                        help=f"rows between progress saves (default {CHECKPOINT_EVERY_ROWS:,})")
    parser.add_argument("--checkpoint-dir", metavar="DIR", default=None,
                        help=f"where progress is saved (default: <out>/{CHECKPOINT_DIRECTORY_NAME})")
    parser.add_argument("--skip-existing", action="store_true",
                        help="leave alone files whose output is already in --out; "
                             "checks only that the output exists")
    add_staging(parser)
    add_enrich(parser)
    arguments = parser.parse_args()

    preview_rows = None if arguments.full else arguments.preview
    settings = {
        "separator": arguments.separator,
        "preview_rows": preview_rows,
        "batch_size": arguments.batch_size,
        "values_tracked": arguments.values_tracked,
        "values_shown": arguments.values_shown,
        "skip_identifier_check": arguments.no_identifier_check,
        "skip_croissant": arguments.no_croissant,
        "scope_description": ("every row of every file" if preview_rows is None
                              else f"the first {preview_rows:,} rows of each file"),
    }
    output_directory = Path(arguments.out)
    checkpoint_directory = None
    if not arguments.no_checkpoints:
        checkpoint_directory = Path(arguments.checkpoint_dir
                                    or output_directory / CHECKPOINT_DIRECTORY_NAME)

    document_directory(arguments.directory, output_directory, settings,
                       workers=arguments.workers,
                       checkpoint_directory=checkpoint_directory,
                       checkpoint_every=arguments.checkpoint_every,
                       stage_locally=arguments.batch_local_copies,
                       staging_parent=arguments.local_copy_dir,
                       skip_existing=arguments.skip_existing,
                       enrich=arguments.enrich,
                       enrich_profile=arguments.enrich_profile,
                       aidrin_command=arguments.aidrin)


if __name__ == "__main__":
    main()
