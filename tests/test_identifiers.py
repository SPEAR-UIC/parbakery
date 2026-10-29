"""parbake/tests/test_identifiers.py - tests for the identifier check (identifiers.py)

Run: pytest tests/test_identifiers.py

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import pandas

from describe_csv import describe_csv
from identifiers import IdentifierCheck, watched_words


# --- which columns are checked ---

def test_flags_columns_whose_name_suggests_an_identifier():
    assert watched_words("username")
    assert watched_words("project_name")
    assert not watched_words("runtime_seconds")


def test_column_name_matching_ignores_case_and_looks_inside_the_name():
    """USERNAME_GENID is anonymised but still worth a human look."""
    assert watched_words("USERNAME_GENID")


def test_ordinary_column_names_are_not_flagged():
    for name in ("exit_code", "nodes_used", "walltime"):
        assert watched_words(name) == []


def test_columns_with_unremarkable_names_are_left_alone():
    check = IdentifierCheck()
    check.check_batch("runtime_seconds", pandas.Series(["1", "2", "3"]))
    assert check.columns_of_concern() == []


# --- all-digit share ---

def test_a_column_of_real_usernames_is_flagged():
    """ALCF usernames always contain letters."""
    check = IdentifierCheck()
    check.check_batch("username", pandas.Series(["chulwoo", "azamatm"]))

    concern = check.columns_of_concern()[0]
    assert concern["column_name"] == "username"
    assert "NOT consistent" in concern["verdict"]
    assert concern["all_digit_share"] == 0.0


def test_an_all_digit_column_reads_as_anonymised():
    """An anonymised id is a hash reduced to an integer."""
    check = IdentifierCheck()
    check.check_batch("username_id", pandas.Series(["1", "76", "30861277613258"]))

    concern = check.columns_of_concern()[0]
    assert concern["all_digit_share"] == 1.0
    assert "consistent with an anonymised id" in concern["verdict"]
    assert "NOT" not in concern["verdict"]


def test_one_real_username_among_the_ids_is_enough_to_flag_it():
    """Case: the anonymiser missed one row."""
    check = IdentifierCheck()
    check.check_batch("username_id", pandas.Series(["1", "2", "3", "chulwoo"]))

    concern = check.columns_of_concern()[0]
    assert concern["all_digit_share"] < 1.0
    assert "NOT consistent" in concern["verdict"]


def test_blank_values_are_ignored():
    check = IdentifierCheck()
    check.check_batch("username", pandas.Series(["", "", ""]))
    assert check.columns_of_concern() == []


def test_shares_are_counted_across_batches():
    check = IdentifierCheck()
    check.check_batch("username", pandas.Series(["1", "2"]))
    check.check_batch("username", pandas.Series(["chulwoo", "azamatm"]))

    assert check.columns_of_concern()[0]["all_digit_share"] == 0.5


# --- paths ---

def test_a_path_column_is_picked_up_even_with_a_dull_name():
    """"script" is not a watched word; the values are paths."""
    check = IdentifierCheck()
    check.check_batch("script", pandas.Series(["/home/chulwoo/run.py"]))

    concern = check.columns_of_concern()[0]
    assert concern["column_name"] == "script"
    assert concern["watched_by_name"] is False
    assert concern["path_like_share"] == 1.0


def test_paths_are_not_judged_by_their_digits():
    """A path always contains letters, so the all-digit share says nothing."""
    check = IdentifierCheck()
    check.check_batch("script", pandas.Series(["/home/chulwoo/run.py"]))
    assert "path" in check.columns_of_concern()[0]["verdict"]


def test_values_that_merely_mention_a_path_are_not_path_columns():
    check = IdentifierCheck()
    check.check_batch("notes", pandas.Series(["see /home/x for details"]))
    assert check.columns_of_concern() == []


# --- inside describe_csv ---

def test_the_check_runs_during_the_normal_read(tmp_path):
    csv_path = tmp_path / "leaky.csv"
    csv_path.write_text("username,queue\nchulwoo,debug\nsomebody,prod\n")

    result = describe_csv(csv_path, identifier_check=IdentifierCheck())
    report = result["identifiers"]

    assert report["checked"] is True
    flagged = {entry["column_name"] for entry in report["columns_of_concern"]}
    assert "username" in flagged


def test_the_check_is_skipped_when_not_asked_for(tmp_path):
    csv_path = tmp_path / "leaky.csv"
    csv_path.write_text("username,queue\nchulwoo,debug\n")

    report = describe_csv(csv_path)["identifiers"]
    assert report["checked"] is False
    assert report["columns_of_concern"] == []


def test_checking_does_not_change_the_measurements(tmp_path):
    csv_path = tmp_path / "leaky.csv"
    csv_path.write_text("username,queue\nchulwoo,debug\nsomebody,prod\n")

    without = describe_csv(csv_path)["columns"]
    with_check = describe_csv(csv_path, identifier_check=IdentifierCheck())["columns"]
    assert with_check == without


def test_batch_size_does_not_change_what_is_found(tmp_path):
    csv_path = tmp_path / "leaky.csv"
    csv_path.write_text("username\n" + "\n".join(["chulwoo", "1", "2", "azamatm"]) + "\n")

    results = []
    for batch_size in (1, 2, 1000):
        check = IdentifierCheck()
        describe_csv(csv_path, batch_size=batch_size, identifier_check=check)
        results.append(check.columns_of_concern())
    assert results[0] == results[1] == results[2]
