"""parbake/enrich.py - Enrich: AIDRIN readiness measurements added to par-baked Croissants

Runs at the end of a --enrich pass: for every documented CSV, run AIDRIN's
readiness metrics and add an "_enrich" block to its Croissant, beside
"_parbake_measurements". The block travels with the file, so --skip-existing
can still see what was enriched.

parbake never imports AIDRIN: it pulls in Flask, Celery, and a newer Python
than parbake's 3.8-and-pandas floor. All contact is through the aidrin CLI.

An AIDRIN batch config is flat: every key applies to every metric listed.
Metrics disagree on what "columns" means, so one config cannot serve them all,
and a profile expands into one config per GROUPS entry. A metric runs only
when the profile names every role it needs; a missing role skips that metric,
not the group.

AIDRIN reports a failed metric inside its JSON and still exits 0, so results
are read back; an exit code proves nothing.

Authors: Melanie Cornelius, GitHub Copilot
"""

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

from settings import (ENRICH_SUBDIRECTORY, ENRICH_TIMEOUT_SECONDS,
                      VERSION_TIMEOUT_SECONDS)
from sources import dataset_stem

ENRICH_KEY = "_enrich"
ENRICH_BANNER = ("ENRICHED. AIDRIN READINESS MEASUREMENTS, MACHINE GENERATED, "
                 "NOT REVIEWED BY A HUMAN.")
ENRICH_WARNING = (
    "These are measurements over the source CSV, not judgements. The column "
    "roles came from a profile nobody reviewed: wrong quasi-identifiers or "
    "sensitive columns produce falsely reassuring privacy results. Confirm the "
    "roles before repeating any number from this block."
)

# --- the argument groups -----------------------------------------------------
# Each metric maps AIDRIN config keys to profile roles: "needs" must all be
# present for the metric to run, "optional" are added when present. Groups
# exist because metrics in one config must agree on every key they share,
# and "columns" never agrees.

GROUPS = {
    "quality_baseline": {
        "metrics": {
            "completeness": {},
            "duplicity": {},
            "outliers": {},
        },
    },
    "quality_focused": {
        "metrics": {
            "row-level-completeness": {"needs": {"required-columns": "required_columns"}},
            "duplicity-by-features": {"needs": {"duplicate-columns": "duplicate_columns"}},
            "feature-coverage-ratio": {"needs": {}, "optional": {"threshold": "threshold"}},
            "temporal-completeness": {"needs": {"timestamp-column": "timestamp_column"},
                                      "optional": {"frequency": "frequency"}},
            "null-count-trend": {"needs": {"batch-column": "batch_column"},
                                 "optional": {"target-columns": "target_columns"}},
            "file-reference-validation": {"needs": {"path-targets": "path_targets"},
                                          "optional": {"base-dir": "base_dir"}},
            "variable-unit-validation": {"needs": {"unit-metadata-file": "unit_metadata_file"}},
        },
    },
    # AIDRIN's data-structure category: four metrics that take no argument at
    # all, so they need no role, run on every file, and share one config
    # because there is no key for them to disagree about.
    "data_structure": {
        "metrics": {
            "constant-feature-count": {},
            "max-pairwise-correlation": {},
            "skewness": {},
            "kurtosis": {},
        },
    },
    "impact": {
        "metrics": {
            "correlations": {"needs": {"columns": "correlation_columns"}},
            "feature-relevance": {"needs": {"target-column": "target_column"},
                                  "optional": {"categorical-columns": "categorical_columns",
                                               "numerical-columns": "numerical_columns"}},
        },
    },
    "fairness": {
        "metrics": {
            "class-imbalance": {"needs": {"target-column": "target_column"},
                                "optional": {"distance-metric": "distance_metric"}},
            "statistical-rates": {"needs": {"y-true-column": "target_column",
                                            "sensitive-attribute-column": "sensitive_attribute_column"}},
            "representation-rate": {"needs": {"columns": "representation_columns"}},
        },
    },
    "governance_anonymity": {
        "metrics": {
            "k-anonymity": {"needs": {"quasi-identifiers": "quasi_identifiers"}},
            "entropy-risk": {"needs": {"quasi-identifiers": "quasi_identifiers"}},
            "l-diversity": {"needs": {"quasi-identifiers": "quasi_identifiers",
                                      "sensitive-column": "sensitive_column"}},
            "t-closeness": {"needs": {"quasi-identifiers": "quasi_identifiers",
                                      "sensitive-column": "sensitive_column"}},
        },
    },
    "governance_risk": {
        "metrics": {
            "single-attribute-risk": {"needs": {"id-column": "id_column",
                                                "eval-columns": "risk_columns"}},
            "multiple-attribute-risk": {"needs": {"id-column": "id_column",
                                                  "eval-columns": "risk_columns"}},
        },
    },
    "governance_hipaa": {
        "metrics": {
            "hipaa-compliance": {"needs": {"columns": "hipaa_columns"}},
        },
    },
    "privacy_dp": {
        # differential-privacy writes ./noisy/noisy_data.csv beside its cwd;
        # every group of a file runs in that file's scratch directory instead.
        "metrics": {
            "differential-privacy": {"needs": {"columns": "dp_columns", "epsilon": "epsilon"}},
        },
    },
}


