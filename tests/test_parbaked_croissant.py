"""parbake/tests/test_parbaked_croissant.py - tests for par-baked Croissants (parbaked_croissant.py)

Run: pytest tests/test_parbaked_croissant.py
Validation tests are skipped when mlcroissant is not installed.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from describe_csv import describe_csv
from parbaked_croissant import (
    BANNER,
    FIELD_PLACEHOLDER,
    OUTSTANDING,
    build_parbaked_croissant,
    render_parbaked_markdown,
    write_parbaked_croissant,
)

EXAMPLE_CSV = """\
username,exit_code,notes
chulwoo,0,ok
azamatm,143,retried
someone,0,
"""


@pytest.fixture
def described(tmp_path):
    """Return describe_csv output for EXAMPLE_CSV."""
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(EXAMPLE_CSV)
    return describe_csv(csv_path)


@pytest.fixture
def document(described):
    """Return a parbaked croissant built from described."""
    return build_parbaked_croissant(described)


# --- structure ---

def test_it_is_a_croissant_shaped_document(document):
    assert document["@type"] == "sc:Dataset"
    assert "@context" in document
    assert len(document["recordSet"]) == 1
    assert len(document["distribution"]) == 1


def test_every_column_becomes_a_field_in_order(document, described):
    names = [field["name"] for field in document["recordSet"][0]["field"]]
    assert names == list(described["columns"])


def test_fields_are_wired_to_the_file_and_the_column(document):
    field = document["recordSet"][0]["field"][0]
    assert field["source"]["extract"]["column"] == "username"
    assert field["source"]["fileObject"]["@id"] == document["distribution"][0]["@id"]


def test_the_file_records_only_what_was_measured(document, described):
    file_object = document["distribution"][0]
    assert file_object["contentSize"] == f"{described['file']['size_in_bytes']} B"
    assert file_object["encodingFormat"] == "text/csv"


# --- absent content ---

def test_no_field_is_given_a_data_type(document):
    """A type guessed from a sample is a judgement for a human."""
    for field in document["recordSet"][0]["field"]:
        assert "dataType" not in field


def test_no_field_is_given_a_real_description(document):
    for field in document["recordSet"][0]["field"]:
        assert field["description"] == FIELD_PLACEHOLDER


def test_it_claims_no_licence_citation_or_creator(document):
    """A person has to supply each of these."""
    for absent in ("license", "citeAs", "creator", "publisher", "datePublished", "url"):
        assert absent not in document


def test_it_makes_no_responsible_ai_claims(document):
    """"No known biases" would be a claim nobody has checked."""
    assert not [key for key in document if key.startswith("rai:")]


def test_measurements_are_kept_out_of_the_croissant_fields(document):
    """Measurements go in _parbake_measurements, not the schema."""
    assert "_parbake_measurements" in document
    for field in document["recordSet"][0]["field"]:
        assert set(field) <= {"@type", "@id", "name", "description", "source"}


def test_the_measurements_are_carried_across(document, described):
    measured = document["_parbake_measurements"]["exit_code"]
    assert measured["rows_seen"] == described["columns"]["exit_code"]["rows_seen"]
    assert measured["distinct_count"] == described["columns"]["exit_code"]["distinct_count"]


# --- unreviewed markers ---

def test_the_dataset_description_carries_the_banner(document):
    assert BANNER in document["description"]


def test_the_record_set_carries_the_banner(document):
    assert BANNER in document["recordSet"][0]["description"]


def test_the_file_object_carries_the_banner(document):
    assert BANNER in document["distribution"][0]["description"]


def test_the_provenance_block_says_it_is_unreviewed(document):
    provenance = document["_parbake"]
    assert "NOT REVIEWED" in provenance["status"]
    assert provenance["validates"] is False
    assert provenance["why_it_does_not_validate"]


def test_the_outstanding_work_travels_with_the_file(document):
    assert document["_parbake"]["outstanding_for_a_human"] == list(OUTSTANDING)


def test_it_records_whether_the_whole_file_was_read(tmp_path):
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(EXAMPLE_CSV)

    full = build_parbaked_croissant(describe_csv(csv_path))
    preview = build_parbaked_croissant(describe_csv(csv_path, preview_rows=1))

    assert full["_parbake"]["rows_read_is_the_whole_file"] is True
    assert preview["_parbake"]["rows_read_is_the_whole_file"] is False
    assert "preview" in preview["_parbake"]["scope"]


@pytest.mark.parametrize("warning_word", ["PAR-BAKED", "NOT REVIEWED", "DO NOT SUBMIT"])
def test_the_warning_appears_many_times_over(document, warning_word):
    assert json.dumps(document).count(warning_word) >= 3


# --- file name ---

def test_writing_uses_the_parbaked_name_not_the_finished_one(described, tmp_path):
    output = tmp_path / "out" / "jobs.parbaked.json"
    write_parbaked_croissant(described, output)

    assert output.exists()
    assert ".croissant.json" not in output.name


# --- validation (mlcroissant) ---

def _mlcroissant_errors(document):
    """Run the real validator and return its exit code and error lines."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(document, handle, indent=2)
        path = handle.name
    try:
        finished = subprocess.run(
            ["mlcroissant", "validate", "--jsonld", path],
            capture_output=True, text=True, timeout=180,
        )
    finally:
        Path(path).unlink(missing_ok=True)

    output = finished.stdout + finished.stderr
    errors, collecting = [], False
    for line in output.splitlines():
        if "error(s)" in line:
            collecting = True
            continue
        if "warning(s)" in line:
            collecting = False
        if collecting and line.strip().startswith("- "):
            errors.append(line.strip()[2:].strip())
    return finished.returncode, errors


