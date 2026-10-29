"""parbake/tests/test_staging.py - tests for local copies (staging.py)

Run: pytest tests/test_staging.py

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import pytest

from settings import DEFAULT_PREVIEW_ROWS, DEFAULT_VALUES_SHOWN, DEFAULT_VALUES_TRACKED
from parbakery import document_directory
from staging import StagingArea, staged_copy


def make_settings(preview_rows=DEFAULT_PREVIEW_ROWS):
    """Return run settings with defaults for every key."""
    return {
        "separator": ",",
        "preview_rows": preview_rows,
        "batch_size": None,
        "values_tracked": DEFAULT_VALUES_TRACKED,
        "values_shown": DEFAULT_VALUES_SHOWN,
        "skip_identifier_check": False,
        "skip_croissant": True,
        "scope_description": "a test",
    }


@pytest.fixture
def six_files(tmp_path):
    """Return a directory of six small CSVs, more than the workers used."""
    source = tmp_path / "data"
    source.mkdir()
    for number in range(6):
        (source / f"file_{number}.csv").write_text(
            "name,value\n" + "".join(f"row{row},{row}\n" for row in range(20)),
            encoding="utf-8",
        )
    return source


# --- StagingArea ---

def test_the_directory_is_made_and_then_removed(tmp_path):
    with StagingArea(tmp_path / "scratch") as staging:
        root = staging.root
        assert root.is_dir()
    assert not root.exists()


def test_the_directory_goes_away_even_after_a_failure(tmp_path):
    root = None
    with pytest.raises(ZeroDivisionError):
        with StagingArea(tmp_path / "scratch") as staging:
            root = staging.root
            raise ZeroDivisionError("something went wrong mid-run")
    assert not root.exists()


def test_the_parent_directory_is_made_if_it_is_not_there(tmp_path):
    scratch = tmp_path / "not" / "made" / "yet"
    with StagingArea(scratch) as staging:
        assert staging.root.is_dir()
    assert scratch.is_dir()


# --- staged_copy ---

def test_a_file_is_copied_in_and_removed_again(six_files, tmp_path):
    original = sorted(six_files.glob("*.csv"))[0]

    with StagingArea(tmp_path / "scratch") as staging:
        with staged_copy(staging.root, original) as (local, problem):
            assert problem is None
            assert local != original
            assert local.read_bytes() == original.read_bytes()
            assert len(list(staging.root.rglob("*.csv"))) == 1

        # Removed at block end, so a worker holds no copy while fetching the next.
        assert not local.exists()
        assert list(staging.root.rglob("*.csv")) == []


def test_a_copy_is_cleaned_up_even_if_reading_it_fails(six_files, tmp_path):
    original = sorted(six_files.glob("*.csv"))[0]
    with StagingArea(tmp_path / "scratch") as staging:
        with pytest.raises(ValueError):
            with staged_copy(staging.root, original):
                raise ValueError("the read blew up")
        assert list(staging.root.rglob("*.csv")) == []


def test_the_name_is_kept_exactly(six_files, tmp_path):
    """Reports and checkpoints are named after the file."""
    original = sorted(six_files.glob("*.csv"))[0]
    with StagingArea(tmp_path / "scratch") as staging:
        with staged_copy(staging.root, original) as (local, _problem):
            assert local.name == original.name


def test_two_files_with_the_same_name_do_not_collide(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    (first / "same.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (second / "same.csv").write_text("a,b\n9,9\n", encoding="utf-8")

    with StagingArea(tmp_path / "scratch") as staging:
        with staged_copy(staging.root, first / "same.csv") as (one, _):
            with staged_copy(staging.root, second / "same.csv") as (other, _):
                assert one != other
                assert one.read_text(encoding="utf-8") == "a,b\n1,2\n"
                assert other.read_text(encoding="utf-8") == "a,b\n9,9\n"


def test_a_file_that_cannot_be_copied_is_read_where_it_is(tmp_path):
    missing = tmp_path / "not_here.csv"
    with StagingArea(tmp_path / "scratch") as staging:
        with staged_copy(staging.root, missing) as (local, problem):
            assert local == missing
            assert isinstance(problem, Exception)


def test_size_and_modification_time_are_kept(tmp_path):
    """The checkpoint fingerprint is size and mtime."""
    source = tmp_path / "data"
    source.mkdir()
    original = source / "a.csv"
    original.write_text("a,b\n1,2\n", encoding="utf-8")

    with StagingArea(tmp_path / "scratch") as staging:
        with staged_copy(staging.root, original) as (local, _problem):
            assert local.stat().st_size == original.stat().st_size
            assert local.stat().st_mtime == pytest.approx(original.stat().st_mtime)


def test_no_more_copies_exist_than_workers_hold(tmp_path):
    """A copy cannot outlive its block, so the cap is one copy per worker."""
    source = tmp_path / "data"
    source.mkdir()
    files = []
    for number in range(4):
        path = source / f"f{number}.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        files.append(path)

    with StagingArea(tmp_path / "scratch") as staging:
        # Two workers holding one copy each: the most two workers can hold.
        with staged_copy(staging.root, files[0]), staged_copy(staging.root, files[1]):
            assert len(list(staging.root.rglob("*.csv"))) == 2
        assert list(staging.root.rglob("*.csv")) == []


# --- end to end ---

def test_staging_gives_the_same_answers_as_reading_in_place(six_files, tmp_path):
    plain, _ = document_directory(
        six_files, tmp_path / "plain", make_settings(), workers=2)
    staged, _ = document_directory(
        six_files, tmp_path / "staged", make_settings(), workers=2,
        stage_locally=True, staging_parent=tmp_path / "scratch")

    assert len(plain) == len(staged)
    for one, other in zip(plain, staged):
        assert one["file"] == other["file"]
        assert one["rows_read"] == other["rows_read"]
        assert one["column_count"] == other["column_count"]
        assert one["columns_to_check"] == other["columns_to_check"]
        assert one["empty_columns"] == other["empty_columns"]
        assert one["constant_columns"] == other["constant_columns"]


def test_staging_works_with_a_single_worker(six_files, tmp_path):
    """--workers 1 takes a separate code path."""
    plain, _ = document_directory(
        six_files, tmp_path / "plain", make_settings(), workers=1)
    staged, _ = document_directory(
        six_files, tmp_path / "staged", make_settings(), workers=1,
        stage_locally=True, staging_parent=tmp_path / "scratch")

    for one, other in zip(plain, staged):
        assert one["file"] == other["file"]
        assert one["rows_read"] == other["rows_read"]
        assert one["column_count"] == other["column_count"]


def test_the_output_names_the_original_file_not_the_copy(six_files, tmp_path):
    """The copy is deleted, so its path would name nothing."""
    output = tmp_path / "out"
    results, _ = document_directory(
        six_files, output, make_settings(), workers=2,
        stage_locally=True, staging_parent=tmp_path / "scratch")

    for outcome in results:
        assert outcome["file"].parent == six_files
        assert outcome["file"].exists()

    written = "\n".join(path.read_text(encoding="utf-8")
                        for path in output.rglob("*.txt"))
    assert "parbake_staging" not in written
    assert str(six_files) in written


def test_the_index_can_still_be_built_after_the_copies_are_gone(six_files, tmp_path):
    """The index stats every file after the copies are deleted."""
    output = tmp_path / "out"
    document_directory(six_files, output, make_settings(), workers=2,
                       stage_locally=True, staging_parent=tmp_path / "scratch")
    index = (output / "DIRECTORY_DOCUMENTATION.txt").read_text(encoding="utf-8")
    assert "file_0.csv" in index
    assert "parbake_staging" not in index


def test_nothing_is_left_behind_in_the_staging_parent(six_files, tmp_path):
    scratch = tmp_path / "scratch"
    document_directory(six_files, tmp_path / "out", make_settings(), workers=2,
                       stage_locally=True, staging_parent=scratch)
    assert list(scratch.iterdir()) == []


def test_a_worker_takes_its_next_file_without_waiting_for_the_others(tmp_path):
    """Batch copying would let at most one file finish before the slow one."""
    from io import StringIO

    from parbakery import Run, run_in_parallel
    from progress import ProgressDisplay

    source = tmp_path / "data"
    source.mkdir()
    slow = source / "slow.csv"
    slow.write_text("a,b\n" + "".join(f"{row},{row}\n" for row in range(300_000)),
                    encoding="utf-8")
    quick = []
    for number in range(5):
        path = source / f"quick_{number}.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        quick.append(path)

    csv_files = [slow] + quick
    display = ProgressDisplay([path.name for path in csv_files],
                              stream=StringIO(), show_resources=False)

    with StagingArea(tmp_path / "scratch") as staging:
        run = Run(tmp_path / "out", make_settings(preview_rows=None),
                  staging_root=staging.root)
        run_in_parallel(csv_files, run, workers=2, display=display)

    finished = sorted((progress.finished_at, progress.name)
                      for progress in display.files if progress.finished_at)
    order = [name for _when, name in finished]

    assert len(order) == 6
    before_the_slow_one = order.index("slow.csv")
    assert before_the_slow_one >= 3, (
        f"only {before_the_slow_one} file(s) finished while the slow one ran; "
        "a free worker is waiting instead of taking its next file")
