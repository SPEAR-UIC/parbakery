"""parbake/identifiers.py - check for un-anonymised data during the normal read

An ALCF username contains letters; an anonymised id is all digits.
Checked: columns whose name suggests an identity, or whose values look like paths.
Says where to look; never says a file is safe to share.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

from settings import COLUMN_NAMES_TO_WATCH, PATH_PROBE_VALUES

# Verdict text is stored in Croissants and matched by names_worth_checking.
HOLDS_PATHS = "holds paths -- a path can carry a username inside it"
ALL_DIGITS = "all digits -- consistent with an anonymised id"
HAS_LETTERS = "contains letters -- NOT consistent with an anonymised id"


def names_worth_checking(columns_of_concern):
    """Return the names of columns holding letters or paths; all-digit columns are not flagged."""
    return [entry["column_name"] for entry in columns_of_concern
            if "NOT consistent" in entry["verdict"] or "path" in entry["verdict"]]


def watched_words(column_name):
    """Return the COLUMN_NAMES_TO_WATCH words found in the column name."""
    lowered = column_name.lower()
    return [word for word in COLUMN_NAMES_TO_WATCH if word in lowered]


def looks_like_paths(values):
    """Return whether any of the first PATH_PROBE_VALUES values starts with "/"."""
    # Only a few values: this runs on every column of every batch.
    return bool(values.head(PATH_PROBE_VALUES).str.startswith("/").any())


class IdentifierCheck:
    """Per-column value shapes for watched or path-like columns."""

    def __init__(self):
        """Start with no columns seen."""
        self.shapes = {}

    def check_batch(self, column_name, values):
        """Count digit and path values in one batch, if the column is worth checking."""
        filled_in = values[values != ""]
        if filled_in.empty:
            return
        words = watched_words(column_name)
        if not words and not looks_like_paths(filled_in):
            return
        shape = self.shapes.setdefault(column_name, {
            "values_seen": 0,
            "all_digit_values": 0,
            "path_like_values": 0,
            "watched_by_name": bool(words),
            "matched_words": words,
            "example": filled_in.iloc[0],
        })
        shape["values_seen"] += len(filled_in)
        shape["all_digit_values"] += int(filled_in.str.isdigit().sum())
        shape["path_like_values"] += int(filled_in.str.startswith("/").sum())

    def state(self):
        """Return a copy of the shapes, for a checkpoint."""
        # Without this a resumed read misjudges the rows it skipped.
        return {name: dict(shape) for name, shape in self.shapes.items()}

    def restore(self, state):
        """Replace the shapes with a state() copy."""
        self.shapes = {name: dict(shape) for name, shape in state.items()}

    def columns_of_concern(self):
        """Return one entry per checked column, with shares, verdict and example."""
        results = []
        for column_name, shape in sorted(self.shapes.items()):
            seen = shape["values_seen"]
            digit_share = shape["all_digit_values"] / seen if seen else 0
            path_share = shape["path_like_values"] / seen if seen else 0
            if path_share > 0:
                verdict = HOLDS_PATHS
            elif seen and shape["all_digit_values"] == seen:
                verdict = ALL_DIGITS
            else:
                verdict = HAS_LETTERS
            results.append({
                "column_name": column_name,
                "watched_by_name": shape["watched_by_name"],
                "matched_words": shape["matched_words"],
                "all_digit_share": round(digit_share, 4),
                "path_like_share": round(path_share, 4),
                "verdict": verdict,
                "example": shape["example"],
            })
        return results
