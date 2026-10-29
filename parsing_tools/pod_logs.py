#!/usr/bin/env python3
"""parbake/parsing_tools/pod_logs.py - parse Kubernetes pod logs into a table

    python3 pod_logs.py export.txt                  # export.parsed.jsonl and .csv
    python3 pod_logs.py exports/ a.txt --out parsed/ --workers 4
One row per record: runtime fields, pod metadata, parts of the application log line.
Application log line shapes: logging, json, logfmt, text. Values stay as written.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from arguments import add_workers  # noqa: E402  (standard library only)
from workers import run_pool, send, worker_count  # noqa: E402

# A line number from cat -n or grep -n, only when a record follows it.
LINE_NUMBER = re.compile(r"^[ \t]*\d+(?:\t|:| +)(?=\{)")


# --- records ---

@dataclass
class Record:
    """One input line: its number, text, parsed fields and problems."""

    line: int
    text: str
    fields: Optional[dict] = None
    partials_joined: int = 0
    problems: List[str] = field(default_factory=list)

    def problem(self, code, detail=""):
        """Record a problem as "code: detail"."""
        self.problems.append(f"{code}: {detail}" if detail else code)


def read_records(lines: Iterable[str]) -> Iterator[Record]:
    """Yield one Record per non-blank line, with any copied line number removed."""
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r\n")
        if line.strip():
            yield Record(number, LINE_NUMBER.sub("", line, count=1))


def parse_outer(record: Record) -> None:
    """Parse the record's text as a JSON object into fields; numbers stay as text."""
    try:
        value = json.loads(record.text, parse_float=str, parse_int=str)
    except json.JSONDecodeError as problem:
        record.problem("not-json", f"{problem.msg} at character {problem.pos}")
        return
    if not isinstance(value, dict):
        record.problem("not-json", "valid JSON, but not an object")
        return
    record.fields = value


# --- fragments ---

def join_partials(records: Iterable[Record]) -> Iterator[Record]:
    """Yield records with runtime-split lines joined: _p P parts, then the F record.
    Parts are held per source and stream, since containers interleave."""
    pending: Dict[tuple, List[Record]] = {}
    for record in records:
        fields = record.fields
        if fields is None or "_p" not in fields:
            yield record
            continue
        source = (fields.get("filename") or fields.get("pod"),
                  fields.get("container"), fields.get("stream"))
        if fields.get("_p") == "P":
            pending.setdefault(source, []).append(record)
            continue
        parts = pending.pop(source, [])
        if parts:
            fields["log"] = "".join(str(part.fields.get("log", "")) for part in parts) \
                + str(fields.get("log", ""))
            record.partials_joined = len(parts)
            record.line = parts[0].line
        yield record
    for parts in pending.values():
        for part in parts:
            part.problem("partial-unfinished",
                         "a fragment with no final record after it; kept as found")
            yield part


# --- the application's own line ---

LOGGING_LINE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)\s+-\s+(?P<level>[A-Za-z]+)\s+-\s+(?P<rest>.*)$",
    re.DOTALL)
CONTEXT_START = re.compile(r"\s+-\s+\{")
LOGFMT_PAIR = re.compile(r'([A-Za-z_][\w.\-]*)=("(?:[^"\\]|\\.)*"|\S*)')

# Keys a structured line commonly uses for its time, level and message.
STANDARD_KEYS = (
    ("log.time", ("time", "timestamp", "ts", "@timestamp")),
    ("log.level", ("level", "severity", "lvl")),
    ("log.message", ("msg", "message", "event")),
)


def standard_fields(mapping, convert=str):
    """Return log.time, log.level and log.message from the first STANDARD_KEYS key present."""
    fields = {}
    for name, keys in STANDARD_KEYS:
        value = next((mapping[key] for key in keys if key in mapping), None)
        if value is not None:
            fields[name] = convert(value)
    return fields


def parse_log(line: str) -> Tuple[Dict[str, str], Optional[dict], str]:
    """Return (log.* fields, JSON context or None, shape) for one application log line."""
    # logging   2026-08-13 23:59:54  -  INFO  -  message  -  {context}
    # json      the whole line is a JSON object
    # logfmt    level=info msg="message" key=value
    # text      anything else, kept whole as the message
    stripped = line.strip()
    found = LOGGING_LINE.match(stripped)
    if found:
        message, context = _split_context(found.group("rest"))
        fields = {"log.time": found.group("time"), "log.level": found.group("level"),
                  "log.message": message}
        fields.update(message_fields(message))
        return fields, context, "logging"

    if stripped.startswith("{"):
        try:
            value = json.loads(stripped, parse_float=str, parse_int=str)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return standard_fields(value, text_of), value, "json"

    pairs = LOGFMT_PAIR.findall(stripped)
    if len(pairs) >= 2 and _mostly_pairs(stripped):
        values = {key: _unquote(value) for key, value in pairs}
        fields = {f"log.{key}": value for key, value in values.items()}
        fields.update(standard_fields(values))
        return fields, None, "logfmt"

    return {"log.message": stripped}, None, "text"


