# ELISA Analysis

A MyAssays-style analysis pipeline for 96-well ELISA plate reader data: drop
in an Excel workbook of raw absorbance readings and a plate layout, and get
back a full 4PL/5PL standard-curve fit, back-calculated sample
concentrations, QC flags, and GraphPad Prism-styled charts — all in one
Excel report.

## What it does

1. Reads the raw OD grid and plate layout from an Excel workbook.
2. Averages replicate wells and (optionally) subtracts the blank.
3. Fits a 4-parameter or 5-parameter logistic (4PL/5PL) standard curve to
   the standards, with optional `1/Y` or `1/Y²` regression weighting (the
   standard approach for ELISA, since variance grows with signal).
4. Back-calculates each standard's concentration from its own fit (% recovery
   QC) and interpolates concentrations for every sample well, applying its
   dilution factor.
5. Flags samples that fall outside the standard curve's calibrated range
   (OOR) or whose replicates disagree by more than 15% CV.
6. Produces a Prism-style standard-curve plot (log-scale X, sigmoidal fit,
   error bars, R² annotation) and a sample-concentration bar chart, embedded
   into a formatted Excel report alongside the full data tables.

## Install

```bash
pip install -r requirements.txt
```

(Optional, for the `elisa-analyze` command-line shortcut: `pip install -e .`)

## Web interface

```bash
streamlit run app.py
```

Opens a browser UI where you can upload your raw-data file, edit the plate
layout as an interactive 8x12 grid (and the standards/samples tables) right
in the page, run the analysis, and see the Prism-style charts and results
tables immediately — with buttons to download the full Excel report and to
save your layout for reuse next time. This works with either the combined
template or a native instrument export (see below); no command line needed.

