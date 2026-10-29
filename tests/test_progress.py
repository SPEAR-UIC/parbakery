"""parbake/tests/test_progress.py - tests for the progress display (progress.py)

Run: pytest tests/test_progress.py
A FakeTerminal stream stands in for a tty; pyte is optional.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import io
import re
import os

import pytest

from progress import (
    BAR_WIDTH,
    FileProgress,
    ProgressDisplay,
    describe_seconds,
    render_bar,
    truncate_start,
)


# --- render_bar ---

@pytest.mark.parametrize("fraction, expected", [
    (0.0, "-" * 20),
    (0.5, "#" * 10 + "-" * 10),
    (1.0, "#" * 20),
])
def test_bar_fills_in_proportion(fraction, expected):
    assert render_bar(fraction) == expected


def test_an_unknown_fraction_is_drawn_as_unknown():
    assert render_bar(None) == "?" * BAR_WIDTH


@pytest.mark.parametrize("fraction", [-0.5, 1.5])
def test_the_bar_cannot_overflow(fraction):
    """Row estimates are approximate, so reads can pass 100%."""
    bar = render_bar(fraction)
    assert len(bar) == BAR_WIDTH


# --- durations and names ---

@pytest.mark.parametrize("seconds, expected", [
    (5, "5s"), (45, "45s"), (90, "1m30s"), (3700, "1h01m"), (7325, "2h02m"),
])
def test_durations_stay_short(seconds, expected):
    assert describe_seconds(seconds) == expected


def test_a_long_name_is_trimmed_from_the_left():
    """The end of a filename is the part that tells files apart."""
    trimmed = truncate_start("ANL-ALCF-DJC-POLARIS_20220809_20221231.csv", 25)
    assert len(trimmed) == 25
    assert trimmed.endswith("20221231.csv")


def test_a_short_name_is_left_alone():
    assert truncate_start("jobs.csv", 25) == "jobs.csv"


# --- FileProgress ---

def test_a_waiting_file_says_so():
    progress = FileProgress("jobs.csv", estimated_rows=100)
    assert "waiting" in progress.line(1, 3, 20)


def test_progress_is_shown_against_the_estimate():
    progress = FileProgress("jobs.csv", estimated_rows=1000)
    progress.state = "reading"
    progress.rows_read = 250
    assert progress.fraction == 0.25


def test_a_file_past_its_estimate_is_capped_at_full():
    """Case: 130 rows read against an estimate of 100."""
    progress = FileProgress("jobs.csv", estimated_rows=100)
    progress.state = "reading"
    progress.rows_read = 130
    assert progress.fraction == 1.0


def test_a_file_with_no_estimate_has_no_fraction():
    progress = FileProgress("jobs.csv", estimated_rows=None)
    progress.state = "reading"
    progress.rows_read = 500
    assert progress.fraction is None
    assert "500" in progress.line(1, 1, 20)


def test_a_finished_file_is_full_even_if_the_estimate_was_wrong():
    progress = FileProgress("jobs.csv", estimated_rows=1_000_000)
    progress.state = "done"
    progress.rows_read = 12
    assert progress.fraction == 1.0


def test_a_failed_file_says_so():
    progress = FileProgress("broken.csv")
    progress.state = "failed"
    progress.note = "ParserError"
    line = progress.line(1, 1, 20)
    assert "FAILED" in line and "ParserError" in line


# --- ProgressDisplay ---

class FakeTerminal(io.StringIO):
    """StringIO that reports itself as a terminal."""

    def isatty(self):
        """Return True."""
        return True


CONTROL = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\r")


def visible_lines(text):
    """Return the non-blank lines of a frame with control codes removed."""
    return [line for line in CONTROL.sub("", text).splitlines() if line.strip()]


def test_the_display_has_one_line_per_file():
    stream = FakeTerminal()
    display = ProgressDisplay(["a.csv", "b.csv", "c.csv"], stream=stream,
                              show_resources=False)
    display.draw(force=True)

    written = stream.getvalue()
    assert len(visible_lines(written)) == 3
    for name in ("a.csv", "b.csv", "c.csv"):
        assert name in written


def test_redrawing_moves_the_cursor_back_up_over_the_last_frame():
    """Otherwise each refresh appends a new block."""
    stream = FakeTerminal()
    display = ProgressDisplay(["a.csv", "b.csv"], stream=stream,
                              show_resources=False)

    display.draw(force=True)
    first_frame = stream.getvalue()
    assert not re.search(r"\x1b\[\d+A", first_frame)

    display.draw(force=True)
    assert "\x1b[2A" in stream.getvalue()      # one line per file


def test_redrawing_covers_the_resource_line_too():
    """Otherwise the display walks down the screen."""
    stream = FakeTerminal()
    display = ProgressDisplay(["a.csv", "b.csv"], stream=stream,
                              show_resources=True)

    display.draw(force=True)
    display.draw(force=True)
    assert "\x1b[4A" in stream.getvalue()      # 2 files + resource line + blank


def test_drawing_is_rate_limited():
    """Redrawing on every message costs more than the work being measured."""
    stream = FakeTerminal()
    display = ProgressDisplay(["a.csv"], stream=stream)

    display.draw(force=True)
    length_after_first = len(stream.getvalue())
    display.draw()                              # within REDRAW_SECONDS
    assert len(stream.getvalue()) == length_after_first


def test_updates_reach_the_right_file():
    display = ProgressDisplay(["a.csv", "b.csv"], {"a.csv": 100}, stream=io.StringIO())
    display.update("a.csv", state="reading", rows_read=50)

    assert display.by_name["a.csv"].rows_read == 50
    assert display.by_name["b.csv"].rows_read == 0


def test_an_update_for_an_unknown_file_is_ignored():
    display = ProgressDisplay(["a.csv"], stream=io.StringIO())
    display.update("nonexistent.csv", state="reading")


def test_the_clock_starts_when_a_file_starts():
    """Worker messages arrive per batch, so a slow file would look frozen."""
    display = ProgressDisplay(["a.csv"], stream=io.StringIO())
    assert display.by_name["a.csv"].started_at is None

    display.update("a.csv", state="reading")
    assert display.by_name["a.csv"].started_at is not None


def test_a_finished_file_reports_the_workers_time_not_ours():
    display = ProgressDisplay(["a.csv"], stream=io.StringIO())
    display.update("a.csv", state="reading")
    display.update("a.csv", state="done", seconds=42.0)
    assert display.by_name["a.csv"].elapsed == 42.0


def test_redrawing_is_off_when_the_output_is_not_a_terminal():
    """Cursor codes in a log file are worse than no progress."""
    display = ProgressDisplay(["a.csv"], stream=io.StringIO())
    assert display.can_redraw is False


def test_a_non_terminal_reports_each_file_once_when_it_finishes():
    stream = io.StringIO()
    display = ProgressDisplay(["a.csv"], stream=stream)

    display.update("a.csv", state="reading")
    display.draw(force=True)
    assert stream.getvalue() == ""

    display.update("a.csv", state="done", rows_read=10, seconds=1.0)
    display.draw(force=True)
    assert "done" in stream.getvalue()

    display.draw(force=True)
    assert stream.getvalue().count("done") == 1


# --- DISPLAY_KEY ---

def test_the_key_explains_every_word_the_display_uses():
    """Measured case: "dull" shipped with no key entry."""
    from progress import DISPLAY_KEY

    explained = {line.split()[0] for line in DISPLAY_KEY}
    assert {"rows", "cols", "time", "constant", "check"} <= explained


def test_the_key_prints_each_term_with_its_meaning():
    said = []
    from progress import print_key
    print_key(said.append)

    text = "\n".join(said)
    assert "key" in text
    for term in ("rows", "cols", "time", "constant", "check"):
        assert term in text


# --- ResourceMonitor ---

def test_the_resource_line_reports_workers_memory_and_cpu():
    from progress import ResourceMonitor

    line = ResourceMonitor().line(workers=4)
    assert "4 worker(s)" in line
    assert "in use" in line
    assert "cores busy" in line


def test_the_worker_count_is_told_not_guessed():
    """Measured case: counting processes reported 2 workers for a 4-worker run."""
    from progress import ResourceMonitor

    assert "4 worker(s)" in ResourceMonitor().line(workers=4)
    assert "process(es)" in ResourceMonitor().line()


def test_cpu_is_a_rate_not_a_running_total():
    import time

    from progress import ResourceMonitor

    monitor = ResourceMonitor()
    assert monitor.sample()["cores_busy"] is None

    start = time.monotonic()
    while time.monotonic() - start < 0.3:
        sum(index * index for index in range(100_000))
    busy = monitor.sample()["cores_busy"]
    assert busy is not None and busy > 0.1, f"expected a core to look busy, got {busy}"

    time.sleep(0.3)
    assert monitor.sample()["cores_busy"] < busy


def test_a_worker_finishing_cannot_make_the_rate_negative():
    """Diffing a summed total goes negative when a worker exits."""
    from progress import ResourceMonitor

    monitor = ResourceMonitor()
    monitor.sample()
    monitor._cpu_by_pid[999_999] = 5.0      # a pid that will not appear again
    monitor._cpu_total = sum(monitor._cpu_by_pid.values())

    assert monitor.sample()["cores_busy"] >= 0


def test_the_resource_line_can_be_turned_off():
    stream = FakeTerminal()
    display = ProgressDisplay(["a.csv"], stream=stream, show_resources=False)
    display.draw(force=True)
    assert "worker(s) running" not in stream.getvalue()


def test_the_resource_line_appears_above_the_bars():
    stream = FakeTerminal()
    display = ProgressDisplay(["a.csv"], stream=stream, show_resources=True,
                              workers=3)
    display.draw(force=True)

    lines = visible_lines(stream.getvalue())
    assert "3 worker(s)" in lines[0]
    assert "a.csv" in lines[1]


def test_a_process_that_has_gone_is_skipped_not_raised():
    """A worker can exit between being listed and being read."""
    from progress import ResourceMonitor, process_tree

    assert process_tree(999_999_999) == []
    assert ResourceMonitor(999_999_999).sample()["processes"] == 0


def test_a_sample_counts_memory_across_the_tree():
    from progress import ResourceMonitor

    reading = ResourceMonitor().sample()
    assert reading["processes"] >= 1
    assert reading["memory_bytes"] > 0


def test_the_tree_reaches_grandchildren_not_just_children():
    """Measured case: walking one level reported 137 MB for a run using 740."""
    import os
    import subprocess
    import sys
    import time

    from progress import process_tree

    # Mirrors main -> forkserver -> worker.
    grandparent = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess,sys,time;"
         "subprocess.Popen([sys.executable,'-c','import time;time.sleep(5)']);"
         "time.sleep(5)"]
    )
    try:
        time.sleep(1.5)
        found = [process.pid for process in process_tree(os.getpid())]

        assert grandparent.pid in found, "did not find the direct child"
        assert len(found) >= 3, f"grandchild missing; tree was {found}"
    finally:
        grandparent.kill()
        grandparent.wait()


# --- more files than screen rows ---
# The cursor cannot move above the screen top, so a taller frame scrolls on every redraw.

def many_files(count=1226):
    """Return count compressed CSV names."""
    return [f"syslog-2024-{number:04d}.csv.gz" for number in range(count)]


@pytest.fixture
def short_terminal(monkeypatch):
    """Patch the terminal size to 100x24 and return the row count."""
    import progress
    size = os.terminal_size((100, 24))
    monkeypatch.setattr(progress.shutil, "get_terminal_size", lambda _fallback=None: size)
    return 24


def test_a_frame_never_grows_taller_than_the_terminal(short_terminal):
    names = many_files()
    stream = FakeTerminal()
    display = ProgressDisplay(names, stream=stream, show_resources=False, workers=250)
    display.draw(force=True)

    assert stream.getvalue().count("\n") <= short_terminal


def test_a_tall_frame_stays_bounded_as_the_run_progresses(short_terminal):
    names = many_files()
    stream = FakeTerminal()
    display = ProgressDisplay(names, stream=stream, show_resources=False, workers=250)

    for name in names[:400]:
        display.update(name, state="done", rows_read=1000, note="66 cols")
    for name in names[400:650]:
        display.update(name, state="reading", rows_read=500)

    stream.truncate(0)
    stream.seek(0)
    display.draw(force=True)
    assert stream.getvalue().count("\n") <= short_terminal


def test_every_file_is_still_accounted_for_when_most_are_hidden(short_terminal):
    names = many_files()
    stream = FakeTerminal()
    display = ProgressDisplay(names, stream=stream, show_resources=False, workers=250)
    for name in names[:400]:
        display.update(name, state="done", rows_read=1000)
    for name in names[400:650]:
        display.update(name, state="reading", rows_read=500)

    line = display.summary_line()
    assert "1,226 files" in line
    assert "400 done" in line
    assert "250 reading" in line
    assert "576 waiting" in line


def test_a_short_list_is_shown_in_full_as_before(short_terminal):
    stream = FakeTerminal()
    display = ProgressDisplay(["a.csv", "b.csv", "c.csv"], stream=stream,
                              show_resources=False)
    display.draw(force=True)

    written = visible_lines(stream.getvalue())
    assert len(written) == 3
    assert not any("files:" in line for line in written)


def test_one_slow_file_does_not_pin_the_display(short_terminal):
    """Case: file 0 still reading, 1-800 done, 801 onwards live."""
    names = many_files()
    stream = FakeTerminal()
    display = ProgressDisplay(names, stream=stream, show_resources=False, workers=250)

    display.update(names[0], state="reading", rows_read=9_000_000)
    for name in names[1:801]:
        display.update(name, state="done", rows_read=1000)
    for name in names[801:1051]:
        display.update(name, state="reading", rows_read=500)

    stream.truncate(0)
    stream.seek(0)
    display.draw(force=True)
    written = stream.getvalue()

    assert names[0] in written
    assert names[801] in written
    assert written.count("\n") <= short_terminal


def test_finishing_files_are_shown_when_there_is_room(short_terminal):
    """With few workers, recent completions fill the spare rows."""
    names = many_files()
    stream = FakeTerminal()
    display = ProgressDisplay(names, stream=stream, show_resources=False, workers=8)
    for name in names[:1200]:
        display.update(name, state="done", rows_read=1000, note="66 cols")
    for name in names[1200:1206]:
        display.update(name, state="reading", rows_read=77)

    stream.truncate(0)
    stream.seek(0)
    display.draw(force=True)
    written = stream.getvalue()

    assert names[1200] in written
    assert names[1199] in written          # finished most recently
    assert "done" in written
    assert written.count("\n") <= short_terminal


def test_a_file_records_when_it_finished():
    display = ProgressDisplay(["a.csv"], show_resources=False)
    assert display.by_name["a.csv"].finished_at is None
    display.update("a.csv", state="done")
    assert display.by_name["a.csv"].finished_at is not None


def test_the_resource_line_is_not_read_on_the_redraw_clock():
    """Walking every worker's /proc on each redraw takes time from reading."""
    from progress import REDRAW_SECONDS, RESOURCE_SECONDS
    assert RESOURCE_SECONDS > REDRAW_SECONDS
    assert RESOURCE_SECONDS >= 5.0


