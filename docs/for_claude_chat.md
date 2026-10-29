Here is the architecture of parbake, a Python tool. Use it as context.

parbake measures every CSV file directly inside a directory and writes, per
file: a measurement report, a par-baked Croissant metadata file (built to fail
validation until a person adds data types, a checksum and the correct
conformsTo), a Markdown rendering of it, and a directory index. It records no
judgements: no field meanings, types, licence or caveats.

Run: python3 parbakery.py <directory> [--full] [--out DIR] [--workers N]

Reading: CSV, plain or .gz/.bz2/.xz. First 1,000 rows by default, --full for
all. Values read as text. Batches of about 1,000,000 cells. Per column: rows,
empty, NA-like text, distinct values (up to 1,000 tracked) and counts, value
lengths, numeric count and min/max, all-empty and single-value flags. An
anonymisation check flags name-matched columns holding letters, and path-like
values.

Scaling: one worker process per file (default up to 8). A file is never split
across workers. --batch-local-copies copies each file to local disk first.

Fault tolerance: a failed file or dead worker is recorded and the run
continues. Checkpoints every 1,000,000 rows; resumed only if format version,
file size, mtime and measurement settings match; deleted when the file
finishes. --skip-existing skips files whose outputs already exist.

Modules:
  parbakery.py          command line; describes each CSV; builds the index
  workers.py            worker count, pool, progress messages (stdlib only)
  staging.py            per-worker local copies (--batch-local-copies)
  previous_run.py       --skip-existing, rebuilds index entries from outputs
  arguments.py          shared flags and @ files
  describe_csv.py       measures one CSV: batches, totals, checkpoints
  measuring.py          ColumnSummary, per-column running totals
  identifiers.py        IdentifierCheck, the anonymisation check
  sources.py            finds files; compression, batch size, row estimates
  checkpoints.py        CheckpointStore: save, validate, load, discard
  parbaked_croissant.py builds the par-baked Croissant and its Markdown
  reporting.py          per-file report, directory index, number formatting
  progress.py           per-file live display, memory and CPU (psutil)
  settings.py           every constant
  croissant_to_md.py    standalone: any Croissant JSON to Markdown
  parsing_tools/pod_logs.py  standalone: Kubernetes pod logs to CSV

```mermaid
flowchart LR
  dir["data directory<br/>*.csv, .gz, .bz2, .xz"] --> pk

  subgraph parent["parent process"]
    pk["parbakery.py"] --> ad["previous_run<br/>--skip-existing"]
    ad --> pool["worker_count + pool<br/>one process per file"]
    prog["progress<br/>live display"]
    idx["reporting.build_index"]
  end

  subgraph worker["worker process, one per file"]
    stg["staging<br/>--batch-local-copies"] --> load
    subgraph dc["describe_csv.py"]
      load["checkpoints.load"] --> read["sources + pandas.read_csv<br/>next batch, as text"]
      read --> meas["measuring + identifiers<br/>update column totals"]
      meas --> save["checkpoints.save<br/>every 1,000,000 rows"]
      save -->|loop| read
      save -->|end of file| disc["checkpoints.discard"]
    end
    disc --> rep["reporting.print_report"]
    rep --> pc["parbaked_croissant"]
  end

  pool -->|one job per file| stg
  worker -.->|progress over a queue| prog
  pool -->|after the last file| idx

  ck[(".parbake_checkpoints/")] -.-> load
  save -.-> ck
  rep --> txt["parbaked_txt/*.txt"]
  pc --> js["parbaked_croissants/*.parbaked.json"]
  pc --> md["parbaked_markdown/*.parbaked.md"]
  idx --> ix["DIRECTORY_DOCUMENTATION.txt"]
```