def _split_context(rest):
    """Return (message, context): rest with a trailing JSON object split off."""
    # From the left: the first " - {" that parses to the end is the outermost.
    for found in CONTEXT_START.finditer(rest):
        start = found.end() - 1
        try:
            value = json.loads(rest[start:], parse_float=str, parse_int=str)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return rest[:found.start()].strip(), value
    return rest.strip(), None


def _mostly_pairs(text):
    """Return whether key=value pairs make up at least 80% of text."""
    residue = LOGFMT_PAIR.sub("", text)
    return len(residue.strip()) <= len(text) * 0.2


def _unquote(value):
    """Return a logfmt value without its quotes, escapes decoded."""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    return value


def message_fields(message: str) -> Dict[str, str]:
    """Return log.<key> fields from a pipe-separated message; parts without "=" form log.event."""
    # "/api/usage:save called | userName=a@b | ModelId=x| InputToken=2970"
    if "|" not in message or "=" not in message:
        return {}
    fields, event = {}, []
    for part in message.split("|"):
        part = part.strip()
        if not part:
            continue
        key, separator, value = part.partition("=")
        key = key.strip()
        if separator and key and not any(character.isspace() for character in key):
            fields.setdefault(f"log.{key}", value.strip())
        else:
            event.append(part)
    if event:
        fields["log.event"] = " | ".join(event)
    return fields


# --- one output row ---

def text_of(value) -> str:
    """Return a value as CSV text, not reinterpreted."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def row_for(record: Record) -> Tuple[Dict[str, str], str]:
    """Return (row, log line shape) for one record."""
    # Context keys join the outer namespace; a disagreeing one is kept as context.<key>.
    row: Dict[str, str] = {}
    shape = ""
    if record.fields is None:
        row["parse.raw"] = record.text
    else:
        for key, value in record.fields.items():
            row[key] = text_of(value)
        log = record.fields.get("log")
        if isinstance(log, str):
            parsed, context, shape = parse_log(log)
            for key, value in parsed.items():
                row.setdefault(key, value)
            for key, value in (context or {}).items():
                value = text_of(value)
                if key not in row:
                    row[key] = value
                elif row[key] != value:
                    row[f"context.{key}"] = value
                    record.problem("conflict",
                                   f"{key} is {row[key]!r} outside the log line "
                                   f"and {value!r} inside it")

    row["parse.line"] = str(record.line)
    row["parse.log_shape"] = shape
    row["parse.partials_joined"] = str(record.partials_joined)
    row["parse.problems"] = "; ".join(record.problems)
    return row, shape


# --- progress ---

class Progress:
    """One progress bar on stderr, in bytes read, drawn only to a terminal."""
    # Not parbake's display: parsing one file needs only the standard library.

    WIDTH = 20
    REDRAW_SECONDS = 0.2

    def __init__(self, total_bytes, stream=None):
        """Start at zero bytes; draw only if stream is a terminal."""
        self.total = total_bytes
        self.done = 0
        self.records = 0
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = hasattr(self.stream, "isatty") and self.stream.isatty()
        self.last_drawn = 0.0

    def line(self):
        """Return the bar line."""
        fraction = min(1.0, self.done / self.total) if self.total else 1.0
        filled = int(fraction * self.WIDTH)
        return (f"[{'#' * filled}{'-' * (self.WIDTH - filled)}] "
                f"{fraction:4.0%}   {self.records:,} records")

    def advance(self, byte_count, records=0):
        """Add bytes and records; redraw at most every REDRAW_SECONDS."""
        self.done += byte_count
        self.records += records
        now = time.monotonic()
        if self.enabled and now - self.last_drawn >= self.REDRAW_SECONDS:
            self.last_drawn = now
            self.stream.write("\r" + self.line())
            self.stream.flush()

    def finish(self):
        """Draw the full bar and end the line."""
        if self.enabled:
            self.done = self.total
            self.stream.write("\r" + self.line() + "\n")
            self.stream.flush()


class QueueProgress:
    """Progress from a worker, sent as workers.py messages."""

    def __init__(self, name, total_bytes, messages):
        """Start at zero and send the start message."""
        self.name = name
        self.total = total_bytes
        self.done = 0
        self.records = 0
        self.messages = messages
        self.started_at = time.monotonic()
        self.last_sent = 0.0
        send(messages, name, "start")

    def advance(self, byte_count, records=0):
        """Add bytes and records; send an estimate and progress at most every REDRAW_SECONDS."""
        self.done += byte_count
        self.records += records
        now = time.monotonic()
        if now - self.last_sent >= Progress.REDRAW_SECONDS and self.done and self.records:
            self.last_sent = now
            # The display counts rows: scale records by bytes read to estimate total rows.
            estimate = max(self.records, int(self.records * self.total / self.done))
            send(self.messages, self.name, "estimate", estimate)
            send(self.messages, self.name, "progress", self.records, now - self.started_at)

    def finish(self):
        """Do nothing: the worker sends the finished message."""
        pass


def counted_lines(handle, progress):
    """Yield decoded lines, advancing progress by each line's byte size."""
    # Bytes: a text-mode file cannot report its position while iterated.
    for raw in handle:
        progress.advance(len(raw))
        yield raw.decode("utf-8", errors="replace")


