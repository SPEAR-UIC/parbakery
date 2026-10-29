"""parbake/tests/test_checkpoints.py - tests for checkpoints and resuming (checkpoints.py)

Run: pytest tests/test_checkpoints.py

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import json
import time

import pytest

from checkpoints import (
    CHECKPOINT_FORMAT,
    CheckpointStore,
    fingerprint_settings,
)
from describe_csv import describe_csv
from identifiers import IdentifierCheck
from measuring import ColumnSummary

SETTINGS = fingerprint_settings(",", 1000, None)


@pytest.fixture
def csv_file(tmp_path):
    """Return a 500-row CSV with repeating values."""
    path = tmp_path / "jobs.csv"
    path.write_text("username,exit_code\n" +
                    "".join(f"user{n % 7},{n % 3}\n" for n in range(500)))
    return path


@pytest.fixture
def store(tmp_path):
    """Return an enabled CheckpointStore that saves every 100 rows."""
    return CheckpointStore(tmp_path / "checkpoints", enabled=True, every_rows=100)


def summaries_for(names):
    """Return an empty ColumnSummary for each name."""
    return {name: ColumnSummary(name, i) for i, name in enumerate(names)}


# --- saving ---

def test_a_checkpoint_is_written_where_it_says(store, csv_file):
    written = store.save("jobs", csv_file, SETTINGS, 100, summaries_for(["a"]))
    assert written == store.path_for("jobs")
    assert written.is_file()


def test_the_write_is_atomic(store, csv_file):
    """A half-written checkpoint would be trusted by the next run."""
    store.save("jobs", csv_file, SETTINGS, 100, summaries_for(["a"]))

    leftovers = list(store.directory.glob("*.writing"))
    assert leftovers == []


def test_nothing_is_written_when_checkpointing_is_off(tmp_path, csv_file):
    off = CheckpointStore(tmp_path / "cp", enabled=False)
    assert off.save("jobs", csv_file, SETTINGS, 100, summaries_for(["a"])) is None
    assert not (tmp_path / "cp").exists()


def test_a_checkpoint_is_only_due_after_enough_rows(store):
    assert store.is_due(rows_done=99, rows_at_last_save=0) is False
    assert store.is_due(rows_done=100, rows_at_last_save=0) is True
    assert store.is_due(rows_done=150, rows_at_last_save=100) is False


def test_a_small_file_never_reaches_a_checkpoint(tmp_path, csv_file):
    """So no file size threshold is needed."""
    big_interval = CheckpointStore(tmp_path / "cp", enabled=True, every_rows=1_000_000)
    describe_csv(csv_file, checkpoints=big_interval)
    assert big_interval.outstanding() == []


# --- refusing a checkpoint ---

def test_a_missing_checkpoint_is_not_an_error(store, csv_file):
    found, reason = store.load("jobs", csv_file, SETTINGS)
    assert found is None
    assert reason == "no checkpoint"


def test_a_checkpoint_for_a_changed_file_is_refused(store, csv_file):
    store.save("jobs", csv_file, SETTINGS, 100, summaries_for(["a"]))

    time.sleep(0.01)
    csv_file.write_text("username,exit_code\nsomeone,0\n")

    found, reason = store.load("jobs", csv_file, SETTINGS)
    assert found is None
    assert "changed" in reason


def test_a_checkpoint_from_different_settings_is_refused(store, csv_file):
    store.save("jobs", csv_file, SETTINGS, 100, summaries_for(["a"]))

    other = fingerprint_settings(";", 1000, None)
    found, reason = store.load("jobs", csv_file, other)
    assert found is None
    assert "settings" in reason


def test_batch_size_is_deliberately_not_part_of_the_fingerprint():
    """Lowering batch size is the usual fix after a kill; resuming must still work."""
    assert set(SETTINGS) == {"separator", "values_tracked", "preview_rows"}


def test_a_checkpoint_from_another_version_is_refused(store, csv_file):
    store.save("jobs", csv_file, SETTINGS, 100, summaries_for(["a"]))

    path = store.path_for("jobs")
    document = json.loads(path.read_text())
    document["parbake_checkpoint"] = CHECKPOINT_FORMAT + 1
    path.write_text(json.dumps(document))

    found, reason = store.load("jobs", csv_file, SETTINGS)
    assert found is None
    assert "version" in reason


def test_a_corrupt_checkpoint_is_refused_rather_than_crashing(store, csv_file):
    store.save("jobs", csv_file, SETTINGS, 100, summaries_for(["a"]))
    store.path_for("jobs").write_text("{ this is not json")

    found, reason = store.load("jobs", csv_file, SETTINGS)
    assert found is None
    assert "unreadable" in reason


def test_a_checkpoint_with_no_progress_is_refused(store, csv_file):
    store.save("jobs", csv_file, SETTINGS, 0, summaries_for(["a"]))
    found, _ = store.load("jobs", csv_file, SETTINGS)
    assert found is None


# --- removing checkpoints ---

def test_a_finished_file_loses_its_checkpoint(store, csv_file):
    store.save("jobs", csv_file, SETTINGS, 100, summaries_for(["a"]))
    store.discard("jobs")
    assert not store.path_for("jobs").exists()


def test_a_finished_run_removes_the_whole_directory(store, csv_file):
    """A checkpoint for a dataset no longer in the input must not survive."""
    store.save("one", csv_file, SETTINGS, 100, summaries_for(["a"]))
    store.save("two", csv_file, SETTINGS, 100, summaries_for(["a"]))

    assert store.clear() is True
    assert not store.directory.exists()


def test_clearing_never_touches_files_it_did_not_write(store, csv_file):
    """--checkpoint-dir can point at a shared directory."""
    store.save("one", csv_file, SETTINGS, 100, summaries_for(["a"]))
    stranger = store.directory / "somebody_elses_notes.txt"
    stranger.write_text("mine")

    assert store.clear() is False
    assert stranger.is_file()
    assert stranger.read_text() == "mine"
    assert not store.path_for("one").exists()


# --- resuming ---

def test_an_interrupted_read_resumes_to_an_identical_result(tmp_path):
    """Includes a column over the value cap, where a merge-based scheme fails."""
    path = tmp_path / "big.csv"
    path.write_text(
        "name,code,unique\n" +
        "".join(f"user{n % 11},{n % 5},row{n}\n" for n in range(5_000))
    )

    straight = describe_csv(path, identifier_check=IdentifierCheck())

    store = CheckpointStore(tmp_path / "cp", enabled=True, every_rows=500)

    class Interrupted(Exception):
        """Raised to simulate a kill."""
        pass

    def stop_once_saved(rows, seconds):
        """Raise Interrupted once a checkpoint exists."""
        if store.outstanding():
            raise Interrupted()

    with pytest.raises(Interrupted):
        describe_csv(path, identifier_check=IdentifierCheck(),
                     checkpoints=store, on_progress=stop_once_saved)

    assert store.outstanding(), "nothing was checkpointed, so nothing is being tested"

    resumed = describe_csv(path, identifier_check=IdentifierCheck(), checkpoints=store)

    assert resumed["file"]["resumed_from_row"], "it did not actually resume"
    assert resumed["file"]["rows_read"] == straight["file"]["rows_read"]
    assert resumed["columns"] == straight["columns"]
    assert resumed["identifiers"] == straight["identifiers"]


def test_resuming_works_for_a_column_that_hit_the_value_cap(tmp_path):
    path = tmp_path / "wide.csv"
    path.write_text("unique\n" + "".join(f"value{n}\n" for n in range(3_000)))

    straight = describe_csv(path, values_tracked=100)
    assert straight["columns"]["unique"]["distinct_count_is_at_least"]

    store = CheckpointStore(tmp_path / "cp", enabled=True, every_rows=400)

    class Interrupted(Exception):
        """Raised to simulate a kill."""
        pass

    def stop_once_saved(rows, seconds):
        """Raise Interrupted once a checkpoint exists."""
        if store.outstanding():
            raise Interrupted()

    with pytest.raises(Interrupted):
        describe_csv(path, values_tracked=100, checkpoints=store,
                     on_progress=stop_once_saved)
    resumed = describe_csv(path, values_tracked=100, checkpoints=store)

    assert resumed["columns"] == straight["columns"]


def test_the_checkpoint_is_gone_once_the_file_finishes(tmp_path, csv_file):
    store = CheckpointStore(tmp_path / "cp", enabled=True, every_rows=100)
    describe_csv(csv_file, checkpoints=store)
    assert store.outstanding() == []
