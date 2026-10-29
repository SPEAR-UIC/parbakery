"""parbake/measuring.py - per-column measurements, accumulated batch by batch

ColumnSummary: one column's counts, lengths, tracked values and number range.
Only the tracked-value table grows, up to values_tracked entries.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import numpy
import pandas

from settings import (
    DEFAULT_VALUES_SHOWN,
    DEFAULT_VALUES_TRACKED,
    NULL_LIKE_TEXT,
    SINGLE_VALUE_THRESHOLD,
)

# Saved whole by checkpoints: a resumed read continues the same totals.
STATE_FIELDS = (
    "rows_seen", "empty_count", "null_like_text_count", "value_counts",
    "stopped_tracking_new_values", "shortest_value_length", "longest_value_length",
    "number_count", "not_a_number_count", "smallest_number", "largest_number",
)


def is_all_empty(rows_seen, empty_count):
    """Return whether every row seen is empty."""
    return rows_seen > 0 and empty_count == rows_seen


def holds_one_value(rows_seen, commonest_count):
    """Return whether one value covers at least SINGLE_VALUE_THRESHOLD of all rows."""
    return rows_seen > 0 and commonest_count / rows_seen >= SINGLE_VALUE_THRESHOLD


def quiet_columns(columns):
    """Return (empty, constant) column names from as_dict() measurements by name."""
    empty = [name for name, column in columns.items() if column["is_all_empty"]]
    constant = [name for name, column in columns.items()
                if column["holds_one_value"] and not column["is_all_empty"]]
    return empty, constant


def widen(low, high, new_low, new_high):
    """Return the range (low, high) widened to include (new_low, new_high)."""
    if low is None:
        return new_low, new_high
    return min(low, new_low), max(high, new_high)


class ColumnSummary:
    """Measurements of one column, built by add_batch()."""

    def __init__(self, column_name, position, values_tracked=DEFAULT_VALUES_TRACKED):
        """Start with zero counts and no values."""
        self.column_name = column_name
        self.position = position
        self.values_tracked = values_tracked

        self.rows_seen = 0
        self.empty_count = 0
        self.null_like_text_count = 0
        # When full, known values still count; distinct_count becomes a floor.
        self.value_counts = {}
        self.stopped_tracking_new_values = False
        self.shortest_value_length = None
        self.longest_value_length = None
        # Kept when some values are not numbers; see not_a_number_count.
        self.number_count = 0
        self.not_a_number_count = 0
        self.smallest_number = None
        self.largest_number = None

    def add_batch(self, values):
        """Add one batch of text values to the measurements."""
        self.rows_seen += len(values)
        is_empty = values == ""
        self.empty_count += int(is_empty.sum())
        filled_in = values[~is_empty]
        if filled_in.empty:
            return

        self.null_like_text_count += int(filled_in.str.lower().isin(NULL_LIKE_TEXT).sum())

        lengths = filled_in.str.len()
        self.shortest_value_length, self.longest_value_length = widen(
            self.shortest_value_length, self.longest_value_length,
            int(lengths.min()), int(lengths.max()))

        for value, count in filled_in.value_counts().items():
            if value in self.value_counts:
                self.value_counts[value] += int(count)
            elif len(self.value_counts) < self.values_tracked:
                self.value_counts[value] = int(count)
            else:
                self.stopped_tracking_new_values = True

        # to_numeric reads "nan" and "inf" as floats; isfinite drops them.
        as_numbers = pandas.to_numeric(filled_in, errors="coerce")
        numbers = as_numbers[numpy.isfinite(as_numbers)]
        self.number_count += len(numbers)
        self.not_a_number_count += len(filled_in) - len(numbers)
        if not numbers.empty:
            self.smallest_number, self.largest_number = widen(
                self.smallest_number, self.largest_number,
                float(numbers.min()), float(numbers.max()))

    @property
    def filled_in_count(self):
        """Return the count of non-empty rows."""
        return self.rows_seen - self.empty_count

    @property
    def distinct_count(self):
        """Return the count of tracked distinct values."""
        return len(self.value_counts)

    @property
    def is_all_numbers(self):
        """Return whether every non-empty value is a number."""
        return self.filled_in_count > 0 and self.not_a_number_count == 0

    @property
    def is_all_empty(self):
        """Return whether every row is empty."""
        return is_all_empty(self.rows_seen, self.empty_count)

    @property
    def single_value_share(self):
        """Return the commonest value's share of all rows, blanks included."""
        if not self.rows_seen or not self.value_counts:
            return 0.0
        return self.most_common(1)[0][1] / self.rows_seen

    @property
    def holds_one_value(self):
        """Return whether one value covers at least SINGLE_VALUE_THRESHOLD of rows."""
        return self.single_value_share >= SINGLE_VALUE_THRESHOLD

    def most_common(self, how_many):
        """Return the how_many commonest (value, count) pairs, ties by value."""
        # Not Counter: it breaks ties by insertion order, which depends on batch order.
        return sorted(self.value_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:how_many]

    def share_covered_by(self, values_and_counts):
        """Return the share of filled-in rows that these (value, count) pairs cover."""
        if not self.filled_in_count:
            return 0.0
        return sum(count for _, count in values_and_counts) / self.filled_in_count

    def state(self):
        """Return the STATE_FIELDS as a dict, for a checkpoint."""
        state = {name: getattr(self, name) for name in STATE_FIELDS}
        state["value_counts"] = dict(self.value_counts)
        return state

    @classmethod
    def from_state(cls, column_name, position, state, values_tracked=DEFAULT_VALUES_TRACKED):
        """Return a ColumnSummary rebuilt from a state() dict."""
        summary = cls(column_name, position, values_tracked)
        for name in STATE_FIELDS:
            setattr(summary, name, state[name])
        summary.value_counts = dict(state["value_counts"])
        return summary

    def as_dict(self, values_shown=DEFAULT_VALUES_SHOWN):
        """Return the measurements as a plain dict, with values_shown common values."""
        summary = {
            "position": self.position,
            "rows_seen": self.rows_seen,
            "empty_count": self.empty_count,
            "null_like_text_count": self.null_like_text_count,
            "distinct_count": self.distinct_count,
            "distinct_count_is_at_least": self.stopped_tracking_new_values,
            "shortest_value_length": self.shortest_value_length,
            "longest_value_length": self.longest_value_length,
            "most_common_values": [{"value": value, "count": count}
                                   for value, count in self.most_common(values_shown)],
            "number_count": self.number_count,
            "not_a_number_count": self.not_a_number_count,
            "is_all_numbers": self.is_all_numbers,
            "is_all_empty": self.is_all_empty,
            "holds_one_value": self.holds_one_value,
            "single_value_share": round(self.single_value_share, 4),
        }
        if self.number_count:
            summary["smallest_number"] = self.smallest_number
            summary["largest_number"] = self.largest_number
        return summary
