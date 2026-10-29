"""parbake/reporting.py - printed text: one file's report, the directory index, number formats

print_report(): the report for one CSV, on stdout.
build_index(): the text of DIRECTORY_DOCUMENTATION.txt.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import datetime

from settings import (
    CROISSANT_SUBDIRECTORY,
    DEFAULT_VALUES_SHOWN,
    MARKDOWN_SUBDIRECTORY,
    MINIMUM_LISTING_COVERAGE,
    SINGLE_VALUE_THRESHOLD,
    TEXT_SUBDIRECTORY,
)

BYTE_UNITS = ("B", "KB", "MB", "GB", "TB")


def describe_bytes(count):
    """Return a byte count as "12.3 MB"."""
    size = float(count)
    for unit in BYTE_UNITS:
        if size < 1024 or unit == BYTE_UNITS[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024


def describe_seconds(seconds):
    """Return seconds as 45s, 3m12s or 1h04m."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def describe_number(value):
    """Return a number without invented precision: 5, not 5.0; "-" for None."""
    if value is None:
        return "-"
    if float(value).is_integer() and abs(value) < 2 ** 53:
        return f"{int(value):,}"
    return f"{value:,.6g}"


def truncate_end(text, limit):
    """Return text cut to limit characters, ending in an ellipsis if cut."""
    # Not textwrap.shorten: it cuts at spaces, and most values have none.
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def truncate_start(text, limit):
    """Return text cut to limit characters from the start, for filenames."""
    text = str(text)
    return text if len(text) <= limit else "..." + text[-(limit - 3):]


# --- one file's report ---

def print_header(header):
    """Print a header line and an underline."""
    print(header)
    print("  " + "-" * (len(header) - 2))


def print_identifier_report(result):
    """Print the columns worth checking before sharing, if the check ran."""
    # Reports what was seen; never says a file is safe.
    report = result["identifiers"]
    if not report["checked"]:
        return
    concerns = report["columns_of_concern"]
    if not concerns:
        print("\n  no columns matched the watch list and none look like paths")
        return

    print("\n  COLUMNS WORTH CHECKING BEFORE SHARING")
    width = max(len(entry["column_name"]) for entry in concerns)
    for entry in concerns:
        detail = []
        if entry["watched_by_name"]:
            detail.append(f"name matched: {', '.join(entry['matched_words'])}")
        if entry["all_digit_share"] < 1.0:
            detail.append(f"{1 - entry['all_digit_share']:.0%} of values contain letters")
        if entry["path_like_share"] > 0:
            detail.append(f"{entry['path_like_share']:.0%} look like paths")
        print(f"    {entry['column_name']:<{width}}  {entry['verdict']}")
        print(f"    {'':<{width}}  {'; '.join(detail)}")
        print(f"    {'':<{width}}  e.g. {entry['example'][:60]!r}")


def distinct_text(summary, suffix="+"):
    """Return the distinct count, with suffix when it is a floor."""
    count = f"{summary.distinct_count:,}"
    return count + suffix if summary.stopped_tracking_new_values else count


def print_column_table(summaries, width):
    """Print one row per column: empties, distinct count, lengths, commonest value."""
    print()
    print_header(f"  {'column':<{width}}  {'empty':>9}  {'NA-ish':>7}  {'distinct':>9}"
                 f"  {'shortest':>8}  {'longest':>7}  {'most common value':<30}  {'share':>6}")
    for summary in summaries:
        top_text, share_text = "-", "-"
        if summary.value_counts:
            value, count = summary.most_common(1)[0]
            filled = summary.filled_in_count
            top_text = truncate_end(repr(value), 28)
            share_text = f"{count / filled if filled else 0:>5.1%}"
        print(f"  {summary.column_name:<{width}}"
              f"  {summary.empty_count:>9,}"
              f"  {summary.null_like_text_count:>7,}"
              f"  {distinct_text(summary):>9}"
              f"  {str(summary.shortest_value_length or '-'):>8}"
              f"  {str(summary.longest_value_length or '-'):>7}"
              f"  {top_text:<30}"
              f"  {share_text:>6}")


def print_quiet_columns(summaries):
    """Print the columns that are all empty or hold one value."""
    empty = [s for s in summaries if s.is_all_empty]
    constant = [s for s in summaries if s.holds_one_value and not s.is_all_empty]
    if not empty and not constant:
        return
    print("\n  COLUMNS CARRYING NO INFORMATION")
    if empty:
        print(f"\n    empty in every row ({len(empty)})")
        for summary in empty:
            print(f"      {summary.column_name}")
    if constant:
        print(f"\n    one value covers at least {SINGLE_VALUE_THRESHOLD:.0%} of rows "
              f"({len(constant)})")
        width = max(len(s.column_name) for s in constant)
        for summary in constant:
            value, count = summary.most_common(1)[0]
            # A count, not a share: 2 rows in 39,432 would round to 0.0%.
            differ = summary.rows_seen - count
            note = f"   ({differ:,} of {summary.rows_seen:,} rows differ)" if differ else ""
            print(f"      {summary.column_name:<{width}}  {truncate_end(repr(value), 30):<32}"
                  f"{summary.single_value_share:>6.1%}{note}")


def print_numbers(summaries, width):
    """Print number counts and ranges for columns holding numbers."""
    numeric = [s for s in summaries if s.number_count]
    if not numeric:
        return
    print("\n  numbers")
    print_header(f"  {'column':<{width}}  {'numbers':>12}  {'not numbers':>12}"
                 f"  {'smallest':>16}  {'largest':>16}")
    for summary in numeric:
        print(f"  {summary.column_name:<{width}}"
              f"  {summary.number_count:>12,}"
              f"  {summary.not_a_number_count:>12,}"
              f"  {describe_number(summary.smallest_number):>16}"
              f"  {describe_number(summary.largest_number):>16}")


