"""parbake/tests/test_enrich.py - tests for --enrich (enrich.py)

Run: pytest tests/test_enrich.py

The aidrin binary is replaced by a stub script that reads the batch config and
writes results, so these tests say nothing about AIDRIN itself -- only about
what parbake asks of it and what it does with the answer.

Authors: Melanie Cornelius, GitHub Copilot
"""

import json
from pathlib import Path

import pytest

from enrich import GROUPS, attach, enrich_documented_files, expand, write_configs
from parbakery import document_directory
from settings import DEFAULT_VALUES_SHOWN, DEFAULT_VALUES_TRACKED, ENRICH_SUBDIRECTORY


def make_settings():
    """Return run settings with defaults for every key."""
    return {
        "separator": ",",
        "preview_rows": None,
        "batch_size": None,
        "values_tracked": DEFAULT_VALUES_TRACKED,
        "values_shown": DEFAULT_VALUES_SHOWN,
        "skip_identifier_check": False,
        "skip_croissant": False,
        "scope_description": "every row of every file",
    }


# A profile that names every role, so every metric in GROUPS can run.
FULL_ROLES = {
    "required_columns": ["age"],
    "duplicate_columns": ["job"],
    "threshold": 0.9,
    "timestamp_column": "when",
    "frequency": "D",
    "batch_column": "machine",
    "target_columns": ["temp"],
    "path_targets": ["log"],
    "base_dir": "/data",
    "unit_metadata_file": "jobs.units.json",
    "correlation_columns": ["age", "temp"],
    "target_column": "job",
    "categorical_columns": ["job"],
    "numerical_columns": ["age"],
    "distance_metric": "jensen_shannon",
    "sensitive_attribute_column": "sex",
    "representation_columns": ["sex"],
    "quasi_identifiers": ["age", "sex"],
    "sensitive_column": "job",
    "id_column": "row_id",
    "risk_columns": ["age"],
    "hipaa_columns": ["job"],
    "dp_columns": ["age"],
    "epsilon": 1.0,
}


VERSION = 'if sys.argv[1:] == ["--version"]:\n    print("aidrin test-stub"); raise SystemExit(0)'

READ_CONFIG = """import json, sys
from pathlib import Path
""" + VERSION + """
config = json.loads(Path(sys.argv[2]).read_text())
out = Path(sys.argv[sys.argv.index("-o") + 1])
"""

AIDRIN_SUCCEEDS = READ_CONFIG + """
results = {m: {"value": 1} for m in config["metrics"]}
out.write_text(json.dumps(results))
"""

AIDRIN_FAILS_METRIC = READ_CONFIG + """
results = {m: {"value": 1} for m in config["metrics"]}
results[config["metrics"][0]] = {"Error": "a named column is missing"}
out.write_text(json.dumps(results))
"""

AIDRIN_WRITES_NOISY = READ_CONFIG + """
results = {m: {"value": 1} for m in config["metrics"]}
out.write_text(json.dumps(results))
if "differential-privacy" in config["metrics"]:
    Path("noisy").mkdir(exist_ok=True)
    Path("noisy/noisy_data.csv").write_text("age\\n1\\n")
"""

AIDRIN_WRITES_NOTHING = VERSION + """
raise SystemExit(1)
"""


def make_aidrin(tmp_path, name, body):
    """Return the path to a stub aidrin that runs body."""
    script = tmp_path / name
    script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def make_profile(tmp_path, roles):
    """Return the path to a JSON role profile -- JSON so the tests need no PyYAML."""
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"roles": roles}), encoding="utf-8")
    return str(profile)


@pytest.fixture
def one_file(tmp_path):
    """Return a directory with one CSV whose columns the roles above can name."""
    source = tmp_path / "data"
    source.mkdir()
    rows = "".join(f"{row},op,{row % 7},2026-01-0{row % 9 + 1},m{row % 2},{row * 3}\n"
                   for row in range(1, 9))
    (source / "jobs.csv").write_text(
        "age,job,sex,when,machine,temp" + "\n" + rows, encoding="utf-8")
    return source


# --- which metrics a profile can run ---

