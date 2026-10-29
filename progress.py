"""parbake/progress.py - progress display for several files at once, one line per file

    [1/4] POLARIS_20220809_20221231.csv  [##########----------]  52%   20,480 rows   0.8s
    [3/4] id_mapping.csv                                          waiting
On a terminal: redrawn in place. Otherwise: one line per file as it finishes.
Resource line: memory and cores busy across the process tree.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import os
import shutil
import sys
import time
from collections import Counter

import psutil

from reporting import describe_bytes, describe_seconds, truncate_end, truncate_start
from settings import SINGLE_VALUE_THRESHOLD

BAR_WIDTH = 20
FILLED = "#"
EMPTY = "-"

REDRAW_SECONDS = 0.2
# Sampling every worker's CPU and memory costs far more than a redraw.
RESOURCE_SECONDS = 5.0

# A cut trailing field shorter than this is dropped rather than left as a stub.
SHORTEST_CUT_FIELD = 8


def terminal_size():
    """Return the terminal size, or 100x24 if unknown."""
    return shutil.get_terminal_size((100, 24))


def fit_line(line, limit):
    """Return line cut to limit, at a field boundary (two spaces) where possible.
    A cut last field keeps an ellipsis if SHORTEST_CUT_FIELD shows, else is dropped."""
    if len(line) <= limit:
        return line
    cut = line[:limit]
    around = line[max(0, limit - 1): limit + 1]
    if around == "  " or line[limit: limit + 2] == "  " or cut.endswith("  "):
        return cut.rstrip()
    boundary = cut.rfind("  ")
    if boundary < 0:
        return truncate_end(line, limit)
    if len(cut) - (boundary + 2) >= SHORTEST_CUT_FIELD:
        return line[: limit - 1] + "…"
    return cut[:boundary].rstrip()


def render_bar(fraction, width=BAR_WIDTH):
    """Return a bar for fraction 0.0 to 1.0; question marks if None."""
    if fraction is None:
        return "?" * width
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return FILLED * filled + EMPTY * (width - filled)


DISPLAY_KEY = (
    "rows      rows checked",
    "cols      columns checked",
    "time      time elapsed",
    f"constant  flagged columns holding the same value {SINGLE_VALUE_THRESHOLD:.0%}+ of the time",
    "check     flagged for an anonymity check",
)
STAGING_KEY = ("copying   being copied to local disk, not yet read",)


def print_key(say=print, staging=False):
    """Print the key to the display columns."""
    say("  key")
    for line in DISPLAY_KEY + (STAGING_KEY if staging else ()):
        say(f"    {line}")


# --- what the run is costing ---

TOTAL_CORES = psutil.cpu_count() or 1


def process_tree(root_pid=None):
    """Return the root process and all its descendants; empty on error."""
    # Recursive: workers are children of the forkserver, not of this process.
    try:
        root = psutil.Process(root_pid or os.getpid())
        return [root] + root.children(recursive=True)
    except psutil.Error:
        return []


class ResourceMonitor:
    """Memory held and cores busy across the process tree."""

    def __init__(self, root_pid=None):
        """Start with no samples taken."""
        self.root_pid = root_pid
        # Per pid, so an exiting worker cannot make the next rate negative.
        self._cpu_by_pid = {}
        self._sampled_at = None
        self._cpu_total = 0

    def sample(self):
        """Return process count, memory bytes and cores busy since the last sample."""
        processes = process_tree(self.root_pid)
        memory = 0

        for process in processes:
            try:
                memory += process.memory_info().rss
                times = process.cpu_times()
                self._cpu_by_pid[process.pid] = times.user + times.system
            except psutil.Error:
                continue        # exited while being read

        now = time.monotonic()
        cpu_total = sum(self._cpu_by_pid.values())

        cores_busy = None
        if self._sampled_at is not None:
            elapsed = now - self._sampled_at
            if elapsed > 0:
                cores_busy = (cpu_total - self._cpu_total) / elapsed

        self._sampled_at = now
        self._cpu_total = cpu_total

        return {
            "processes": len(processes),
            "memory_bytes": memory,
            "cores_busy": cores_busy,       # None until a second sample
        }

    def line(self, workers=None):
        """Return the resource line, from a new sample."""
        # Workers passed in: the tree also holds the forkserver and manager.
        reading = self.sample()

        workers_text = (f"{workers} worker(s)" if workers is not None
                        else f"{reading['processes']} process(es)")

        busy = reading["cores_busy"]
        cpu_text = "  ?  " if busy is None else f"{busy:.1f}"

        return (f"  {workers_text}"
                f"  |  {describe_bytes(reading['memory_bytes'])} in use"
                f"  |  {cpu_text} of {TOTAL_CORES} cores busy")


# --- the display ---

class FileProgress:
    """One file's state, rows read and timing."""

    def __init__(self, name, estimated_rows=None):
        """Start waiting, with no rows read."""
        self.name = name
        self.estimated_rows = estimated_rows
        self.rows_read = 0
        self.seconds = 0.0          # from the worker, once finished
        self.started_at = None
        self.state = "waiting"      # waiting | copying | reading | done | failed
        self.finished_at = None
        self.note = ""
        self.reported = False       # printed, when not redrawing

    @property
    def elapsed(self):
        """Return seconds spent: the worker's total once finished, else since start."""
        # Local clock while reading: worker messages come per batch and would freeze it.
        if self.state in ("done", "failed"):
            return self.seconds
        if self.started_at is None:
            return 0.0
        return time.monotonic() - self.started_at

    @property
    def fraction(self):
        """Return the share of estimated rows read, or None if no estimate."""
        if self.state == "done":
            return 1.0
        if not self.estimated_rows:
            return None
        return min(self.rows_read / self.estimated_rows, 1.0)

    def line(self, index, total, name_width):
        """Return this file's display line."""
        label = f"[{index}/{total}] {truncate_start(self.name, name_width):<{name_width}}"

        if self.state in ("waiting", "copying"):
            return f"  {label}  {'':<{BAR_WIDTH}}   {self.state}"

        bar = render_bar(self.fraction)
        if self.state == "failed":
            return f"  {label}  [{bar}]  FAILED  {self.note}"

        if self.state == "done":
            return (f"  {label}  [{bar}]   done  {self.rows_read:>12,} rows"
                    f"  {describe_seconds(self.elapsed):>6}  {self.note}")

        share = "  ?  " if self.fraction is None else f"{self.fraction:>4.0%}"
        return (f"  {label}  [{bar}]  {share}  {self.rows_read:>12,} rows"
                f"  {describe_seconds(self.elapsed):>6}")