# --- the whole file ---

@dataclass
class Summary:
    """Counts for one parsed file, or totals for several."""

    source: Path
    records: int = 0
    partials: int = 0
    shapes: Counter = field(default_factory=Counter)
    levels: Counter = field(default_factory=Counter)
    sources: Counter = field(default_factory=Counter)
    events: Counter = field(default_factory=Counter)
    problems: Counter = field(default_factory=Counter)
    fields: Counter = field(default_factory=Counter)
    written: List[Path] = field(default_factory=list)

    def add(self, other):
        """Add another summary's counts to this one."""
        self.records += other.records
        self.partials += other.partials
        for name in ("shapes", "levels", "sources", "events", "problems", "fields"):
            getattr(self, name).update(getattr(other, name))

    def one_line(self):
        """Return records, problems and the CSV path on one line."""
        problems = sum(self.problems.values())
        csv_path = self.written[-1] if self.written else ""
        return (f"{self.source.name}: {self.records:,} records, "
                f"{problems:,} problem(s) -> {csv_path}")

    def lines(self):
        """Return the full summary as lines."""
        out = [f"{self.source}",
               f"  records              {self.records}"]
        if self.partials:
            out.append(f"  fragments joined     {self.partials}")
        out.append("  log line shapes      " + _counts(self.shapes))
        if self.levels:
            out.append("  levels               " + _counts(self.levels))
        out.append("  sources              " + _counts(self.sources, 6))
        if self.events:
            out.append("  commonest events     " + _counts(self.events, 8))
        out.append("  problems             " + (_counts(self.problems) if self.problems
                                                 else "none"))
        out.append(f"  fields               {len(self.fields)}")
        for name, count in sorted(self.fields.items(), key=lambda item: (-item[1], item[0])):
            out.append(f"      {count:>8}  {name}")
        for path in self.written:
            out.append(f"  written              {path}")
        return out


def _counts(counter, limit=None):
    """Return the limit commonest "name count" pairs, comma-separated."""
    shown = counter.most_common(limit)
    text = ", ".join(f"{name or '(none)'} {count}" for name, count in shown)
    if limit and len(counter) > limit:
        text += f", and {len(counter) - limit} more"
    return text or "none"


def parse_file(source: Path, out_directory: Path, progress_stream=None,
               progress=None) -> Summary:
    """Parse one export into <stem>.parsed.jsonl and <stem>.parsed.csv; return the Summary."""
    # JSONL first while collecting columns, then CSV from it: memory holds only the column list.
    summary = Summary(source=source)
    out_directory.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_directory / f"{source.stem}.parsed.jsonl"
    csv_path = out_directory / f"{source.stem}.parsed.csv"
    columns: Dict[str, None] = {}

    def parsed(records):
        """Yield each record after parse_outer()."""
        for record in records:
            parse_outer(record)
            yield record

    if progress is None:
        progress = Progress(source.stat().st_size, progress_stream)
    with open(source, "rb") as handle, open(jsonl_path, "w", encoding="utf-8") as jsonl:
        lines = counted_lines(handle, progress)
        for record in join_partials(parsed(read_records(lines))):
            row, shape = row_for(record)
            jsonl.write(json.dumps(row, ensure_ascii=False) + "\n")
            _tally(summary, record, row, shape)
            progress.advance(0, records=1)
            for name in row:
                columns.setdefault(name, None)
    progress.finish()

    ordered = [name for name in columns if not name.startswith("parse.")] + \
              [name for name in columns if name.startswith("parse.")]
    with open(jsonl_path, encoding="utf-8") as jsonl, \
            open(csv_path, "w", encoding="utf-8", newline="") as table:
        writer = csv.DictWriter(table, fieldnames=ordered, restval="")
        writer.writeheader()
        for line in jsonl:
            writer.writerow(json.loads(line))

    summary.written = [jsonl_path, csv_path]
    return summary


