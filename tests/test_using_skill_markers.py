"""parbake/tests/test_using_skill_markers.py - the markers the using-skill
looks for exist in the cards it is pointed at.

Skill: skills/using-alcf-croissants/SKILL.md
Run: pytest tests/test_using_skill_markers.py

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import json
from pathlib import Path

import pytest

from describe_csv import describe_csv
from parbaked_croissant import build_parbaked_croissant

SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / \
    "using-alcf-croissants" / "SKILL.md"

REVIEWED = Path(__file__).resolve().parents[2] / "croissant_files" / "djc_v4.croissant.json"


@pytest.fixture
def document(tmp_path):
    """Return a parbaked croissant built from a two-row CSV."""
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text("username,exit_code\nchulwoo,0\nazamatm,143\n")
    return build_parbaked_croissant(describe_csv(csv_path))


def test_the_skill_file_exists_and_has_frontmatter():
    text = SKILL_PATH.read_text()
    assert text.startswith("---\n")
    assert "name: using-alcf-croissants" in text
    assert "description:" in text


def test_the_skill_defers_the_review_gate_to_the_reading_skill():
    text = SKILL_PATH.read_text()
    assert "reading-croissant-datasets" in text


# --- markers the skill points at, in a reviewed card ---

@pytest.mark.skipif(not REVIEWED.is_file(), reason="no reviewed croissant to check")
def test_the_reviewed_card_carries_what_the_skill_reads():
    raw = REVIEWED.read_text()
    for marker in ("cr:isEnumeration", "citeAs", '"license"', '"version"',
                   "datePublished", "rai:dataLimitations",
                   "rai:personalSensitiveInformation", "dataType",
                   "PROVISIONAL", "Naming:", "GENID"):
        assert marker in raw, f"the skill promises {marker!r}, the card has none"


@pytest.mark.skipif(not REVIEWED.is_file(), reason="no reviewed croissant to check")
def test_the_enumeration_payload_is_readable_as_the_skill_says():
    document = json.loads(REVIEWED.read_text())
    enums = [r for r in document.get("recordSet", []) if r.get("cr:isEnumeration")]
    assert enums, "the skill teaches joining coded columns to a table"
    table = enums[0]
    coded = f"{table['name']}/code"
    assert table["key"] == {"@id": coded}
    assert all(coded in row for row in table["data"])


@pytest.mark.skipif(not REVIEWED.is_file(), reason="no reviewed croissant to check")
def test_the_fileset_names_files_the_way_the_skill_says():
    document = json.loads(REVIEWED.read_text())
    file_sets = [d for d in document.get("distribution", [])
                 if d.get("@type") == "cr:FileObject" or d.get("@type") == "cr:FileSet"]
    assert file_sets, "the skill tells you to read the distribution block"
    assert any("ANL-ALCF" in json.dumps(each) for each in file_sets)


# --- the par-baked case the using skill defers on ---

def test_a_parbaked_file_still_carries_the_reading_gate_markers(document):
    assert "PARBAKED" in document["conformsTo"]
    for field in document["recordSet"][0]["field"]:
        assert "NOT REVIEWED" in field["description"]


def test_a_parbaked_file_has_no_undocumented_guarantees(document):
    """The using skill's promise: a par-baked card grants no citation."""
    for key in ("citeAs", "license", "version", "rai:dataLimitations"):
        assert key not in document