# --- terminal width ---
# A wrapped line makes the cursor-up redraw land short and leave a stale frame behind.

LONG_NAMES = ["ANL-ALCF-MACHINESTATUS-THETAGPU_20200922_20201231.csv",
              "aurora_crayex_telemetry_power_2024-05-02.csv",
              "ANL-ALCF-DJC-POLARIS_20220809_20221231.csv"]


def terminal_of(monkeypatch, columns, rows=24):
    """Patch the terminal size to columns x rows."""
    import progress
    size = os.terminal_size((columns, rows))
    monkeypatch.setattr(progress.shutil, "get_terminal_size", lambda _fallback=None: size)


def busy_display(stream, workers=250):
    """Return a display of LONG_NAMES with one file reading and one done."""
    display = ProgressDisplay(LONG_NAMES, stream=stream, show_resources=True, workers=workers)
    display.update(LONG_NAMES[0], state="reading", rows_read=123_456_789,
                   estimated_rows=500_000_000)
    display.update(LONG_NAMES[1], state="done", rows_read=987_654_321, seconds=3725.0,
                   note="266 cols, 190 constant, check 12, and a long note besides")
    return display


@pytest.mark.parametrize("columns", [60, 80, 100, 120, 160])
def test_no_line_is_wider_than_the_terminal(monkeypatch, columns):
    """Measured case: at 80 columns a reading line was 89 chars, a finished line 121."""
    terminal_of(monkeypatch, columns)
    stream = FakeTerminal()
    busy_display(stream).draw(force=True)

    for line in visible_lines(stream.getvalue()):
        assert len(line) <= columns - 1, (columns, len(line), line)