def _tally(summary, record, row, shape):
    """Add one record's row to the summary counts."""
    summary.records += 1
    summary.partials += record.partials_joined
    summary.shapes[shape or "not parsed"] += 1
    if row.get("log.level"):
        summary.levels[row["log.level"]] += 1
    source = "/".join(part for part in (row.get("namespace"), row.get("container")) if part)
    summary.sources[source] += 1
    event = row.get("log.event") or row.get("log.message")
    if event:
        summary.events[event[:80]] += 1
    for problem in record.problems:
        summary.problems[problem.split(":", 1)[0]] += 1
    for name in row:
        if not name.startswith("parse.") and row[name] != "":
            summary.fields[name] += 1


# This tool's output, skipped so a second run does not parse the first.
OUTPUT_SUFFIXES = (".parsed.jsonl", ".parsed.csv")


def looks_like_records(path: Path) -> bool:
    """Return whether the first non-blank line is a JSON record; read no further."""
    try:
        with open(path, "rb") as handle:
            for raw in handle:
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    return LINE_NUMBER.sub("", line, count=1).startswith("{")
    except OSError:
        return False
    return False


def find_sources(paths: Iterable[Path]) -> Tuple[List[Path], List[Tuple[Path, str]]]:
    """Return (files to parse, [(skipped, why)]). A named file is always parsed.
    A directory gives its record files, sorted; not hidden, output or subdirectories."""
    sources, skipped = [], []
    for path in paths:
        if path.is_file():
            sources.append(path)
        elif path.is_dir():
            for entry in sorted(path.iterdir()):
                if not entry.is_file():
                    continue
                if entry.name.startswith("."):
                    continue
                if entry.name.endswith(OUTPUT_SUFFIXES):
                    continue
                if looks_like_records(entry):
                    sources.append(entry)
                else:
                    skipped.append((entry, "its first line is not a JSON record"))
        else:
            skipped.append((path, "no such file or directory"))
    return sources, skipped


def _parse_in_worker(job, messages):
    """Parse one file in a worker, reporting progress on messages; return the Summary."""
    source, out_directory = job
    started = time.monotonic()
    progress = QueueProgress(source.name, source.stat().st_size, messages)
    summary = parse_file(source, out_directory, progress=progress)
    send(messages, source.name, "finished", {
        "state": "done", "rows_read": summary.records,
        "seconds": time.monotonic() - started,
        "note": f"{sum(summary.problems.values()):,} problem(s)"})
    return summary


def parse_in_parallel(jobs: List[Tuple[Path, Path]], workers=None) -> List[Summary]:
    """Parse files in parallel, one worker per file; return the Summaries in job order.
    An exception in any worker ends the run."""
    from progress import ProgressDisplay     # psutil; not needed for one file

    count = worker_count(workers, len(jobs))
    display = ProgressDisplay([source.name for source, _ in jobs], workers=count)
    futures = run_pool(_parse_in_worker, jobs, count, display)
    display.finish()
    return [future.result() for future in futures]


def main(argv=None):
    """Parse arguments, find sources, parse them, print the summaries."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sources", nargs="+", type=Path,
                        help="exports, or directories of them")
    parser.add_argument("--out", type=Path,
                        help="directory for the output (default: beside each source)")
    add_workers(parser)
    arguments = parser.parse_args(argv)

    sources, skipped = find_sources(arguments.sources)
    for path, why in skipped:
        print(f"skipped {path}: {why}", file=sys.stderr)
    if not sources:
        print("nothing to parse", file=sys.stderr)
        return

    if len(sources) == 1:
        summary = parse_file(sources[0], arguments.out or sources[0].parent)
        print("\n".join(summary.lines()))
        return

    jobs, claimed = [], {}
    for source in sources:
        destination = (arguments.out or source.parent) / f"{source.stem}.parsed.csv"
        if destination in claimed:
            print(f"skipped {source}: its output would overwrite that of "
                  f"{claimed[destination]}", file=sys.stderr)
            continue
        claimed[destination] = source
        jobs.append((source, arguments.out or source.parent))

    summaries = parse_in_parallel(jobs, arguments.workers)

    total = Summary(source=Path(f"{len(summaries)} files"))
    for summary in summaries:
        print(summary.one_line())
        total.add(summary)
    print()
    print("\n".join(total.lines()))


if __name__ == "__main__":
    main()
