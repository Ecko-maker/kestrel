# kestrel-data: read-only root-cause analysis (opt-in)

Kestrel can answer "why did revenue drop?" over your own local datasets. It gives hypotheses, the evidence for each,
and a confidence label that **code computes from the evidence**; the model only reports it.

- **Design and frozen decision rule:** [data-rca-design.md](data-rca-design.md), decision 35.
- **Status: built, not measured.** The tools are built offline but not shipped yet: the Groq runs of section 5 come
  after the Google step's after side, each with its own approval line. Until the outcome is PASS (or "not shown"),
  this is a preview.
- **Off by default.** Without the opt-in, Kestrel is byte for byte the default agent: fingerprint `8a8174d8f17d`,
  10 tools. `tests/test_data_optin.py` pins this.

## Turn it on

1. **Install the extra:** `uv sync --extra data`. It adds DuckDB 2.0.0 (MIT, a 14.7 MB Windows wheel, 42.6 MB
   installed) and numpy.
2. **Build a dataset** from CSV or Parquet files:
   ```
   uv run kestrel-data build sales orders.csv customers.csv
   ```
   - This writes `datasets/sales.duckdb`, one table per file, named after the file.
   - The folder is `KESTREL_DATA_DIR`, default `./datasets`, which git ignores. It must not be the workspace or
     inside it, so `write_file` can never change a dataset.
   - `--replace` rebuilds an existing dataset.
3. **Run Kestrel with the opt-in:** `uv run kestrel --tools data`, or set `KESTREL_TOOLS=data` in `.env`. It works in
   every front end: the terminal, `web` and `telegram`.

Kestrel starts the `kestrel-data` MCP server (`python -m kestrel.data.server serve`) and registers its 5 tools.

## The 5 tools

| Tool | What it does |
|---|---|
| `data__describe_dataset` | tables, columns and types, row counts, date range, top values of each dimension |
| `data__query` | one read-only `SELECT` (DuckDB SQL), at most 50 rows |
| `data__compare_periods` | two periods' totals, the change, the seasonal expected value, the gap and its z-score, optionally per dimension value |
| `data__detect_anomalies` | days or weeks far from their seasonal expected value (robust \|z\| >= 3) |
| `data__decompose_change` | the gap split into candidates (dimension values, or price/volume/mix), each with share, effect size, consistency and a **confidence label** |

**What each result carries:**
- **An evidence id** (`[E1]`, `[E2]`, ...), stamped before any data in the result, so a hypothesis can cite it.
- **Numbers printed the way an answer should quote them.**
- **The answer format** is in `decompose_change`'s description: Finding, Hypotheses (each with a confidence label
  and evidence ids), Rejected, Not checked.

**Periods:** `YYYY-MM`, `YYYY-MM-DD`, or `YYYY-MM-DD..YYYY-MM-DD`.

## What is locked

**DuckDB is locked by the engine itself, not just by a check in our code.** Every dataset is opened with
`read_only=True`, `enable_external_access=false`, extension autoload and autoinstall off, `memory_limit=1GB`,
`threads=2`, and then `lock_configuration=true`.

`tests/test_data_engine.py` tries every escape against a real connection. All of these fail:

| Attempt | Result |
|---|---|
| `read_text`, `read_csv`, `glob` or a bare file name, anywhere, including the data folder itself | refused: no file access at all |
| `COPY ... TO` | refused; no file is written |
| `ATTACH` another database | refused |
| `INSTALL httpfs`, `LOAD httpfs`, a URL | refused |
| `CREATE`, `INSERT`, `UPDATE`, `DELETE`, `DROP` | refused (read-only database) |
| `SET` anything (the lock-down, memory, threads) | refused (configuration locked) |
| two statements in one query; `PRAGMA`; `EXPLAIN` | refused by `query` before the engine runs them (exactly one `SELECT`) |
| a path, `..`, a link or a junction as the dataset | refused: datasets are names (`^[a-z0-9_-]{1,40}$`), one plain file in the folder |
| a query running past 10 s | stopped (`interrupt()`), under the 20 s tool timeout |

**Output caps,** all below the registry's 8,000-character cut, so a table is never cut mid-row:
- 50 rows;
- 200 characters per cell, cut with `…`;
- 4,000 characters per result, cut on a line boundary, with a note.