def print_value_lists(summaries, values_shown):
    """Print the commonest values per column.
    A list covering under MINIMUM_LISTING_COVERAGE of rows is replaced by one line."""
    print(f"\n  most common values (up to {values_shown} per column)")
    suppressed = []
    for summary in summaries:
        if not summary.value_counts:
            print(f"\n    {summary.column_name}: (no values; every row was empty)")
            continue
        distinct = f"{distinct_text(summary, ' or more')} distinct"
        shown = summary.most_common(values_shown)
        coverage = summary.share_covered_by(shown)
        filled = summary.filled_in_count
        if coverage < MINIMUM_LISTING_COVERAGE:
            value, count = shown[0]
            suppressed.append(
                f"    {summary.column_name}  ({distinct}): list suppressed, "
                f"top {len(shown)} cover only {coverage:.1%} of rows; "
                f"most common {truncate_end(repr(value), 30)} at {count / filled:.1%}")
            continue
        print(f"\n    {summary.column_name}  ({distinct})")
        for value, count in shown:
            share = count / filled if filled else 0
            print(f"      {truncate_end(repr(value), 40):<42} {count:>12,}  {share:>6.1%}")

    if suppressed:
        print(f"\n  columns whose values do not repeat enough to list "
              f"(under {MINIMUM_LISTING_COVERAGE:.0%} coverage)")
        print("\n".join(suppressed))


def print_report(result, values_shown=DEFAULT_VALUES_SHOWN):
    """Print the full report for one describe_csv() result."""
    details = result["file"]
    summaries = list(result["_summaries"].values())
    width = max(len(summary.column_name) for summary in summaries)

    print(f"\n{details['path']}")
    print(f"  {describe_bytes(details['size_in_bytes'])} on disk"
          f"  |  {details['column_count']} columns"
          f"  |  {details['rows_read']:,} rows read"
          f"  |  {details['seconds_taken']}s")
    print(f"  {details['scope']}")
    print_identifier_report(result)
    print_column_table(summaries, width)
    print_quiet_columns(summaries)
    print_numbers(summaries, width)
    print_value_lists(summaries, values_shown)


# --- the directory index ---

def index_entry(outcome):
    """Return one CSV's lines for DIRECTORY_DOCUMENTATION.txt."""
    lines = [f"  {outcome['file'].name}  ({describe_bytes(outcome['file'].stat().st_size)})"]
    if not outcome["ok"]:
        lines.append(f"      COULD NOT READ: {outcome['problem']}")
        lines.append(f"      details in {outcome['report_path'].name}")
        return lines

    if outcome.get("skipped"):
        lines.append(f"      SKIPPED: already documented. {outcome['rows_read']:,} rows, "
                     f"{outcome['column_count']} columns, from the previous run "
                     f"({outcome['scope']})")
    else:
        lines.append(f"      {outcome['rows_read']:,} rows read, "
                     f"{outcome['column_count']} columns, {outcome['seconds']}s, "
                     f"{outcome['scope']}")

    if outcome["columns_to_check"]:
        lines.append(f"      check before sharing: {', '.join(outcome['columns_to_check'])}")
    elif outcome.get("check_results_unknown"):
        # Silence would read as "nothing to check".
        lines.append("      check before sharing: NOT KNOWN; the previous run did not "
                     "record it. Rerun this file without --skip-existing to find out.")
    if outcome["empty_columns"]:
        lines.append(f"      empty in every row ({len(outcome['empty_columns'])}): "
                     f"{', '.join(outcome['empty_columns'])}")
    if outcome["constant_columns"]:
        lines.append(f"      one value covers {SINGLE_VALUE_THRESHOLD:.0%}+ of rows "
                     f"({len(outcome['constant_columns'])}): "
                     f"{', '.join(outcome['constant_columns'])}")

    lines.append(f"      described in {TEXT_SUBDIRECTORY}/{outcome['report_path'].name}")
    if outcome.get("croissant_path"):
        lines.append(f"      par-baked croissant: {CROISSANT_SUBDIRECTORY}/"
                     f"{outcome['croissant_path'].name}, NOT REVIEWED, fails validation "
                     "on purpose")
        if outcome.get("markdown_path"):
            lines.append(f"      rendered as: {MARKDOWN_SUBDIRECTORY}/"
                         f"{outcome['markdown_path'].name}")
    if outcome.get("croissant_problem"):
        lines.append(f"      croissant NOT written: {outcome['croissant_problem']}")
    return lines


def build_index(directory_to_scan, results, other_files, settings):
    """Return the text of DIRECTORY_DOCUMENTATION.txt."""
    scanned_at = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "# DIRECTORY DOCUMENTATION",
        "",
        f"Directory scanned: {directory_to_scan}",
        f"Scanned at:        {scanned_at}",
        f"Read:              {settings['scope_description']}",
        "",
        f"# CSV files ({len(results)})",
    ]
    for outcome in results:
        lines += [""] + index_entry(outcome)
    if not results:
        lines += ["", "  (none)"]

    lines += ["", f"# Other files, not examined ({len(other_files)})", ""]
    lines += [f"  {other.name}  ({describe_bytes(other.stat().st_size)})"
              for other in other_files] or ["  (none)"]
    lines.append("")
    return "\n".join(lines)
