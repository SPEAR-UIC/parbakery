# parbake

Measures a directory of CSV files and writes down what it found.

Parbake reads each file, measures the columns, and leaves behind a report a
human can read and a par-baked Croissant a machine can validate. It measures
and reports; it never interprets. Field meanings, types, licences, and caveats
are not in scope because none of that can be measured. The
[bakery](../bakery/) finishes what parbake starts.

## Install

Python 3.8 or newer (enforced by `settings.py`; developed on 3.12). No
virtualenv ships with this directory, so run the modules with whatever
`python3` has the dependencies. Used together with the bakery, the pair needs
Python 3.11 or newer: bakery sets that floor and imports parbake into its own
process.

```
pip install numpy pandas psutil
```

`psutil` is used only by the progress display. `describe_csv.py` needs
`pandas` and `numpy`, and nothing else. `croissant_to_md.py` and
`parsing_tools/pod_logs.py` use only the standard library.

## Usage

Document a whole directory:

```
python3 parbakery.py /path/to/data
```

That reads the first 1000 rows of each CSV and writes reports to
`./parbake_output`. These files can be hundreds of gigabytes, so a full read
is never the default; ask for one with `--full`.

Common variations:

```
python3 parbakery.py /path/to/data --preview 50000    # read more rows
python3 parbakery.py /path/to/data --full             # read every row
python3 parbakery.py /path/to/data --out reports/     # choose the output dir
python3 parbakery.py /path/to/data --skip-existing    # carry on after a kill
python3 parbakery.py /path/to/data --enrich           # finish with AIDRIN
```

All options:

| option | effect |
|---|---|
| `directory` | positional; what to scan (default: current directory). Subdirectories are not entered |
| `--preview [N]` | read the first N rows of each file (default 1000) |
| `--full` | read every row of every file |
| `--out DIR` | where reports go (default `./parbake_output`) |
| `--workers N` | files read at once, one process each (default: up to 8, capped by CPU count and file count) |
| `--separator C` | field separator (default `,`) |
| `--batch-size N` | rows read at a time per worker; by default derived from file width so memory stays flat. Lower it if runs get killed |
| `--values-tracked N` | distinct values counted per column before it stops (default 1000; past that, counts show as `N+`) |
| `--values-shown N` | values listed per column in the report (default 10) |
| `--no-identifier-check` | skip the un-anonymised data check |
| `--no-croissant` | skip the Croissant JSON and Markdown outputs |
| `--no-checkpoints` | do not save progress; a killed run starts over |
| `--checkpoint-every N` | rows between progress saves (default 1,000,000) |
| `--checkpoint-dir DIR` | where progress is saved (default `<out>/.parbake_checkpoints`) |
| `--skip-existing` | leave alone any file whose output is already in `--out` |
| `--enrich` | after the pass, run AIDRIN readiness metrics over each CSV (see Enrich) |
| `--enrich-profile FILE` | YAML or JSON naming the column roles AIDRIN needs |
| `--aidrin CMD` | the aidrin command to run (default `aidrin`) |
| `--batch-local-copies` | each worker copies its file to local disk before reading (see Shared filesystems) |
| `--local-copy-dir DIR` | where those copies go (default `$TMPDIR`) |

`python3 parbakery.py --help` prints the same list.

### Flags from a file

Any of these, the directory included, can be kept in a file and read by
putting `@` before its name:

```
python3 parbakery.py @examples/practice.args
python3 parbakery.py /lustre/data @lustre.args --workers 8
```

Each line is split as a shell would split it: a flag and its value can share a
line (`--out reports/`), quotes keep spaces in a value, and `#` starts a
comment. Variables and `~` are not expanded, so write paths out in full;
relative paths are relative to where you run from. When a flag appears twice,
the later one wins, so a flag typed after the file overrides it.
[`examples/practice.args`](examples/practice.args) is a working file to start
from: a short preview of `../copies_of_data` into `practice_output/`, with the
Lustre flags for a full run commented out.

Measure a single file (the measurement core without the directory, worker,
and Croissant layers):

```
python3 describe_csv.py data.csv                  # full read
python3 describe_csv.py data.csv --preview        # first 1000 rows
python3 describe_csv.py data.csv --json out.json  # also emit JSON
```

Flags: `--preview [N]`, `--batch-size`, `--values-tracked`, `--values-shown`,
`--no-identifier-check`, `--separator`, `--json`, `--quiet`.

Two standalone helpers:

```
python3 croissant_to_md.py dataset.croissant.json [out.md | -o out.md]
python3 parsing_tools/pod_logs.py LOG... --out DIR
```

The first renders any Croissant JSON-LD as Markdown (to stdout if no output
is given); it is the same renderer the bakery uses. The second converts
Kubernetes pod logs into a CSV/JSONL table, one row per log record. Point
parbake at the CSV it writes; nothing calls `parbakery.py` for you.

### Extracting one column

Two helpers work the distinct values of a single column. Neither is called by
the pipeline.

