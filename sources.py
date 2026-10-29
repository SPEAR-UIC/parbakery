"""parbake/sources.py - which files are read, their compression, and row estimates

Row estimates only feed a progress bar; compression detection must be exact.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import bz2
import lzma
import zlib
from pathlib import Path

import pandas

from settings import (
    CELL_BUDGET_PER_BATCH,
    COMPRESSION_SUFFIXES,
    CSV_SUFFIXES,
    EXACT_ROW_COUNT_LIMIT,
    LITERAL_READ_SETTINGS,
    MAXIMUM_BATCH_ROWS,
    MINIMUM_BATCH_ROWS,
)

DECOMPRESSORS = {
    ".gz": lambda: zlib.decompressobj(32 + zlib.MAX_WBITS),   # 32: accept a gzip header
    ".bz2": bz2.BZ2Decompressor,
    ".xz": lzma.LZMADecompressor,
}

# Estimation samples.
SAMPLE_BYTES = 1 << 16
SAMPLE_POINTS = 8
COMPRESSED_SAMPLE_BYTES = 1 << 18
COMPRESSED_CHUNK = 1 << 15


def suffix_of(path, suffixes):
    """Return the first of suffixes the path name ends with, or ""."""
    # Not Path.suffix: for "jobs.csv.gz" that is ".gz".
    name = str(path).lower()
    return next((suffix for suffix in suffixes if name.endswith(suffix)), "")


def matched_csv_suffix(path):
    """Return the CSV suffix this path ends with, or ""."""
    return suffix_of(path, CSV_SUFFIXES)


def find_files(directory):
    """Return (CSV files, other files) directly inside directory, sorted by name."""
    csv_files, other_files = [], []
    for entry in sorted(directory.iterdir()):
        if entry.is_file():
            (csv_files if matched_csv_suffix(entry) else other_files).append(entry)
    return csv_files, other_files


def dataset_stem(path):
    """Return the name without its CSV suffix: jobs.csv.gz -> jobs."""
    path = Path(path)
    suffix = matched_csv_suffix(path)
    return path.name[: -len(suffix)] if suffix else path.stem


def estimate_compressed_rows(path, suffix):
    """Return estimated rows in a compressed file, from its measured ratio."""
    # Measured chunks, not gzip.open: it buffers ahead, so a small file reads as ratio 1.
    decompressor = DECOMPRESSORS[suffix]()
    produced, fed = bytearray(), 0
    try:
        with open(path, "rb") as raw:
            while len(produced) < COMPRESSED_SAMPLE_BYTES:
                block = raw.read(COMPRESSED_CHUNK)
                if not block:
                    break
                fed += len(block)
                produced += decompressor.decompress(block)
    except Exception:
        return None

    newlines = produced.count(b"\n")
    if not fed or newlines < 2:
        return None
    uncompressed_size = path.stat().st_size * len(produced) / fed
    return max(1, int(uncompressed_size / (len(produced) / newlines)) - 1)


def estimate_row_count(path):
    """Return estimated rows, or None if the file is too small or odd to say.
    Count small files; sample larger ones at evenly spaced points."""
    path = Path(path)
    file_size = path.stat().st_size
    if file_size == 0:
        return None
    compression = suffix_of(path, COMPRESSION_SUFFIXES)
    if compression:
        return estimate_compressed_rows(path, compression)

    if file_size <= EXACT_ROW_COUNT_LIMIT:
        with open(path, "rb") as handle:
            newlines = sum(block.count(b"\n") for block in iter(lambda: handle.read(1 << 20), b""))
        return max(0, newlines - 1)

    # Not just the start: early rows can run long (Polaris came out 24% low).
    total_bytes = total_newlines = 0
    with open(path, "rb") as handle:
        for point in range(SAMPLE_POINTS):
            handle.seek(file_size * point // SAMPLE_POINTS)
            if point:
                handle.readline()           # skip to a whole line
            sample = handle.read(SAMPLE_BYTES)
            cut = sample.rfind(b"\n")       # and drop the partial last one
            if cut <= 0:
                continue
            total_bytes += cut + 1
            total_newlines += sample.count(b"\n", 0, cut + 1)

    if total_newlines < 2:
        return None
    return max(1, int(file_size / (total_bytes / total_newlines)) - 1)


def read_header(path, separator):
    """Return column names exactly as written, in file order."""
    return list(pandas.read_csv(path, sep=separator, nrows=0, **LITERAL_READ_SETTINGS).columns)


def rows_per_batch(column_count):
    """Return rows per batch that keep cells, and so memory, about constant."""
    # A fixed row count lets one wide file kill a worker that was fine on the last.
    if column_count < 1:
        return MINIMUM_BATCH_ROWS
    return max(MINIMUM_BATCH_ROWS, min(MAXIMUM_BATCH_ROWS, CELL_BUDGET_PER_BATCH // column_count))