def is_empty(value):
    """Return whether a profile role is unset."""
    return value is None or value == "" or value == []


def load_profile(path):
    """Return the parsed profile: a dict with at least a "roles" mapping.

    None means no profile. .yaml needs PyYAML, which parbake does not require;
    .json always works.
    """
    if path is None:
        return {"roles": {}, "groups": None}
    text = Path(path).read_text(encoding="utf-8")
    if Path(path).suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            raise SystemExit("an .yaml enrich profile needs PyYAML; install it in the "
                             "environment running parbake, or write the profile as .json")
        profile = yaml.safe_load(text)
    else:
        profile = json.loads(text)
    if not isinstance(profile, dict):
        raise SystemExit(f"enrich profile {path} is not a mapping")
    profile.setdefault("roles", {})
    profile.setdefault("groups", None)
    return profile


def expand(roles, wanted=None):
    """Return [(group, [metrics], {config keys})] this profile can run.

    wanted: group names to consider, so a profile can trim. A group with no
    runnable metric does not appear.
    """
    chosen = []
    for name, group in GROUPS.items():
        if wanted and name not in wanted:
            continue
        metrics, keys = [], {}
        for metric, rules in group["metrics"].items():
            needs = rules.get("needs", {})
            if any(is_empty(roles.get(role)) for role in needs.values()):
                continue
            keys.update({key: roles[role] for key, role in needs.items()})
            for key, role in rules.get("optional", {}).items():
                if not is_empty(roles.get(role)):
                    keys[key] = roles[role]
            metrics.append(metric)
        if metrics:
            chosen.append((name, metrics, keys))
    return chosen