def test_a_full_profile_runs_every_group_and_every_metric():
    groups = expand(FULL_ROLES)
    assert {name for name, _, _ in groups} == set(GROUPS)
    ran = {metric for _, metrics, _ in groups for metric in metrics}
    everything = {m for group in GROUPS.values() for m in group["metrics"]}
    assert ran == everything


def test_no_profile_at_all_still_runs_the_zero_argument_metrics():
    groups = expand({})
    assert {name for name, _, _ in groups} == {"quality_baseline", "quality_focused",
                                               "data_structure"}
    for name, metrics, _keys in groups:
        if name == "quality_focused":
            assert metrics == ["feature-coverage-ratio"], \
                "only the optional-threshold metric may run with no roles"
        if name == "data_structure":
            assert len(metrics) == 4, "the whole structure group takes no arguments"


def test_a_groups_list_trims_the_run():
    groups = expand(FULL_ROLES, ["quality_baseline", "fairness"])
    assert {name for name, _, _ in groups} == {"quality_baseline", "fairness"}


def test_a_metric_missing_its_role_skips_only_that_metric():
    roles = dict(FULL_ROLES)
    del roles["target_column"]
    for name, metrics, _keys in expand(roles):
        if name == "fairness":
            assert metrics == ["representation-rate"]
        if name == "impact":
            assert metrics == ["correlations"]


def test_the_columns_that_disagree_stay_in_separate_groups():
    """The whole reason groups exist: correlations and representation-rate want
    different columns under the same key, so one config cannot hold both."""
    keys = {name: set(k) for name, _m, k in expand(FULL_ROLES)}
    assert "columns" in keys["impact"] and "columns" in keys["fairness"]
    for name, metrics, config_keys in expand(FULL_ROLES):
        assert "columns" not in config_keys or name in ("impact", "fairness",
                                                        "governance_hipaa", "privacy_dp")
    assert keys["data_structure"] == set(), "the structure group keys nothing"


# --- the configs written for aidrin ---

def test_every_config_names_the_file_and_stays_quiet(tmp_path):
    groups = expand(FULL_ROLES)
    written = write_configs(tmp_path / "scratch", Path("/data/jobs.csv"), groups)
    for name, (_path, metrics) in written.items():
        config = json.loads(_path.read_text(encoding="utf-8"))
        assert config["file-path"] == "/data/jobs.csv"
        assert config["metrics"] == metrics
        assert config["save-images"] is False, "batch must not litter /tmp with PNGs"
    assert set(written) == {name for name, _, _ in groups}


# --- running the pass with --enrich ---

def test_enrich_adds_a_block_to_every_croissant(one_file, tmp_path):
    output = tmp_path / "out"
    results, _ = document_directory(
        one_file, output, make_settings(), workers=1, enrich=True,
        enrich_profile=make_profile(tmp_path, {"target_column": "job"}),
        aidrin_command=make_aidrin(tmp_path, "ok", AIDRIN_SUCCEEDS))

    document = json.loads(Path(results[0]["croissant_path"]).read_text(encoding="utf-8"))
    block = document["_enrich"]
    assert block["banner"].startswith("ENRICHED")
    assert block["warning"], "the block must tell readers the roles went unchecked"
    assert block["aidrin_version"] == "aidrin test-stub"
    assert block["source_file"].endswith("jobs.csv")
    for group in block["groups"].values():
        assert group["status"] == "ok"
        assert (output / ENRICH_SUBDIRECTORY / "jobs" / group["results_file"]).exists()


def test_a_metric_that_errors_is_a_status_not_a_silence(one_file, tmp_path):
    output = tmp_path / "out"
    results, _ = document_directory(
        one_file, output, make_settings(), workers=1, enrich=True,
        enrich_profile=make_profile(tmp_path, FULL_ROLES),
        aidrin_command=make_aidrin(tmp_path, "err", AIDRIN_FAILS_METRIC))

    document = json.loads(Path(results[0]["croissant_path"]).read_text(encoding="utf-8"))
    groups = document["_enrich"]["groups"]
    reported = {name for name, g in groups.items() if "metric_errors" in g}
    assert reported, "AIDRIN exits 0 on failure; the run must still notice"
    for name in reported:
        assert "reported errors" in groups[name]["status"]


