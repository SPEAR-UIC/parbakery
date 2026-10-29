---
name: using-alcf-croissants
description: Work correctly with an ALCF dataset by reading its Croissant
  data card first. Use whenever you analyze, query, plot, summarize, join, or
  write a notebook about an ALCF dataset (job accounting, telemetry, logs) that
  has a .baked.json, .baked.md, .baked.txt, .parbaked.json, or .croissant.json
  beside it or naming it. Finds the right file, decodes coded columns from the
  card rather than from priors, computes instead of estimating, and carries the
  card's limitations, privacy notes, license, and citation into every
  deliverable.
---

# Using an ALCF dataset through its Croissant card

An ALCF data card is machine-readable documentation for one dataset: the
canonical artifact is a Croissant JSON-LD file, with Markdown and text
renderings generated from it for people. The card exists because ALCF data is
misleading read raw: coded columns, per-machine conventions, silently biased
populations, and anonymization that changes what a group count means.

This skill assumes you already know whether the card is reviewed or par-baked.
If you do not, run the `reading-croissant-datasets` skill first. A par-baked
card has no meanings, units, decodings, license, or citation in it, and
nothing here replaces that gate.

Everything below assumes you can also run code against the data. Do that. Do
not estimate a count you could measure.

---

## Step 1 -- Locate the card before touching the data

Match the data file to its card by name. ALCF dataset files are named:

```
ANL-ALCF-<FAMILY>-<MACHINE>_<YYYYMMDD>_<YYYYMMDD>.csv[.gz]
```

with variants that replace the family prefix (for example
`aurora_crayex_telemetry_power_2024-05-02.csv`). The card carries the same
stem. Look for, in order of authority:

| File | What it is |
|---|---|
| `<stem>.baked.json` | the canonical reviewed Croissant (bakery output) |
| `<stem>.croissant.json` | a published or hand-finalized Croissant |
| `<stem>.baked.md` / `.baked.txt` | renderings of the JSON, for humans |
| `<stem>.parbaked.json` | machine-generated, unreviewed. Not a citation |

If the JSON and a rendering disagree, trust the JSON; the renderings are
generated from it. If there is no card at all, say so and treat every column
meaning as unknown -- you are doing data_only work, and any answer that needs
semantics should say it lacks them.

## Answer from the card before touching the data

Try answering from the cards alone before opening the datasets at all: it
saves tokens and context, and the card is the faster source for anything
it states. If the card exists and the query is about what is in a file --
which columns it has, what a column means, its units or coded values, the
date range or row counts the card's scope block states -- read the answer
off the card. When several datasets are in play, locate the right one by
scanning the cards' field lists, not by opening every CSV looking for a
column header. That is what the card is for, and it costs one file open. Do not spend
commands and tokens analyzing the dataset to rediscover a fact the card
already states. Go to the data only when the card is silent on the
question, marks the column `UNDECODED:` or placeholder, or the answer
depends on the values themselves (a count you must measure, a
distribution, the latest record). The rule is: never compute what the
card says; never estimate what the data can measure.

## Step 2 -- Choose the right file of the dataset

A dataset is usually many files, one per date range. The card's
`distribution` block describes the family: a `cr:FileSet` whose `description`
gives the naming pattern (`Naming: ...`) and whose `includes` gives the glob.
Then:

- The dates in a filename are a selection rule, not a content bound. One DJC
  card states the file labeled 20220809 holds records from 2022-07-31 because
  jobs are selected by end time. Read `rai:dataLimitations` for the rule that
  applies, and verify against real timestamps before trusting a name.
- Machine generations differ: scheduler, node shape, exit-code conventions,
  and which columns are live. The MACHINE token and the card's machine notes
  tell you whose rules apply. Do not carry an interpretation across machines
  without checking both cards.
- Never join job identifiers across machines or scheduler eras on the
  assumption the formats correspond. If the card does not name a join key,
  report that the join is undocumented rather than performing it.

## Step 3 -- Read columns the way the card reads them