needs_mlcroissant = pytest.mark.skipif(
    shutil.which("mlcroissant") is None,
    reason="mlcroissant is not installed, so validation cannot be checked",
)


@needs_mlcroissant
def test_the_generated_file_fails_validation(document):
    exit_code, _ = _mlcroissant_errors(document)
    assert exit_code != 0


@needs_mlcroissant
def test_it_fails_because_of_the_conforms_to_we_chose(document):
    """Not by accident of being malformed."""
    _, errors = _mlcroissant_errors(document)
    assert any("conformsTo" in error and "PARBAKED" in error for error in errors)


@needs_mlcroissant
def test_fixing_only_the_conforms_to_is_not_enough(document):
    """The data types are still missing."""
    document = json.loads(json.dumps(document))
    document["conformsTo"] = "http://mlcommons.org/croissant/1.0"

    exit_code, errors = _mlcroissant_errors(document)
    assert exit_code != 0
    assert any("dataType" in error for error in errors)


@needs_mlcroissant
def test_it_validates_once_the_outstanding_work_is_actually_done(document):
    """Case: a real conformsTo, a sha256 and a dataType on every field."""
    document = json.loads(json.dumps(document))
    document["conformsTo"] = "http://mlcommons.org/croissant/1.0"
    document["distribution"][0]["sha256"] = "a" * 64
    for field in document["recordSet"][0]["field"]:
        field["dataType"] = "sc:Text"

    exit_code, errors = _mlcroissant_errors(document)
    assert exit_code == 0, f"expected it to validate, got: {errors}"


# --- Markdown ---

def test_markdown_is_rendered_by_the_existing_renderer(document):
    markdown = render_parbaked_markdown(document)
    assert markdown.startswith("# ")
    assert "## Fields" in markdown


def test_the_markdown_repeats_the_warning(document):
    markdown = render_parbaked_markdown(document)
    assert markdown.count("PAR-BAKED") >= 2
    assert "DO NOT SUBMIT" in markdown


def test_the_markdown_shows_every_field_as_unwritten(document):
    markdown = render_parbaked_markdown(document)
    assert markdown.count(FIELD_PLACEHOLDER) == len(document["recordSet"][0]["field"])


def test_the_renderer_can_be_swapped_for_a_test(document):
    class FakeRenderer:
        """Render a heading and a fixed line."""
        @staticmethod
        def render_markdown(doc):
            """Return a heading and a fixed line."""
            return f"# {doc['name']}\nrendered"

    assert render_parbaked_markdown(document, renderer=FakeRenderer) == (
        "# jobs\nrendered")
