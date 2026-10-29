"""parbake/settings.py - tunable constants

NOTES.md: how the measured values were chosen.
Every module imports this first, so it holds the Python version check.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import sys

# Here so the message comes before anything fails confusingly.
if sys.version_info < (3, 8):
    raise SystemExit(
        "parbake needs Python 3.8 or newer; this is "
        f"{sys.version_info.major}.{sys.version_info.minor}.\n"
        "On a cluster there is usually a newer one: try\n"
        "    module avail python\n"
        "and load one, or run it directly: /path/to/python3.11 parbakery.py ..."
    )


# --- which files are read ---

# What pandas decompresses from the name. Not zip: picking an archive member is a judgement.
COMPRESSION_SUFFIXES = (".gz", ".bz2", ".xz")

# Longest first, so ".csv.gz" wins over ".csv".
CSV_SUFFIXES = tuple(sorted((".csv",) + tuple(f".csv{c}" for c in COMPRESSION_SUFFIXES),
                            key=len, reverse=True))

# Keep the text exactly as written: "NA" stays a word, a blank line is a row.
LITERAL_READ_SETTINGS = {
    "dtype": str,
    "keep_default_na": False,
    "na_filter": False,
    "skip_blank_lines": False,
}


# --- how much is read at a time ---

# Cells, not rows: memory is rows times columns.
CELL_BUDGET_PER_BATCH = 1_000_000
MINIMUM_BATCH_ROWS = 2_000
MAXIMUM_BATCH_ROWS = 100_000

DEFAULT_PREVIEW_ROWS = 1000

# Files up to this size are counted exactly rather than sampled.
EXACT_ROW_COUNT_LIMIT = 8 << 20


# --- what is measured ---

# Counted apart from blanks, compared without case.
NULL_LIKE_TEXT = frozenset({"na", "n/a", "null", "nan", "none"})

# Distinct values tracked per column. Past it the distinct count is a floor.
DEFAULT_VALUES_TRACKED = 1000

# Share of all rows, blanks included, one value must cover for a constant column.
# Below 1.0 to allow a few stray rows.
SINGLE_VALUE_THRESHOLD = 0.99


# --- what is shown ---

DEFAULT_VALUES_SHOWN = 10

# A value list covering less than this share of rows is not printed.
MINIMUM_LISTING_COVERAGE = 0.10


# --- un-anonymised data ---

# Matched case-insensitively anywhere in a column name.
# FIXME: "name" also flags MACHINE_NAME and QUEUE_NAME.
COLUMN_NAMES_TO_WATCH = (
    "user", "username", "login", "uid", "owner", "submitter",
    "email", "mail", "name", "project", "account", "person",
)

# Values peeked at per batch when asking whether a column holds paths.
PATH_PROBE_VALUES = 1000


# --- checkpoints ---

CHECKPOINT_EVERY_ROWS = 1_000_000
CHECKPOINT_DIRECTORY_NAME = ".parbake_checkpoints"


# --- running in parallel ---

DEFAULT_WORKER_LIMIT = 8
POLL_SECONDS = 0.05
STAGING_PREFIX = "parbake_staging_"


# --- output ---

DEFAULT_OUTPUT_DIRECTORY = "parbake_output"
INDEX_FILENAME = "DIRECTORY_DOCUMENTATION.txt"
CROISSANT_SUBDIRECTORY = "parbaked_croissants"     # .parbaked.json
MARKDOWN_SUBDIRECTORY = "parbaked_markdown"        # .parbaked.md
TEXT_SUBDIRECTORY = "parbaked_txt"                 # .txt reports
ENRICH_SUBDIRECTORY = "enrich"                     # AIDRIN configs + results, per CSV stem
OUTPUT_SUBDIRECTORIES = (CROISSANT_SUBDIRECTORY, MARKDOWN_SUBDIRECTORY, TEXT_SUBDIRECTORY)


# --- enrich (AIDRIN) ---

# Per batch-group subprocess: AIDRIN's own docs call the heavy governance
# metrics slow; a hung metric should cost one group's results, not the run.
ENRICH_TIMEOUT_SECONDS = 600
# For the --version probe alone. aidrin answers in a second or is not there.
VERSION_TIMEOUT_SECONDS = 60
DEFAULT_AIDRIN_COMMAND = "aidrin"
