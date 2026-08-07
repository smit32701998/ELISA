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
- `--model {4PL,5PL}` — logistic model to fit (default `4PL`)
- `--weight {none,1/y,1/y2}` — regression weighting (default `1/y2`)
- `--units` — concentration unit label used on axes/tables (default `conc. units`)
- `--no-blank-subtract` — skip background subtraction

This writes `<name>_report.xlsx` (results tables + embedded charts) and two
standalone PNGs (`<name>_standard_curve.png`, `<name>_sample_concentrations.png`)
into `--output-dir`.

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