class ProgressDisplay:
    """The display for all files, drawn to a stream."""

    def __init__(self, names, estimated_rows=None, stream=None,
                 show_resources=True, workers=None):
        """Set up one FileProgress per name and size names to the terminal."""
        self.stream = stream or sys.stderr
        self.show_resources = show_resources
        self.workers = workers
        self.monitor = ResourceMonitor() if show_resources else None
        self.resource_text = ""
        self.resources_read_at = 0.0
        self.can_redraw = hasattr(self.stream, "isatty") and self.stream.isatty()
        self.files = [
            FileProgress(name, (estimated_rows or {}).get(name)) for name in names
        ]
        self.by_name = {progress.name: progress for progress in self.files}
        self.lines_drawn = 0
        self.last_drawn_at = 0.0

        self.longest_name = max((len(name) for name in names), default=20)
        self.columns = terminal_size().columns
        self.name_width = self.fit_names(self.columns)

    def fit_names(self, columns):
        """Return the name width that keeps a line within columns."""
        # Measured from a real line: a wrapped line leaves a copy of every frame behind.
        total = len(self.files)
        fixed = 0
        for state in ("reading", "done"):
            sample = FileProgress("", estimated_rows=1)
            sample.state = state
            fixed = max(fixed, len(sample.line(total, total, 0)))
        return max(12, min(self.longest_name, columns - 1 - fixed))

    def update(self, name, **changes):
        """Record a change to one file; do not draw."""
        progress = self.by_name.get(name)
        if progress is None:
            return
        if changes.get("state") == "reading" and progress.started_at is None:
            progress.started_at = time.monotonic()
        if changes.get("state") in ("done", "failed") and progress.finished_at is None:
            progress.finished_at = time.monotonic()
        for field, value in changes.items():
            setattr(progress, field, value)

    def summary_line(self):
        """Return the count of files in each state, on one line."""
        counts = Counter(progress.state for progress in self.files)
        parts = [f"{counts[state]:,} {state}"
                 for state in ("done", "failed", "copying", "reading", "waiting")
                 if counts[state]]
        return f"  {len(self.files):,} files: " + (", ".join(parts) or "starting")

    def window_of_file_lines(self, room):
        """Return at most room lines: active files, then the most recently finished."""
        # Not a window by position: one slow file would pin it.
        if room <= 0:
            return []
        total = len(self.files)

        active = [position for position, progress in enumerate(self.files)
                  if progress.state in ("reading", "copying")]
        not_shown = max(0, len(active) - room)
        shown = set(active[:room])

        spare = room - len(shown)
        if spare > 0:
            finished = sorted(
                (position for position, progress in enumerate(self.files)
                 if progress.finished_at is not None),
                key=lambda position: self.files[position].finished_at,
            )
            shown.update(finished[-spare:])

        if not shown:
            shown = set(range(min(room, total)))

        drawn = [self.files[position].line(position + 1, total, self.name_width)
                 for position in sorted(shown)]
        if not_shown:
            drawn[-1] = f"  ... and {not_shown:,} more being read"
        return drawn

    def draw(self, force=False):
        """Draw, at most every REDRAW_SECONDS unless forced."""
        now = time.monotonic()
        if not force and now - self.last_drawn_at < REDRAW_SECONDS:
            return
        self.last_drawn_at = now
        if self.can_redraw:
            self.redraw(now)
        else:
            self.report_finished()
        self.stream.flush()

    def report_finished(self):
        """Print a line for each newly finished file."""
        total = len(self.files)
        for number, progress in enumerate(self.files, start=1):
            if progress.state in ("done", "failed") and not progress.reported:
                self.stream.write(progress.line(number, total, self.name_width) + "\n")
                progress.reported = True

    def redraw(self, now):
        """Overwrite the previous frame with the current one."""
        lines = []
        if self.show_resources:
            if now - self.resources_read_at >= RESOURCE_SECONDS or not self.resource_text:
                self.resource_text = self.monitor.line(workers=self.workers)
                self.resources_read_at = now
            lines += [self.resource_text, ""]

        # Read every frame: the terminal can be resized mid-run.
        size = terminal_size()
        if size.columns != self.columns:
            self.columns = size.columns
            self.name_width = self.fit_names(size.columns)
        # A frame taller than the screen scrolls the cursor away; keep a row spare.
        room = max(1, size.lines - len(lines) - 1)

        total = len(self.files)
        if room >= total:
            lines += [progress.line(number, total, self.name_width)
                      for number, progress in enumerate(self.files, start=1)]
        else:
            lines.append(self.summary_line())
            lines += self.window_of_file_lines(room - 1)

        # One short of the width so nothing wraps. Overwrite in place: clearing flickers.
        lines = [fit_line(line, self.columns - 1) for line in lines]
        frame = [f"\033[{self.lines_drawn}A"] if self.lines_drawn else []
        frame += [f"\r{line}\033[K\n" for line in lines]
        frame.append("\033[J")
        self.stream.write("".join(frame))
        self.lines_drawn = len(lines)

    def finish(self):
        """Draw the final frame."""
        self.draw(force=True)
