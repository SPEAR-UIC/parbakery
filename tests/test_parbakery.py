"""parbake/tests/test_parbakery.py - tests for documenting a directory (parbakery.py)

Run: pytest tests/test_parbakery.py

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import pytest

from parbakery import describe_one_csv, document_directory
from reporting import build_index
from settings import (
    CROISSANT_SUBDIRECTORY,
    DEFAULT_PREVIEW_ROWS,
    DEFAULT_VALUES_SHOWN,
    DEFAULT_VALUES_TRACKED,
    INDEX_FILENAME,
    MARKDOWN_SUBDIRECTORY,
    TEXT_SUBDIRECTORY,
)
from sources import find_files
from workers import worker_count


def make_settings(preview_rows=DEFAULT_PREVIEW_ROWS, skip_identifier_check=False):
    """Return run settings with defaults for every key."""
    return {
        "separator": ",",
        "preview_rows": preview_rows,
        "batch_size": None,          # derived from the file width
        "values_tracked": DEFAULT_VALUES_TRACKED,
        "values_shown": DEFAULT_VALUES_SHOWN,
        "skip_identifier_check": skip_identifier_check,
        "scope_description": "the first 1,000 rows of each file",
    }


@pytest.fixture
def example_directory(tmp_path):
    """Return a directory of three CSVs, two other files and a subdirectory."""
    source = tmp_path / "data"
    source.mkdir()
    (source / "jobs.csv").write_text("username,exit_code\nchulwoo,0\nazamatm,1\n")
    (source / "telemetry.csv").write_text("node,reading\nx3008,1.5\nx3009,2.5\n")
    (source / "UPPERCASE.CSV").write_text("m,n\n5,6\n")
    (source / "notes.txt").write_text("not a csv\n")
    (source / "diagram.pdf").write_text("also not a csv\n")
    (source / "subfolder").mkdir()
    return source


@pytest.fixture
def output_directory(tmp_path):
    """Return an output path that does not exist yet."""
    return tmp_path / "out"


# --- find_files ---

def test_finds_every_csv(example_directory):
    csv_files, _ = find_files(example_directory)
    assert len(csv_files) == 3


def test_extension_match_ignores_case(example_directory):
    csv_files, _ = find_files(example_directory)
    assert "UPPERCASE.CSV" in [path.name for path in csv_files]


def test_non_csv_files_are_kept_separately(example_directory):
    _, other_files = find_files(example_directory)
    assert sorted(path.name for path in other_files) == ["diagram.pdf", "notes.txt"]


def test_subdirectories_are_ignored(example_directory):
    csv_files, other_files = find_files(example_directory)
    assert "subfolder" not in [path.name for path in csv_files + other_files]


def test_results_are_sorted_so_runs_are_repeatable(example_directory):
    csv_files, other_files = find_files(example_directory)
    assert [p.name for p in csv_files] == sorted(p.name for p in csv_files)
    assert [p.name for p in other_files] == sorted(p.name for p in other_files)


# --- describe_one_csv ---

def test_writes_a_report_for_one_csv(example_directory, output_directory):
    output_directory.mkdir()
    outcome = describe_one_csv(
        example_directory / "jobs.csv", output_directory, make_settings()
    )

    assert outcome["ok"] is True
    assert outcome["rows_read"] == 2
    assert outcome["column_count"] == 2
    assert outcome["report_path"].exists()
    assert "username" in outcome["report_path"].read_text()


def test_the_report_is_named_after_the_csv(example_directory, output_directory):
    output_directory.mkdir()
    outcome = describe_one_csv(
        example_directory / "jobs.csv", output_directory, make_settings()
    )
    assert outcome["report_path"].name == "jobs.txt"


def test_identifier_concerns_are_carried_up_to_the_index(example_directory, output_directory):
    output_directory.mkdir()
    outcome = describe_one_csv(
        example_directory / "jobs.csv", output_directory, make_settings()
    )
    assert "username" in outcome["columns_to_check"]


def test_the_identifier_check_can_be_skipped(example_directory, output_directory):
    output_directory.mkdir()
    outcome = describe_one_csv(
        example_directory / "jobs.csv", output_directory,
        make_settings(skip_identifier_check=True),
    )
    assert outcome["columns_to_check"] == []


# --- unreadable files ---

@pytest.fixture
def directory_with_a_broken_file(tmp_path):
    """Return a directory with one good CSV and one with an unterminated quote."""
    source = tmp_path / "data"
    source.mkdir()
    (source / "good.csv").write_text("a,b\n1,2\n")
    (source / "broken.csv").write_text('a,b,c\n"unterminated,2,3\n4,5,6\n')
    return source


def test_an_unreadable_file_does_not_stop_the_others(
    directory_with_a_broken_file, output_directory
):
    results, _ = document_directory(
        directory_with_a_broken_file, output_directory, make_settings()
    )
    by_name = {outcome["file"].name: outcome for outcome in results}

    assert by_name["broken.csv"]["ok"] is False
    assert by_name["good.csv"]["ok"] is True


def test_the_reason_a_file_failed_is_written_down(
    directory_with_a_broken_file, output_directory
):
    results, _ = document_directory(
        directory_with_a_broken_file, output_directory, make_settings()
    )
    broken = next(o for o in results if o["file"].name == "broken.csv")

    assert "ParserError" in broken["problem"]
    assert "Could not read" in broken["report_path"].read_text()


def test_a_failure_is_visible_in_the_index(directory_with_a_broken_file, output_directory):
    document_directory(directory_with_a_broken_file, output_directory, make_settings())
    index = (output_directory / INDEX_FILENAME).read_text()
    assert "COULD NOT READ" in index


# --- document_directory ---

def test_writes_the_index_and_one_report_per_csv(example_directory, output_directory):
    results, other_files = document_directory(
        example_directory, output_directory, make_settings()
    )

    assert (output_directory / INDEX_FILENAME).exists()
    assert len(results) == 3
    assert len(other_files) == 2
    for outcome in results:
        assert outcome["report_path"].exists()


def test_the_output_directory_is_created_if_missing(example_directory, tmp_path):
    target = tmp_path / "does" / "not" / "exist"
    document_directory(example_directory, target, make_settings())
    assert (target / INDEX_FILENAME).exists()


def test_every_file_appears_in_the_index(example_directory, output_directory):
    document_directory(example_directory, output_directory, make_settings())
    index = (output_directory / INDEX_FILENAME).read_text()

    for name in ["jobs.csv", "telemetry.csv", "UPPERCASE.CSV", "notes.txt", "diagram.pdf"]:
        assert name in index


def test_the_index_states_both_counts(example_directory, output_directory):
    document_directory(example_directory, output_directory, make_settings())
    index = (output_directory / INDEX_FILENAME).read_text()

    assert "# CSV files (3)" in index
    assert "# Other files, not examined (2)" in index


def test_the_index_records_what_was_read(example_directory, output_directory):
    """A preview and a full scan give different numbers."""
    document_directory(example_directory, output_directory, make_settings())
    index = (output_directory / INDEX_FILENAME).read_text()
    assert "the first 1,000 rows of each file" in index


def test_an_empty_directory_is_reported_not_an_error(tmp_path, output_directory):
    empty = tmp_path / "empty"
    empty.mkdir()
    results, other_files = document_directory(empty, output_directory, make_settings())

    assert results == []
    assert other_files == []
    assert "(none)" in (output_directory / INDEX_FILENAME).read_text()


def test_a_full_scan_reads_everything(example_directory, output_directory):
    settings = make_settings(preview_rows=None)
    settings["scope_description"] = "every row of every file"
    results, _ = document_directory(example_directory, output_directory, settings)

    jobs = next(o for o in results if o["file"].name == "jobs.csv")
    assert jobs["scope"] == "full scan"


def test_the_index_can_be_built_without_touching_the_disk(example_directory):
    csv_files, other_files = find_files(example_directory)
    text = build_index(example_directory, [], other_files, make_settings())
    assert "# CSV files (0)" in text


# --- empty and constant columns in the index ---

@pytest.fixture
def directory_with_dull_columns(tmp_path):
    """Return a directory with one CSV: a varied, a constant and a blank column."""
    source = tmp_path / "data"
    source.mkdir()
    (source / "dull.csv").write_text(
        "varied,constant,blank\n" + "\n".join(f"{n},SAME," for n in range(10)) + "\n"
    )
    return source


def test_empty_and_constant_columns_are_carried_up(directory_with_dull_columns, output_directory):
    output_directory.mkdir()
    outcome = describe_one_csv(
        directory_with_dull_columns / "dull.csv", output_directory, make_settings()
    )
    assert outcome["empty_columns"] == ["blank"]
    assert outcome["constant_columns"] == ["constant"]


def test_they_appear_in_the_index(directory_with_dull_columns, output_directory):
    document_directory(directory_with_dull_columns, output_directory, make_settings())
    index = (output_directory / INDEX_FILENAME).read_text()

    assert "empty in every row (1): blank" in index
    assert "one value covers 99%+ of rows (1): constant" in index


def test_a_file_with_nothing_dull_says_nothing(example_directory, output_directory):
    """telemetry.csv has two varied columns."""
    output_directory.mkdir()
    outcome = describe_one_csv(
        example_directory / "telemetry.csv", output_directory, make_settings()
    )
    assert outcome["empty_columns"] == []
    assert outcome["constant_columns"] == []


# --- parallel workers ---

def test_worker_count_never_exceeds_the_number_of_files():
    """An idle worker still costs a Python process with pandas loaded."""
    assert worker_count(8, file_count=3) == 3
    assert worker_count(8, file_count=20) == 8


def test_worker_count_is_at_least_one():
    assert worker_count(0, file_count=5) == 1
    assert worker_count(-4, file_count=5) == 1


def test_more_workers_than_cpus_is_allowed_with_a_warning(monkeypatch, capsys):
    monkeypatch.setattr("workers.os.cpu_count", lambda: 4)
    assert worker_count(16, file_count=100) == 16
    assert "16 workers on 4 CPUs" in capsys.readouterr().err
    assert worker_count(4, file_count=100) == 4
    assert worker_count(16, file_count=3) == 3
    assert capsys.readouterr().err == ""


def test_worker_count_defaults_to_something_sensible():
    assert worker_count(None, file_count=100) >= 1


def test_running_in_parallel_gives_the_same_answers(example_directory, tmp_path):
    sequential_out = tmp_path / "sequential"
    parallel_out = tmp_path / "parallel"

    sequential, _ = document_directory(
        example_directory, sequential_out, make_settings(), workers=1)
    parallel, _ = document_directory(
        example_directory, parallel_out, make_settings(), workers=3)

    assert len(sequential) == len(parallel)
    for one, other in zip(sequential, parallel):
        assert one["file"].name == other["file"].name
        assert one["rows_read"] == other["rows_read"]
        assert one["column_count"] == other["column_count"]
        assert one["columns_to_check"] == other["columns_to_check"]
        assert one["empty_columns"] == other["empty_columns"]
        assert one["constant_columns"] == other["constant_columns"]


def test_results_come_back_in_the_order_the_files_were_listed(example_directory, tmp_path):
    """Workers finish in any order."""
    results, _ = document_directory(
        example_directory, tmp_path / "out", make_settings(), workers=3)
    names = [outcome["file"].name for outcome in results]
    assert names == sorted(names)


def test_one_bad_file_does_not_stop_the_others_in_parallel(
    directory_with_a_broken_file, tmp_path
):
    results, _ = document_directory(
        directory_with_a_broken_file, tmp_path / "out", make_settings(), workers=2)
    by_name = {outcome["file"].name: outcome for outcome in results}

    assert by_name["broken.csv"]["ok"] is False
    assert by_name["good.csv"]["ok"] is True


# --- par-baked croissants and output folders ---

def test_croissants_go_in_their_own_subdirectory(example_directory, output_directory):
    """Kept apart so an unreviewed file is not mistaken for a finished one."""

    document_directory(example_directory, output_directory, make_settings())
    croissants = output_directory / CROISSANT_SUBDIRECTORY

    assert croissants.is_dir()
    assert sorted(p.name for p in croissants.glob("*.json")) == [
        "UPPERCASE.parbaked.json", "jobs.parbaked.json", "telemetry.parbaked.json"
    ]


def test_each_croissant_has_a_markdown_in_the_markdown_folder(
    example_directory, output_directory
):

    document_directory(example_directory, output_directory, make_settings())
    croissants = output_directory / CROISSANT_SUBDIRECTORY
    markdown = output_directory / MARKDOWN_SUBDIRECTORY

    json_files = sorted(croissants.glob("*.parbaked.json"))
    assert json_files
    for json_file in json_files:
        assert (markdown / f"{json_file.name[:-len('.json')]}.md").exists()


def test_output_is_sorted_into_three_folders(example_directory, output_directory):
    from settings import OUTPUT_SUBDIRECTORIES

    document_directory(example_directory, output_directory, make_settings())

    for name in OUTPUT_SUBDIRECTORIES:
        assert (output_directory / name).is_dir(), f"{name} was not created"


def test_each_folder_holds_only_its_own_kind(example_directory, output_directory):

    document_directory(example_directory, output_directory, make_settings())

    for folder, suffix in (
        (CROISSANT_SUBDIRECTORY, ".json"),
        (MARKDOWN_SUBDIRECTORY, ".md"),
        (TEXT_SUBDIRECTORY, ".txt"),
    ):
        written = list((output_directory / folder).iterdir())
        assert written, f"{folder} is empty"
        assert all(path.suffix == suffix for path in written), \
            f"{folder} holds something other than {suffix}"


def test_the_index_stays_at_the_top_level(example_directory, output_directory):
    from settings import OUTPUT_SUBDIRECTORIES

    document_directory(example_directory, output_directory, make_settings())

    assert (output_directory / INDEX_FILENAME).is_file()
    for name in OUTPUT_SUBDIRECTORIES:
        assert not (output_directory / name / INDEX_FILENAME).exists()


def test_the_index_says_which_folder_each_file_is_in(example_directory, output_directory):

    document_directory(example_directory, output_directory, make_settings())
    index = (output_directory / INDEX_FILENAME).read_text()

    for folder in (CROISSANT_SUBDIRECTORY, MARKDOWN_SUBDIRECTORY, TEXT_SUBDIRECTORY):
        assert f"{folder}/" in index


def test_a_failed_file_still_gets_its_report_in_the_text_folder(
    directory_with_a_broken_file, output_directory
):
    """Case: the file failed before any folder was made for it."""

    results, _ = document_directory(
        directory_with_a_broken_file, output_directory, make_settings())
    broken = next(o for o in results if o["file"].name == "broken.csv")

    assert broken["report_path"].parent.name == TEXT_SUBDIRECTORY
    assert broken["report_path"].is_file()


def test_the_index_points_at_the_croissant(example_directory, output_directory):
    document_directory(example_directory, output_directory, make_settings())
    index = (output_directory / INDEX_FILENAME).read_text()

    assert "jobs.parbaked.json" in index
    assert "NOT REVIEWED" in index


def test_nothing_is_ever_named_croissant_json(example_directory, output_directory):
    """That name is reserved for a file a person has finished."""
    document_directory(example_directory, output_directory, make_settings())
    assert list(output_directory.rglob("*.croissant.json")) == []


def test_croissants_can_be_skipped(example_directory, output_directory):

    settings = make_settings()
    settings["skip_croissant"] = True
    results, _ = document_directory(example_directory, output_directory, settings)

    assert not (output_directory / CROISSANT_SUBDIRECTORY).exists()
    assert all(outcome["croissant_path"] is None for outcome in results)


def test_a_croissant_failure_does_not_lose_the_report(example_directory, output_directory,
                                                      monkeypatch):
    """The text report is written before the croissant."""
    import parbakery as module

    def explode(*args, **kwargs):
        """Raise RuntimeError."""
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr(module, "write_parbaked_croissant", explode)
    output_directory.mkdir()
    outcome = describe_one_csv(
        example_directory / "jobs.csv", output_directory, make_settings()
    )

    assert outcome["ok"] is True
    assert outcome["report_path"].exists()
    assert "renderer unavailable" in outcome["croissant_problem"]
