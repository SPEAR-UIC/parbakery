"""parbake/previous_run.py - files a previous run already documented, for --skip-existing

Decided in the parent: a skipped file is never opened or copied.
Checks only that the outputs exist; a source edited since keeps its old description.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import json

from identifiers import names_worth_checking
from measuring import holds_one_value, is_all_empty, quiet_columns
from settings import CROISSANT_SUBDIRECTORY, MARKDOWN_SUBDIRECTORY, TEXT_SUBDIRECTORY
from sources import dataset_stem


def expected_outputs(output_directory, csv_file, want_croissant=True):
    """Return {"report", "croissant", "markdown": path} for a CSV's outputs."""
    stem = dataset_stem(csv_file)
    outputs = {"report": output_directory / TEXT_SUBDIRECTORY / f"{stem}.txt"}
    if want_croissant:
        outputs["croissant"] = output_directory / CROISSANT_SUBDIRECTORY / f"{stem}.parbaked.json"
        outputs["markdown"] = output_directory / MARKDOWN_SUBDIRECTORY / f"{stem}.parbaked.md"
    return outputs


def already_documented(output_directory, csv_file, want_croissant=True):
    """Return whether every output exists."""
    # A killed run can leave a report with no Croissant.
    outputs = expected_outputs(output_directory, csv_file, want_croissant)
    return all(path.exists() for path in outputs.values())


def split_off_done(csv_files, output_directory, want_croissant):
    """Return (still to read, already documented)."""
    to_read, done = [], []
    for csv_file in csv_files:
        if already_documented(output_directory, csv_file, want_croissant):
            done.append(csv_file)
        else:
            to_read.append(csv_file)
    return to_read, done


def outcome_from_previous_run(output_directory, csv_file, want_croissant=True):
    """Return a skipped file's index entry, read back from its par-baked Croissant."""
    outputs = expected_outputs(output_directory, csv_file, want_croissant)
    outcome = {
        "file": csv_file,
        "ok": True,
        "skipped": True,
        "report_path": outputs["report"],
        "croissant_path": outputs.get("croissant"),
        "markdown_path": outputs.get("markdown"),
        "croissant_problem": None,
        "seconds": 0.0,
    }
    document = read_croissant(outputs.get("croissant"))
    findings = findings_from(document) if document else nothing_known()
    return {**outcome, **findings}


def read_croissant(path):
    """Return the parsed Croissant, or None if missing or unreadable."""
    # A truncated or hand-edited Croissant costs a detailed entry, not the run.
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def nothing_known():
    """Return findings for a file whose Croissant gives nothing."""
    # Checks are "unknown", not "none": an empty list would read as clean.
    return {
        "rows_read": 0,
        "column_count": 0,
        "scope": "not re-read; see the existing report",
        "columns_to_check": [],
        "check_results_unknown": True,
        "empty_columns": [],
        "constant_columns": [],
        "enriched": False,
    }


def findings_from(document):
    """Return the index findings recorded in a par-baked Croissant."""
    provenance = document.get("_parbake", {})
    measurements = document.get("_parbake_measurements", {})
    if not isinstance(measurements, dict) or not measurements:
        return nothing_known()
    enriched = isinstance(document.get("_enrich"), dict)

    # Recomputed from counts: older Croissants lack the flags.
    flags = {}
    for name, column in measurements.items():
        rows_seen = column.get("rows_seen", 0)
        commonest = (column.get("most_common_values") or [{}])[0].get("count", 0)
        flags[name] = {
            "is_all_empty": is_all_empty(rows_seen, column.get("empty_count", 0)),
            "holds_one_value": holds_one_value(rows_seen, commonest),
        }
    empty, constant = quiet_columns(flags)

    return {
        "rows_read": provenance.get("rows_read", 0),
        "column_count": len(measurements),
        "scope": provenance.get("scope", "unknown"),
        "columns_to_check": names_worth_checking(provenance.get("columns_of_concern", [])),
        # Croissants older than the anonymity flags are unknown, not clean.
        "check_results_unknown": "columns_of_concern" not in provenance,
        "empty_columns": empty,
        "constant_columns": constant,
        # A skipped file keeps its enrichment: the block is inside the Croissant.
        "enriched": enriched,
    }