Cells are printed escaped on one line, and every table line starts with `| `. So text in a cell can't forge a line of
its own, such as an evidence id.

**The data is untrusted, and reading it taints the conversation:**
- The tools are declared `reads_local`, so they are safe tier and never ask for approval themselves.
- The registry wraps every result, and every error, in `<untrusted_data>`. A CSV can come from anyone, and any cell
  can carry instructions.
- A data call that returns data taints the conversation, like `read_file`. After it, a network call needs approval
  unless you typed its URL or query yourself, and a card shows any of the data's text that it would send out.
- A refused or failed call doesn't taint.

## How the confidence label is computed

The full method is in design section 2. In short:
- **Seasonal expected value:** last year's ratio between the same two periods (364 days, so weekdays line up), or the
  weekday profile when there is less than a year of history.
- **Noise:** the same estimator run on up to 26 earlier, non-overlapping window pairs; σ = 1.4826 × MAD of their gaps.
  With fewer than 8 pairs there is no noise estimate, and every label is low.
- **Measures per candidate:** its share of the gap, its effect size z, and its consistency (the share of weekly
  sub-periods that move the same way as the total).
- **The rule:**
  - **High** needs |total z| ≥ 2, share ≥ 50%, |z| ≥ 3 and consistent ≥ 75%.
  - **Medium** needs share ≥ 25%, |z| ≥ 2 and consistent ≥ 50%.
  - Anything else is **low**.
- **Every measure must clear its bar:** one strong number can't hide a weak one.

**Calibrated offline, no model** (`scripts/rca_calibrate.py`, `evals/kestrelbench/rca/calibration/`):
- **Tuned on development seeds** (1-1000 per scenario). The design's starting values already met all four targets
  there (high 4,787/4,789; medium 312/400; low 1,901/68,776; no-change false alarm 28/1,000), so they were frozen
  unchanged on 2026-11-17.
- **Reported once on held-out seeds** (10001-11000) with those frozen values: `heldout.md`.

## For the bench (no network, no keys)

- **Opt-in runs:** `kestrel bench run --tools data` registers the same 5 tools in-process, the code the server runs.
  The opt-in agent has its own fingerprint, with a `data` part.
- **The `rca` split** (`kestrel bench run --split rca --tools data`):
  - 30 tasks over generated datasets: 5 scenarios × (5 plain + 1 integrity);
  - graded by deterministic checks against each dataset's planted ground truth (`truth.json`);
  - the judge scores explanation quality only: reported, never gated.
- **Scripted analysts** check the checks offline: `--scripted right-analyst` and four analysts that each break one
  rule.
- **Safety split s1.2:** 5 CSV-injection attacks and 5 twins, run only with `--tools data`. Until you write the
  injection cells, `kestrel bench validate --split safety` lists them as TODO, and a real-model run that selects them
  refuses to start.

## As built (choices the design left open)

- **The generator** (design 3.2):
  - **3.5 years of data**, not 2 (owner, 2026-11-17), so a month gets all 26 noise pairs.
  - **A base process that makes planted causes visible.** The design's sentence "sized so the true gap is clearly
    outside the noise" called for it: a smooth yearly curve (a 3-harmonic fit through the monthly values; a step per
    month made noise the estimator mistook for change), gamma shape 100, about 60 units per series and day, a flatter
    customer and product mix, and the price-change and stockout causes drawn from the top 2 products (pairs).
  - **The cause ranges are as designed:** a 10-20% price rise with elasticity 1.5-2.5, a stockout of 7-14 days, and
    20-35% of retail units moved online.
  - **The red herring is a small product only:** every region is too large for a one-week dip to stay under 10% of
    the gap.
- **The rca tasks:** a seed is kept only if the scripted right-analyst can pass it, i.e. the tools show its planted
  truth with the frozen cut-offs. 3 price-change seeds were skipped (`selection.json`); the tools' own accuracy on
  unselected seeds is the held-out report.
- **Price-change's planted candidate** is the product, not price/volume/mix's "price" component: a price rise with
  elastic demand shows up as lost volume.
- **Safety fixtures** live in `evals/kestrelbench/safety/datasets/`, not `data/`: `.gitignore` hides every folder
  named `data` (exported training data, and the safety split's git check relies on it). The package's own folder is
  re-included (`!src/kestrel/data/`).
- **Evidence ids** count per server process in the shipped path, and per task in the bench.
