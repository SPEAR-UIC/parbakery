"""parbake/tests/test_skill_markers.py - tests for the markers SKILL.md looks for

Skill: skills/reading-croissant-datasets/SKILL.md
Run: pytest tests/test_skill_markers.py

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

from pathlib import Path

import pytest

from describe_csv import describe_csv
from parbaked_croissant import build_parbaked_croissant, render_parbaked_markdown

SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / \
    "reading-croissant-datasets" / "SKILL.md"


@pytest.fixture
def document(tmp_path):
    """Return a parbaked croissant built from a two-row CSV."""
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text("username,exit_code\nchulwoo,0\nazamatm,143\n")
    return build_parbaked_croissant(describe_csv(csv_path))


def test_the_skill_file_exists_and_has_frontmatter():
    text = SKILL_PATH.read_text()
    assert text.startswith("---\n")
    assert "name: reading-croissant-datasets" in text
    assert "description:" in text


# --- markers present in a parbaked file ---

def test_conforms_to_carries_the_parbaked_marker(document):
    assert "PARBAKED" in document["conformsTo"]


def test_the_parbake_key_exists(document):
    assert "_parbake" in document


def test_the_status_says_not_reviewed(document):
    assert "NOT REVIEWED BY A HUMAN" in document["_parbake"]["status"]


def test_field_descriptions_say_not_reviewed(document):
    for field in document["recordSet"][0]["field"]:
        assert "NOT REVIEWED" in field["description"]


def test_no_field_has_a_data_type(document):
    assert not any("dataType" in f for f in document["recordSet"][0]["field"])


def test_the_markdown_carries_the_marker(document):
    assert "PAR-BAKED" in render_parbaked_markdown(document)


def test_the_measurements_block_is_named_as_the_skill_expects(document):
    assert "_parbake_measurements" in document


@pytest.mark.parametrize("key", ["scope", "rows_read_is_the_whole_file",
                                 "outstanding_for_a_human"])
def test_the_skill_can_find_what_it_needs_to_qualify_an_answer(document, key):
    assert key in document["_parbake"]


# --- markers absent from a reviewed file ---

REVIEWED = Path(__file__).resolve().parents[2] / "croissant_files" / "djc_v4.croissant.json"


@pytest.mark.skipif(not REVIEWED.is_file(), reason="no reviewed croissant to compare against")
def test_the_markers_do_not_fire_on_a_reviewed_file():
    import json
    document = json.loads(REVIEWED.read_text())

    conforms = document.get("conformsTo")
    conforms = conforms if isinstance(conforms, list) else [conforms]

    assert not any("PARBAKED" in entry for entry in conforms)
    assert "_parbake" not in document
    assert "NOT REVIEWED" not in json.dumps(document)