The `records` `cr:RecordSet` lists one `cr:Field` per CSV column: `name` is
the literal column, `dataType` is the reviewed type, `description` carries
meaning, unit, and notes. Two markers change what you may compute:

- **`UNDECODED: ...`** in a description means the column holds codes the
  card's table does not explain. Say so; do not guess the missing ones.
- **A `(no meaning supplied ...)` placeholder** means exactly that. The
  column is measured but not documented.

Coded columns point at their decoding table with
`references.field.@id`, e.g. `exit_code_enum/code`. The referenced
`cr:RecordSet` has `cr:isEnumeration: true`, and its inline `data` payload is
the table: pairs of `<enum>/code` to `<enum>/meaning`. Decode by joining the
column to that table. Never decode an exit code from general knowledge when
the card tells you what it means on this machine -- the intuitive reading of a
signal number is sometimes exactly backwards here. Two honest labels live in
the meanings:

- `PROVISIONAL:` -- a human wrote a best reading that is not confirmed. Use
  it, and mark your conclusions as resting on a provisional decoding.
- A meaning citing "ALCF Decodings" -- authoritative. Use it without hedging.

Sentinels: a column that is `-1` everywhere (or one value nearly everywhere)
is a flag, not a measurement. Check the card first, then one pass of the
data, before computing any statistic over that column.

## Step 4 -- Compute against the data, carefully

When you load a CSV with pandas:

```python
df = pd.read_csv(path, dtype=str)   # first: nothing is a number until the
                                    # card says it is
```

- Read as `dtype=str` first. Inference mangles job IDs and turns strings like
  `NA` into nulls. Then apply the card's `dataType` column by column, and the
  unit or format its description gives (`parse_dates` only for columns the
  card calls timestamps).
- `.csv.gz` loads directly; do not unpack to disk just to read it.
- Row counts: `wc -l` or a measured count from the card's scope block. Never
  a remembered number.
- Group counts over username-like columns are fine to compute; the values
  themselves are not. See Step 5's privacy rule.

## Step 5 -- Carry the card into every deliverable

Anything you produce -- an answer, a notebook, a plot, a paper -- must carry:

- **`rai:dataLimitations`** entries that touch your method, quoted not
  paraphrased. A limitation that contradicts your instinct is the most
  valuable sentence in the file.
- **`rai:personalSensitiveInformation`**: at ALCF the user and project fields
  are anonymized surrogates (the DJC card names them). Report group counts;
  never publish, plot, or re-identify individual surrogate values.
- **`license`**: read before publishing anything derived from the data.
- **`citeAs`**: copy it verbatim wherever the dataset is used, with
  `version` and `datePublished` so the analysis is pinned to a card revision.
- Which facts you measured from data and which you read from the card. An
  answer that cannot say is not finished.

## Step 6 -- The notebook pattern

For exploratory notebooks on an ALCF dataset:

1. First cell: open the card, print review status, scope, every
   `rai:*` block, and the coded columns with their enumeration names.
2. One cell per claim: each result cites either a measured fact or a card
   field.
3. A closing caveats cell: every limitation that touches any result above.

A notebook that never opened the card is undocumented work, however correct
it looks.

## Step 7 -- Refuse these

- Saying a dataset may be cited, shared, or published when the card does not
  grant it. A missing `license` or `citeAs` is not "free to use"; it is
  unanswered.
- Decoding any code from priors when the card (or its absence) is the real
  source. If there is no enumeration, the codes are undecoded.
- Treating surrogate columns (`*_GENID`) as identity, or joining them to
  anything outside the dataset family without the card saying so.
- Presenting par-baked metadata as documentation. Hand that to the reading
  skill's gate.

## Quick check before your first answer

1. Card found, and which family/machine/dates does it cover?
2. Reviewed or par-baked? Say which.
3. Every coded column: joined to its enumeration, or called undecoded?
4. Every statistic: computed from data, from the card, or estimated? Only the
   first two may be presented as answers.
5. Every deliverable: limitations quoted, privacy rule honored, `citeAs`
   verbatim?
