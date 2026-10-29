"""parbake/checkpoints.py - saved progress, so a killed read resumes instead of starting over

One accumulator, continued, never merged: a resumed read measures what a straight one would.
Used only when format, source size and mtime, and measuring settings all match.
Size and mtime, not a hash: hashing 500 GB costs more than resuming saves.
Batch size is not fingerprinted: it cannot change a measurement.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import json
import os
import time
from pathlib import Path

from measuring import ColumnSummary
from settings import CHECKPOINT_EVERY_ROWS

# Bump when the saved shape changes, so old checkpoints are ignored.
CHECKPOINT_FORMAT = 1
SUFFIX = ".checkpoint.json"
WRITING_SUFFIX = SUFFIX + ".writing"


def fingerprint_source(path):
    """Return the source's resolved path, size and mtime."""
    stat = Path(path).stat()
    return {
        "path": str(Path(path).resolve()),
        "size_in_bytes": stat.st_size,
        "modified_at": int(stat.st_mtime),
    }


def fingerprint_settings(separator, values_tracked, preview_rows):
    """Return the settings that change what is measured."""
    return {"separator": separator, "values_tracked": values_tracked,
            "preview_rows": preview_rows}


class CheckpointStore:
    """One checkpoint per dataset, in a directory of their own."""

    def __init__(self, directory, enabled=True, every_rows=CHECKPOINT_EVERY_ROWS):
        """Set up the store; disabled when directory is None."""
        self.directory = Path(directory) if directory else None
        self.enabled = bool(enabled and self.directory)
        self.every_rows = every_rows

    def path_for(self, dataset_stem, suffix=SUFFIX):
        """Return the checkpoint path for a dataset."""
        return self.directory / f"{dataset_stem}{suffix}"

    def is_due(self, rows_done, rows_at_last_save):
        """Return whether every_rows have passed since the last save."""
        return self.enabled and rows_done - rows_at_last_save >= self.every_rows

    def save(self, dataset_stem, source_path, settings, rows_done, summaries,
             identifier_check=None):
        """Write a checkpoint atomically; return its path, or None if disabled."""
        # Atomic: a half-written checkpoint would be trusted.
        if not self.enabled:
            return None
        document = {
            "parbake_checkpoint": CHECKPOINT_FORMAT,
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": fingerprint_source(source_path),
            "settings": settings,
            "rows_done": rows_done,
            "columns": {name: summary.state() for name, summary in summaries.items()},
            "identifiers": identifier_check.state() if identifier_check else None,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        final = self.path_for(dataset_stem)
        partial = self.path_for(dataset_stem, WRITING_SUFFIX)
        partial.write_text(json.dumps(document), encoding="utf-8")
        os.replace(partial, final)
        return final

    def load(self, dataset_stem, source_path, settings):
        """Return (checkpoint, reason), or (None, why not)."""
        if not self.enabled:
            return None, "checkpointing is off"
        path = self.path_for(dataset_stem)
        if not path.is_file():
            return None, "no checkpoint"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as problem:
            return None, f"checkpoint unreadable ({type(problem).__name__})"

        if document.get("parbake_checkpoint") != CHECKPOINT_FORMAT:
            return None, "checkpoint written by a different version"
        try:
            current = fingerprint_source(source_path)
        except OSError:
            return None, "source file is gone"
        if document.get("source") != current:
            return None, "the data file has changed since the checkpoint"
        if document.get("settings") != settings:
            return None, "settings have changed since the checkpoint"
        if not document.get("rows_done"):
            return None, "checkpoint has no progress in it"
        return document, f"resuming after {document['rows_done']:,} rows"

    @staticmethod
    def rebuild(document, column_names, values_tracked):
        """Return (rows_done, summaries, identifier_state) from a loaded checkpoint.
        A column missing from it starts fresh."""
        saved = document.get("columns", {})
        summaries = {
            name: (ColumnSummary.from_state(name, position, saved[name], values_tracked)
                   if name in saved else ColumnSummary(name, position, values_tracked))
            for position, name in enumerate(column_names)
        }
        return document["rows_done"], summaries, document.get("identifiers")

    def discard(self, dataset_stem):
        """Delete one dataset's checkpoint and any partial write."""
        if not self.enabled:
            return
        self.path_for(dataset_stem).unlink(missing_ok=True)
        self.path_for(dataset_stem, WRITING_SUFFIX).unlink(missing_ok=True)

    def clear(self):
        """Remove every checkpoint, then the directory if empty; return whether it went."""
        # Only checkpoint files: --checkpoint-dir may point somewhere shared.
        if not self.directory or not self.directory.is_dir():
            return False
        for entry in self.directory.iterdir():
            if entry.is_file() and entry.name.endswith((SUFFIX, WRITING_SUFFIX)):
                entry.unlink()
        try:
            self.directory.rmdir()
        except OSError:
            return False
        return True

    def outstanding(self):
        """Return checkpoint paths left by an earlier run, sorted."""
        if not self.directory or not self.directory.is_dir():
            return []
        return sorted(self.directory.glob(f"*{SUFFIX}"))