```
python3 utilities/unique_extractor.py DIR --column JOB_STATE --out states.csv
python3 utilities/dedupe_values.py states.csv --out states_unique.csv
```

`unique_extractor.py` requires `--column` and `--out`, reads every CSV in the
directory, and writes each file's distinct values, one per row; a value
present in two files is written twice. `dedupe_values.py` keeps the header and
reduces such a file to each value once, sorted. It removes exact repeats only:
it does not merge values that differ in case or whitespace.

## Output

```
<out>/
  DIRECTORY_DOCUMENTATION.txt         index: one summary line per file
  parbaked_txt/<name>.txt             the readable report, one per CSV
  parbaked_croissants/<name>.parbaked.json
  parbaked_markdown/<name>.parbaked.md
  .parbake_checkpoints/               progress; removed when the run completes
```

Only CSVs are measured, but every file in the directory appears in the index,
so nothing is silently dropped. Compressed files (`.gz`, `.bz2`, `.xz`) are
read; `.zip` is not, because picking one member out of an archive is a
judgement call. File matching uses the whole name, so `jobs.csv.gz` counts as
a CSV.

Each report contains: the file line (size, columns, rows read, scope, time);
the identifier check results if anything was flagged; a per-column table of
empty, NA-ish, distinct, shortest/longest, and most-common value with its
share; then grouped sections for no-information columns (empty or constant),
numeric columns (count, non-numerics, min, max), and repeated values.

## Measurements

Everything is read as text; nothing is converted on the way in, so a literal
`NA` stays a string. NA-ish text (`na`, `n/a`, `null`, `nan`, `none`) is
counted separately from truly blank. Numeric range is reported when a column
holds numbers even if a stray word is mixed in, along with how many values
are not numbers. A column is constant when one value covers at least 99% of
rows. A preview and a full read measure the same things, so a preview can be
checked by reading the file fully.

## The par-baked Croissant

Each CSV also gets a par-baked Croissant: the mechanical part only (file
identity, column names, measurements), built to fail
`mlcroissant validate` on purpose. `conformsTo` is set to a nonexistent
version, `PARBAKED-DO-NOT-SUBMIT`, and a banner on the file and every field
says it is machine generated and unreviewed. The validation errors form a
ladder, so each one a person fixes is real work, and the file validates
exactly when that work is done:

```
as generated       2 errors   the bad conformsTo, and the @type it implies
fix conformsTo    17 errors   a missing checksum, and fields with no dataType
add a checksum    16 errors   the fields with no dataType
add dataTypes      0 errors   it validates
```

A finished Croissant is named `.croissant.json`; parbake never writes that
name. Skip these outputs with `--no-croissant`.

## Enrich: AIDRIN readiness measurements