def test_the_bar_and_share_survive_in_a_narrow_terminal(monkeypatch):
    """Names are cut first."""
    terminal_of(monkeypatch, 80)
    stream = FakeTerminal()
    busy_display(stream).draw(force=True)

    reading = next(line for line in visible_lines(stream.getvalue()) if "[1/3]" in line)
    assert "25%" in reading and "123,456,789 rows" in reading


def test_a_finished_line_keeps_its_time_in_a_narrow_terminal(monkeypatch):
    """Only the note may be cut."""
    terminal_of(monkeypatch, 80)
    stream = FakeTerminal()
    busy_display(stream).draw(force=True)

    finished = next(line for line in visible_lines(stream.getvalue()) if "[2/3]" in line)
    assert "987,654,321 rows" in finished
    assert "1h" in finished                      # 3,725 seconds


@pytest.mark.parametrize("columns", [80, 100, 120, 140, 160, 200])
def test_a_note_is_cut_readably_or_left_out(monkeypatch, columns):
    """Never a stub such as "4s…", which reads as part of the time."""
    terminal_of(monkeypatch, columns)
    stream = FakeTerminal()
    busy_display(stream).draw(force=True)

    finished = next(line for line in visible_lines(stream.getvalue()) if "[2/3]" in line)
    note = finished.split("1h02m", 1)[1]
    assert finished.count("1h02m") == 1
    assert note == "" or note.startswith("  ")
    shown = note.strip().rstrip("…")
    assert shown == "" or len(shown) >= 7 or "…" not in note