def test_the_differential_privacy_side_file_stays_in_scratch(one_file, tmp_path):
    output = tmp_path / "out"
    document_directory(
        one_file, output, make_settings(), workers=1, enrich=True,
        enrich_profile=make_profile(tmp_path, FULL_ROLES),
        aidrin_command=make_aidrin(tmp_path, "dp", AIDRIN_WRITES_NOISY))

    scratch = output / ENRICH_SUBDIRECTORY / "jobs"
    assert (scratch / "noisy" / "noisy_data.csv").exists()
    assert not (output / "noisy").exists(), "the noisy copy belongs in scratch, not the output"


def test_a_missing_aidrin_costs_the_numbers_not_the_run(one_file, tmp_path):
    output = tmp_path / "out"
    results, _ = document_directory(
        one_file, output, make_settings(), workers=1, enrich=True,
        enrich_profile=make_profile(tmp_path, {"target_column": "job"}),
        aidrin_command=str(tmp_path / "there-is-no-aidrin-here"))

    document = json.loads(Path(results[0]["croissant_path"]).read_text(encoding="utf-8"))
    assert results[0]["ok"] is True
    for group in document["_enrich"]["groups"].values():
        assert group["status"] == "aidrin not found"


def test_an_aidrin_that_writes_nothing_reports_its_exit(one_file, tmp_path):
    output = tmp_path / "out"
    results, _ = document_directory(
        one_file, output, make_settings(), workers=1, enrich=True,
        enrich_profile=make_profile(tmp_path, {"target_column": "job"}),
        aidrin_command=make_aidrin(tmp_path, "quiet", AIDRIN_WRITES_NOTHING))

    document = json.loads(Path(results[0]["croissant_path"]).read_text(encoding="utf-8"))
    for group in document["_enrich"]["groups"].values():
        assert group["status"].startswith("no result written")


def test_a_broken_profile_is_a_problem_not_a_crash(one_file, tmp_path):
    outcomes = [{"ok": True, "file": one_file / "jobs.csv",
                 "croissant_path": tmp_path / "nowhere.json"}]
    summary = enrich_documented_files(outcomes, tmp_path / "out",
                                      str(tmp_path / "missing.json"), "aidrin")
    assert summary["attached"] == 0
    assert any("missing.json" in problem for problem in summary["problems"])


def test_a_profile_that_selects_nothing_leaves_only_a_note(one_file, tmp_path):
    output = tmp_path / "out"
    profile = tmp_path / "empty.json"
    profile.write_text(json.dumps({"roles": {}, "groups": ["a_group_that_does_not_exist"]}),
                       encoding="utf-8")
    results, _ = document_directory(
        one_file, output, make_settings(), workers=1, enrich=True,
        enrich_profile=str(profile),
        aidrin_command=make_aidrin(tmp_path, "ok2", AIDRIN_SUCCEEDS))

    document = json.loads(Path(results[0]["croissant_path"]).read_text(encoding="utf-8"))
    block = document["_enrich"]
    assert block["groups"] == {}
    assert "note" in block
    assert not (output / ENRICH_SUBDIRECTORY).exists(), \
        "nothing to run means no scratch directory either"


# --- the block and --skip-existing ---

def test_a_skipped_file_reports_that_it_was_enriched(one_file, tmp_path):
    output = tmp_path / "out"
    document_directory(
        one_file, output, make_settings(), workers=1, enrich=True,
        enrich_profile=make_profile(tmp_path, {"target_column": "job"}),
        aidrin_command=make_aidrin(tmp_path, "ok3", AIDRIN_SUCCEEDS))

    results, _ = document_directory(one_file, output, make_settings(), workers=1,
                                    skip_existing=True)
    assert results[0]["skipped"] is True
    assert results[0]["enriched"] is True, "the block lives in the Croissant; a skip must see it"


def test_a_file_never_enriched_says_so(one_file, tmp_path):
    output = tmp_path / "out"
    document_directory(one_file, output, make_settings(), workers=1)
    results, _ = document_directory(one_file, output, make_settings(), workers=1,
                                    skip_existing=True)
    assert results[0]["enriched"] is False


def test_attaching_to_a_lost_croissant_fails_quietly(tmp_path):
    assert attach(tmp_path / "gone.json", {"x": 1}) is False
