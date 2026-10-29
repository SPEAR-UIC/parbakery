#!/usr/bin/env python3
"""parbake/utilities/dedupe_values.py - reduce unique_extractor.py output to each value once, sorted

    python3 utilities/dedupe_values.py values.csv [--out unique.csv]
Read as CSV, not sort -u: a value may hold a newline.
Every distinct value is held in memory.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import argparse
import csv
import sys
from contextlib import nullcontext


def main():
    """Parse the command line, read the values, write them deduplicated."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("values_csv", help="a file written by unique_extractor.py")
    parser.add_argument("--out", help="where to write the distinct values (default: stdout)")
    arguments = parser.parse_args()

    with open(arguments.values_csv, newline="", encoding="utf-8") as source:
        reader = csv.reader(source)
        header = next(reader)
        values, rows_read = set(), 0
        for row in reader:
            values.add(row[0])
            rows_read += 1
    distinct = sorted(values)

    output = (open(arguments.out, "w", newline="", encoding="utf-8") if arguments.out
              else nullcontext(sys.stdout))
    with output as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows([value] for value in distinct)
    # stderr, so the count never mixes into values on stdout.
    print(f"{rows_read:,} value(s) read, {len(distinct):,} distinct", file=sys.stderr)


if __name__ == "__main__":
    main()