def test_fit_line_prefers_whole_fields():
    from progress import fit_line
    line = "  [1/3] a.csv  [####]   done   250,000 rows      4s  20 cols, 3 constant"
    at_time = line.index("4s") + 2
    assert fit_line(line, at_time) == line[:at_time]
    assert fit_line(line, at_time + 3).endswith("4s")                # stub dropped
    assert fit_line(line, at_time + 12) == line[:at_time + 11] + "…"
    assert fit_line(line, len(line)) == line
    # A single space is inside the note, so the cut is still marked.
    inside = line.index("3 constant") - 1
    assert fit_line(line, inside).endswith("…")


def test_a_redraw_never_blanks_the_frame_before_writing_it(monkeypatch):
    """Clearing first, then writing, showed an empty screen for a moment."""
    terminal_of(monkeypatch, 100)
    stream = FakeTerminal()
    display = busy_display(stream)
    display.draw(force=True)
    stream.truncate(0)
    stream.seek(0)
    display.draw(force=True)

    frame = stream.getvalue()
    assert frame.index("\x1b[J") > frame.rindex("\n")
    assert frame.count("\x1b[K") == len(visible_lines(frame)) + 1   # each line plus the blank


def test_a_narrowed_window_is_fitted_on_the_next_frame(monkeypatch):
    terminal_of(monkeypatch, 160)
    stream = FakeTerminal()
    display = busy_display(stream)
    display.draw(force=True)

    terminal_of(monkeypatch, 70)
    stream.truncate(0)
    stream.seek(0)
    display.draw(force=True)
    assert max(len(line) for line in visible_lines(stream.getvalue())) <= 69


def test_a_real_terminal_shows_each_file_once_after_many_frames(monkeypatch):
    """Run through the pyte terminal emulator."""
    pyte = pytest.importorskip("pyte")
    columns, rows = 80, 24
    terminal_of(monkeypatch, columns, rows)
    screen = pyte.Screen(columns, rows)
    feed = pyte.Stream(screen)

    stream = FakeTerminal()
    display = busy_display(stream)
    for step in range(40):
        display.update(LONG_NAMES[0], rows_read=step * 10_000_000)
        display.update(LONG_NAMES[2], state="reading", rows_read=step * 1_000,
                       estimated_rows=100_000)
        stream.truncate(0)
        stream.seek(0)
        display.draw(force=True)
        feed.feed(stream.getvalue().replace("\n", "\r\n"))

    shown = "\n".join(screen.display)
    for index in (1, 2, 3):
        assert shown.count(f"[{index}/3]") == 1, shown
    assert shown.count("worker(s)") == 1