On load it asks what protein/analyte the ELISA is measuring (e.g. "Human
IL-6"); that name is then used as the title on every chart and in the Excel
report, so every output file is clearly labeled. Click "Set/edit" near the
top of the page to change it later.

## Input workbook format

Your Excel file needs four sheets. Run `python scripts/make_template.py`
to generate a blank copy pre-formatted with the right grids, or open
`examples/example_raw_data.xlsx` to see a filled-in example.

**Raw Data** — an 8x12 grid shaped exactly like the physical plate: column
headers `1..12` across the top, row headers `A..H` down the left side, and
the instrument's raw absorbance reading in each well cell. This is usually
a straight copy/paste from your plate reader's export.

**Plate Layout** — the same 8x12 grid shape, but each cell holds a short
label identifying what's in that well instead of an OD value:
- `STD1`, `STD2`, ... for standards (must match labels in the Standards sheet)
- `BLANK` for zero/blank wells
- any short code (e.g. `UNK1`) for a sample

Repeat a label across multiple wells for replicates (duplicates,
triplicates, etc.) — the tool averages them automatically.

**Standards** — two columns, `Label` and `Concentration`, giving the known
concentration for each standard label used in Plate Layout. A `0`
concentration marks the zero standard (treated as a blank for background
subtraction if no separate `BLANK` wells exist).

**Samples** (optional) — `Label`, `Sample Name`, `Dilution Factor`. Maps
well labels (e.g. `UNK1`) to a human-readable name and a dilution factor
applied after interpolation. Any sample label not listed here defaults to
`Dilution Factor = 1` and uses the well label as its name.

## Usage

```bash
python -m elisa_analysis.cli --input my_plate.xlsx --output-dir results \
    --model 4PL --weight 1/y2
```

Options:
- `--layout` — separate layout workbook, for native instrument exports (see below)
- `--table` — pick a specific OD grid by name when the raw source has more than one
- `--model {4PL,5PL}` — logistic model to fit (default `4PL`)
- `--weight {none,1/y,1/y2}` — regression weighting (default `1/y2`)
- `--units` — concentration unit label used on axes/tables (default `conc. units`)
- `--analyte` — protein/analyte name (e.g. `"Human CXCL10"`), used as the title on every chart and in the Excel report
- `--no-blank-subtract` — skip background subtraction

This writes `<name>_report.xlsx` (results tables + embedded charts) and two
standalone PNGs (`<name>_standard_curve.png`, `<name>_sample_concentrations.png`)
into `--output-dir`.

## Using a native instrument export (e.g. Tecan Spark)

You don't need to hand-copy data out of your plate reader's own export. Point
`--input` straight at the raw file the instrument produced (e.g. a Tecan
Spark / SparkControl "Result sheet" .xlsx) and pass `--layout` pointing at a
small, separate workbook holding just the assay layout — which wells are
standards/samples, their concentrations, and dilution factors. Since the
layout is usually the same for every run of a given kit, you typically only
fill it in once and reuse it:

```bash
python scripts/make_layout_template.py my_kit_layout.xlsx   # fill this in once per kit

python -m elisa_analysis.cli \
    --input P_Yoon_IL6_2026-07-29.xlsx \      # straight from SparkControl
    --layout my_kit_layout.xlsx \
    --output-dir results
```

Tecan/SparkControl exports place the OD grid below several rows of run
metadata (method name, wavelengths, date, plate type), and for
dual-wavelength reads they can contain up to three 8x12 grids per sheet:
the raw absorbance, the reference-wavelength readout, and the
already-corrected `Difference` (raw − reference). `load_plate` scans the
whole sheet for every such grid automatically and:

1. uses the `Difference` table if one was exported (the standard corrected
   OD for ELISA),
2. otherwise computes raw − reference itself if both are present without an
   explicit difference table,
3. otherwise falls back to whichever single grid it found.

Pass `--table "Reference"` (substring match, case-insensitive) to force a
specific table instead. `scripts/make_tecan_example.py` generates a
synthetic Tecan-style raw file + matching layout workbook
(`examples/tecan_spark_raw_example.xlsx` / `tecan_spark_layout_example.xlsx`)
if you want to see the expected shape without a real export on hand.

This importer looks for any 8x12 grid (column headers `1..12`, row headers
`A..H`) anywhere in the workbook, so it isn't limited to Tecan specifically
— other readers that export a similarly shaped grid (with or without a
metadata header block above it) should load the same way.

## Annotating a raw export yourself (no layout file at all)

If you already hand-annotate your raw export with a plate map before
analyzing it, you don't need a separate layout workbook, or even the
`--layout` flag — just point `--input` (or, in the web app, the upload box)
at that one file:

```bash
python -m elisa_analysis.cli --input my_annotated_export.xlsx --output-dir results
```

Add a `PLATE MAP` header cell anywhere in the sheet, and (optionally) a
`DILUTION MAP` header cell next to it. Beneath each header, across the same
8 rows (A–H) as your OD grid, one column per physical plate column in order
starting at column 1:
- a standard's concentration, written as `Std <number>` (e.g. `Std 1000`, `Std 62.5`)
- `BLANK` for a background/blank well
- anything else is treated as a sample name — repeat the exact same text in
  another well to mark it as a replicate of that sample

Under `DILUTION MAP`, give each sample well a `<sample>/<diluent>` ratio
(e.g. `10.0/90.0`) — the dilution factor is computed as (total volume) ÷
(sample part); mention the total volume in the header text (e.g. "total 100
uL") or it defaults to 100. Columns that aren't ratio-shaped (e.g. one kept
just for visual alignment) are ignored.

`scripts/make_annotated_tecan_example.py` generates a synthetic example of
this (`examples/tecan_annotated_example.xlsx`) if you want to see the exact
shape expected before trying it on a real file. In the web app, uploading a
file like this auto-fills the plate layout, standards, and samples for you
— check it over, then go straight to "Run analysis".

## Try it with the bundled example

```bash
python scripts/make_example.py examples/example_raw_data.xlsx   # regenerate if needed
python -m elisa_analysis.cli --input examples/example_raw_data.xlsx --output-dir results
```

The example simulates an 8-point standard curve and 10 patient samples
(including one intentionally below the lowest standard and one above the
highest, to show the out-of-range flagging).

## Using it as a library

```python
from elisa_analysis.io_utils import load_plate_workbook
from elisa_analysis.analysis import analyze
from elisa_analysis.report import write_report

plate = load_plate_workbook("my_plate.xlsx")
result = analyze(plate, model="5PL", weight_mode="1/y2")

print(result.fit.params, result.fit.r_squared)
print(result.samples_table)

write_report(result, "results/report.xlsx", "results/my_plate")
```

## Curve models

```
4PL:  y = D + (A - D) / (1 + (x / C) ** B)
5PL:  y = D + (A - D) / (1 + (x / C) ** B) ** E
```

`A`/`D` are the low/high asymptotes, `C` is the EC50/IC50, `B` is the Hill
slope, and `E` (5PL only) is an asymmetry factor. Both models are inverted
in closed form to back-calculate concentration from a measured OD.

## Tests

```bash
python -m pytest tests/ -v
```