`--enrich` finishes a pass by asking [AIDRIN](https://www.aidrin.eu/) how
ready the documented CSVs are, across quality, impact on AI models, fairness,
and governance/privacy, and adds an `_enrich` block to each par-baked
Croissant, beside `_parbake_measurements`. The block carries a banner (machine
generated, unreviewed) and a warning: the column roles came from a profile
nobody reviewed, and wrong quasi-identifiers produce falsely reassuring
privacy numbers.

```
python3 parbakery.py /path/to/data --enrich
python3 parbakery.py /path/to/data --enrich --enrich-profile FILE
```

Without a profile, only the metrics that need no column roles run: the
zero-argument quality set (completeness, duplicity, outliers) and the
data-structure set (constant-feature-count, max-pairwise-correlation,
skewness, kurtosis). A profile names the roles (target, quasi-
identifiers, sensitive, timestamp, path columns) in YAML or JSON, and
[`examples/enrich_maximal.yaml`](examples/enrich_maximal.yaml) shows the
maximal version for the four areas. Trim it by deleting roles, or list the
groups you want under `groups:`.

parbake never imports AIDRIN; it would drag in Flask, Celery, and a newer
Python than parbake's 3.8-and-pandas floor. Instead parbake writes one batch
config per argument group to `<out>/enrich/<name>/`, runs `aidrin batch` as a
subprocess (set the command with `--aidrin`), and reads the JSON back. A
config is flat, so two metrics that want the same key for different columns
cannot share one file: `columns` means one thing to correlations and another
to representation-rate. Hence the groups, listed in `enrich.py`. Worked
examples of the raw configs, runnable by hand, are in
[`examples/aidrin_configs/`](examples/aidrin_configs/).

AIDRIN reports a failed metric inside its JSON and still exits 0, so every
group's status is read back, never trusted from the exit code. A timeout
(10 minutes per group) or a missing `aidrin` command leaves a status string in
the block and a note on the console, never a crashed pass. A `--skip-existing`
rerun leaves the block as it is; adding `--enrich` re-measures the file and
rewrites it. Either way, the block inside the Croissant is what says whether a
file was ever enriched. One side effect to know about: differential privacy
writes a noisy copy of the data wherever it runs, so each file's groups run
inside its `enrich/` scratch directory, and that copy never lands beside the
reports.

## Interrupted runs

Two mechanisms, for two different situations:

- `--skip-existing` reuses complete outputs from a previous run. It checks
  only that the output exists, not the source, so a file edited since it was
  documented keeps its old description until you reprocess it. Skipped files
  get their index entries back from their par-baked Croissant.
- Checkpoints resume a half-read file. Progress is saved every
  `--checkpoint-every` rows to `<out>/.parbake_checkpoints/`, and a resumed
  read produces numbers identical to an uninterrupted one (the accumulator is
  serialised and continued, not merged). A checkpoint is discarded and the
  file re-read when the checkpoint format, the file's size or mtime, or a
  measurement-affecting setting (separator, values tracked, preview rows) has
  changed. Batch size is not in that list because it cannot change a
  measurement, and lowering it is the normal response to being killed.

## Shared filesystems

On Lustre and similar, opening a file costs a metadata-server round trip, and
a run over hundreds of files spends most of its time waiting.

```
python3 parbakery.py /lustre/data --batch-local-copies
```

Each worker copies its own file to local disk (`$TMPDIR` by default, usually
node-local storage on a compute node), reads the copy, and deletes it before
taking the next file. So there are never more copies, or more readers on the
shared filesystem, than workers. If a copy fails, the file is read in place
and the problem is reported at the end. On a local filesystem this option is
pure loss, which is why it is off by default.

## The identifier check

Looks for un-anonymised data, riding along on the normal read at no extra
cost. It flags columns whose names suggest an identity (`user`, `username`,
`uid`, `name`, `project`, `account`, ...) and columns whose values look like
file paths, since a path can carry a username. An ALCF username contains
letters; an anonymised id is an all-digit hash. So a `username` column of all
digits is reported as consistent with anonymisation, and one with letters
gets flagged, as does any path-like column. The watch list is imperfect:
`MACHINE_NAME` and `QUEUE_NAME` will flag on `name`. This check cannot tell
you a file is safe to share; it tells you where to look. Turn it off with
`--no-identifier-check`.

## Configuration

Constants live in `settings.py` and everything else imports from there. The
ones you might tune: `DEFAULT_PREVIEW_ROWS` (1000), `CELL_BUDGET_PER_BATCH`
(1M cells, since memory is rows times columns), batch bounds (2,000 to
100,000 rows), `DEFAULT_VALUES_TRACKED` (1000), `SINGLE_VALUE_THRESHOLD`
(0.99), `DEFAULT_VALUES_SHOWN` (10), `MINIMUM_LISTING_COVERAGE` (0.10),
`CHECKPOINT_EVERY_ROWS` (1M), `EXACT_ROW_COUNT_LIMIT` (8 MB). Where a value
was chosen by measurement, the comment in `settings.py` says what was
measured; `NOTES.md` has the fuller reasoning.

## Tests

```
cd parbake && pytest
```

About 330 tests. Measurement is tested against small CSVs in a temp directory;
resume is tested by killing reads at several points and comparing against
straight reads; the Croissant is tested by rendering it and, where
`mlcroissant` is installed, by checking the validation ladder; enrichment is
tested against a stub `aidrin` script, never the real one.

## Modules

| file | role |
|---|---|
| `parbakery.py` | scan a directory, describe each CSV, write the index; the command line |
| `workers.py` | one worker process per file; its queue messages feed the parent's display |
| `previous_run.py` | `--skip-existing`: what a previous run left, read back |
| `enrich.py` | `--enrich`: AIDRIN configs, runs, and the `_enrich` block |
| `arguments.py` | flags shared by the tools, and `@` files |
| `describe_csv.py` | measure one CSV; standalone entry point |
| `measuring.py` | `ColumnSummary`, the per-column accumulators |
| `identifiers.py` | the un-anonymised data check |
| `sources.py` | file discovery, compression, row estimates |
| `checkpoints.py` | saving progress so a killed run can resume |
| `parbaked_croissant.py` | build the par-baked Croissant |
| `croissant_to_md.py` | render a Croissant as Markdown |
| `reporting.py` | per-file report, index, and number/size/duration formatting |
| `progress.py` | terminal display for a parallel run, with memory and CPU (psutil) |
| `settings.py` | every constant, plus the Python version check |
| `parsing_tools/pod_logs.py` | Kubernetes pod logs to a table |
| `utilities/unique_extractor.py` | distinct values of one column across a directory; `dedupe_values.py` reduces them |
| `skills/reading-croissant-datasets/` | instructions for reading a Croissant with its data |
| `skills/using-alcf-croissants/` | instructions for analyzing an ALCF dataset through its card |

`docs/` holds pipeline diagrams and a pointer to the design notes. `NOTES.md`
is the reasoning: why it works this way, what was measured to decide that,
and what was left out on purpose.

Uncommitted state (see `.gitignore`): `__pycache__/`, `.pytest_cache/`,
`parbake_output/`, `practice_output/`.