def aidrin_version(command):
    """Return the aidrin --version text, or None. Never raises."""
    try:
        finished = subprocess.run([command, "--version"], capture_output=True,
                                  text=True, timeout=VERSION_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    if finished.returncode != 0:
        return None
    return (finished.stdout or finished.stderr).strip() or None


def write_configs(config_directory, csv_path, groups):
    """Write one flat batch config per group; return {group: (path, metrics)}.

    JSON, not YAML: people write profiles, machines write these.
    """
    config_directory.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, metrics, keys in groups:
        config = {"file-path": str(csv_path), "metrics": metrics,
                  "save-images": False, **keys}
        path = config_directory / f"{name}.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
        written[name] = (path, metrics)
    return written


def read_results(result_path):
    """Return the parsed AIDRIN JSON for one group, or None."""
    try:
        return json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_group(command, config_path, result_path, work_directory):
    """Run one batch config; return (status, {metric: error}) for the failures.

    work_directory is the scratch directory: side files AIDRIN writes on its
    own, such as the DP noisy CSV, never land beside the reports.
    """
    command_line = [command, "batch", str(config_path), "-o", str(result_path)]
    try:
        finished = subprocess.run(command_line, capture_output=True, text=True,
                                  cwd=work_directory, timeout=ENRICH_TIMEOUT_SECONDS)
    except FileNotFoundError:
        return "aidrin not found", {}
    except subprocess.TimeoutExpired:
        return f"timed out after {ENRICH_TIMEOUT_SECONDS} s", {}
    except OSError as problem:
        return f"could not run: {problem}", {}

    results = read_results(result_path)
    if results is None:
        last = (finished.stderr or finished.stdout or "").strip().splitlines()
        detail = f": {last[-1][:200]}" if last else ""
        return f"no result written (exit {finished.returncode}{detail})", {}

    # A failed metric is an "Error" key beside the successes, not a bad exit.
    errors = {metric: {"error": str(payload["Error"])[:300]}
              for metric, payload in results.items()
              if isinstance(payload, dict) and "Error" in payload}
    if finished.returncode == 0 and not errors:
        return "ok", errors
    return (f"{len(errors)} metric(s) reported errors "
            f"(exit {finished.returncode})", errors)


def run_one_file(csv_path, output_directory, profile, command):
    """Return the _enrich block for one CSV.

    A metric that cannot run becomes a status string; the caller guards the
    exceptions that are left.
    """
    roles = profile.get("roles", {})
    groups = expand(roles, profile.get("groups"))
    block = {
        "warning": ENRICH_WARNING,
        "banner": ENRICH_BANNER,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "aidrin_version": aidrin_version(command),
        "source_file": str(csv_path),
        "roles_used": roles,
        "groups": {},
    }
    if not groups:
        block["note"] = ("the profile named no roles a metric could use; "
                         "only this note is here")
        return block

    scratch = output_directory / ENRICH_SUBDIRECTORY / dataset_stem(Path(csv_path).name)
    configs = write_configs(scratch, csv_path, groups)
    for name, metrics, _keys in groups:
        config_path, _ = configs[name]
        result_path = scratch / f"{name}.results.json"
        status, errors = run_group(command, config_path, result_path, scratch)
        record = {"metrics": metrics, "status": status,
                  "results_file": result_path.name}
        if errors:
            record["metric_errors"] = errors
        block["groups"][name] = record
    return block


def attach(croissant_path, block):
    """Add the _enrich block to a par-baked Croissant, in place. Return success."""
    try:
        document = json.loads(croissant_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    document[ENRICH_KEY] = block
    with croissant_path.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
    return True


def profile_digest(path):
    """Return a short hash of the profile, so a block says what made it."""
    if path is None:
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def enrich_documented_files(outcomes, output_directory, profile_path, command):
    """Enrich every documented CSV in outcomes (fresh or skipped); return a summary.

    An outcome without a croissant_path -- a failure, or --no-croissant -- is
    left alone. A broken enrichment costs numbers, never the finished pass.
    """
    summary = {"ran": 0, "attached": 0, "skipped_files": 0, "problems": []}
    try:
        profile = load_profile(profile_path)
    except (Exception, SystemExit) as bad_profile:
        # SystemExit is how load_profile reports a missing PyYAML.
        summary["problems"].append(f"enrich profile: {bad_profile}")
        return summary
    for outcome in outcomes:
        croissant_path = outcome.get("croissant_path")
        if not outcome.get("ok") or not croissant_path:
            summary["skipped_files"] += 1
            continue
        csv_path = Path(outcome["file"])
        try:
            block = run_one_file(csv_path, output_directory, profile, command)
        except Exception as problem:
            summary["problems"].append(f"{csv_path.name}: {type(problem).__name__}"
                                       f": {problem}")
            continue
        block["profile"] = (str(profile_path) if profile_path else None)
        block["profile_digest"] = profile_digest(profile_path)
        if attach(Path(croissant_path), block):
            summary["attached"] += 1
        else:
            summary["problems"].append(f"no Croissant to attach to: {csv_path.name}")
        summary["ran"] += 1
    return summary