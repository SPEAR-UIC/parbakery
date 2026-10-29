"""parbake/staging.py - local copies of files for --batch-local-copies

For Lustre and similar, where each file open costs a metadata round trip.
Each worker copies its file, reads the copy, deletes it.
Never more copies, or readers on the shared filesystem, than workers.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import gettempdir, mkdtemp

from settings import STAGING_PREFIX


def staging_location(parent=None):
    """Return the directory copies will go under, for announcing before a run."""
    return parent or os.environ.get("TMPDIR") or gettempdir()


class StagingArea:
    """The run's copy directory; made and removed by the parent."""
    # Not by workers: a worker killed mid-copy cannot clean up after itself.

    def __init__(self, parent=None):
        """Record the parent directory; nothing is made until __enter__."""
        self.parent = parent
        self.root = None

    def __enter__(self):
        """Make the copy directory under parent or $TMPDIR."""
        if self.parent is not None:
            Path(self.parent).mkdir(parents=True, exist_ok=True)
        # dir=None lets mkdtemp honour $TMPDIR, usually node-local storage.
        self.root = Path(mkdtemp(prefix=STAGING_PREFIX, dir=self.parent))
        return self

    def __exit__(self, *_):
        """Remove the copy directory and everything in it."""
        if self.root is not None:
            shutil.rmtree(self.root, ignore_errors=True)
            self.root = None
        return False


@contextmanager
def staged_copy(root, original):
    """Yield (path to read, copy problem) for original copied into root.
    With no root, or on a failed copy, yield the original itself."""
    original = Path(original)
    if root is None:
        yield original, None
        return
    # Own directory, so the copy keeps its name: outputs are named after the file.
    holding = Path(mkdtemp(dir=root))
    try:
        destination = holding / original.name
        try:
            # copy2 keeps size and mtime, which checkpoints fingerprint.
            shutil.copy2(original, destination)
        except Exception as problem:
            yield original, problem
        else:
            yield destination, None
    finally:
        shutil.rmtree(holding, ignore_errors=True)
