# The maximal AIDRIN batch configs, one per argument group.

These are what `--enrich` writes into `<out>/enrich/<stem>/<group>.json` for a
file, shown here as YAML so they can be read and edited. Run any of them by
hand against a CSV:

    aidrin batch 01_quality_baseline.yaml -o results.json

Why so many files instead of one: an AIDRIN batch config is FLAT. Every column
key in it applies to every metric listed, and a metric silently ignores keys it
does not use. That is fine for metrics that agree, and wrong for metrics that
want the SAME key for DIFFERENT columns:

  - `columns` means the correlated features to correlations, the categorical
    groups to representation-rate, the PHI-scan targets to hipaa-compliance,
    and the noisy numeric features to differential-privacy.
  - `target-column` means the prediction target to feature-relevance and the
    class column to class-imbalance (those agree, so they share a group only
    where the rest matches).

Put them in one config and whichever metric gets the wrong `columns` produces a
meaningless number. So "run everything" splits into nine configs, grouped by
the argument sets that can safely coexist. The zero-argument ones, 01 and 09,
run with no profile at all; 09 is AIDRIN's data-structure category kept apart
from 01's data-quality one only because AIDRIN files them separately.

Not covered here, on purpose:

  - FAIR compliance report and conditional-demographic-disparity: web interface
    only, no CLI/batch entry.
  - outliers-custom: its rules are not a HeadlessConfig field, so batch cannot
    carry them. Use `aidrin run outliers-custom <file> --rule '...'` or the
    Python `run_metric("outliers-custom", ..., rules_json=...)`.

Every config sets `save-images: false`; otherwise `aidrin batch` drops PNGs in
/tmp/aidrin_images for each visualization-producing metric.
