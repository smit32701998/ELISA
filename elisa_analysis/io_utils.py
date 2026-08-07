"""Reading raw 96-well ELISA data + plate layout from an Excel workbook.

Expected workbook layout (see scripts/make_template.py to generate a blank
copy, and examples/example_raw_data.xlsx for a filled-in example):

Sheet "Raw Data"
    An 8x12 grid shaped exactly like the physical plate: column headers
    1..12 across the top, row headers A..H down the left side, and the
    instrument's raw absorbance (OD) reading in each well cell.

Sheet "Plate Layout"
    The same 8x12 grid shape, but each cell instead holds a short label
    identifying what is in that well, e.g. STD1, STD2, ... BLANK, UNK1,
    UNK2, ... Replicate wells (duplicates/triplicates) simply repeat the
    same label.

Sheet "Standards"
    Two columns: Label, Concentration (the label must match the labels
    used for standards in the Plate Layout sheet). A row with
    Concentration == 0 is treated as the zero standard / blank.

Sheet "Samples" (optional)
    Columns: Label, Sample Name, Dilution Factor. Maps the short well
    labels used in Plate Layout (e.g. UNK1) to a human-readable sample
    name and a dilution factor to apply after interpolation. If this
    sheet is omitted, every non-standard, non-blank label is treated as
    a sample with dilution factor 1 and the label itself as the name.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

ROWS = list("ABCDEFGH")
COLS = list(range(1, 13))

BLANK_LABELS = {"BLANK", "BLK", "NSB", "ZERO"}


@dataclass
class Well:
    well_id: str
    row: str
    col: int
    label: Optional[str]
    od: Optional[float]


@dataclass
class PlateData:
    wells: list
    standards: pd.DataFrame  # columns: Label, Concentration
    samples: pd.DataFrame  # columns: Label, SampleName, Dilution
    units: str = "conc. units"


def _sheet_name(sheets: dict, *candidates: str) -> Optional[str]:
    lower_map = {s.lower().strip(): s for s in sheets}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def _find_grid_origin(df: pd.DataFrame):
    """Locate the (header_row, header_col) of a 1..12 column header row
    that is immediately followed by an A..H row-label column below it."""
    n_rows, n_cols = df.shape
    for r in range(n_rows):
        row_vals = df.iloc[r].tolist()
        for c in range(n_cols - 11):
            window = row_vals[c : c + 12]
            try:
                nums = [int(float(v)) for v in window]
            except (ValueError, TypeError):
                continue
            if nums == list(range(1, 13)):
                # check the 8 rows below, column c - 1, for A..H
                row_labels = []
                for rr in range(r + 1, r + 9):
                    if rr >= n_rows:
                        break
                    val = df.iat[rr, c - 1] if c - 1 >= 0 else None
                    row_labels.append(str(val).strip().upper() if val is not None else "")
                if row_labels == ROWS:
                    return r, c
    raise ValueError(
        "Could not locate an 8x12 plate grid (columns 1-12, rows A-H) in this sheet."
    )


def _read_grid(df: pd.DataFrame) -> dict:
    header_row, header_col = _find_grid_origin(df)
    grid = {}
    for i, row_letter in enumerate(ROWS):
        data_row = header_row + 1 + i
        for j, col_num in enumerate(COLS):
            data_col = header_col + j
            val = df.iat[data_row, data_col] if data_row < df.shape[0] else None
            grid[f"{row_letter}{col_num}"] = val
    return grid


def load_plate_workbook(path: str) -> PlateData:
    sheets = pd.read_excel(path, sheet_name=None, header=None)

    raw_name = _sheet_name(sheets, "Raw Data", "RawData", "Data", "OD")
    layout_name = _sheet_name(sheets, "Plate Layout", "Layout")
    std_name = _sheet_name(sheets, "Standards", "Standard Concentrations")
    sample_name = _sheet_name(sheets, "Samples", "Sample Info")

    if raw_name is None:
        raise ValueError(
            "Workbook is missing a 'Raw Data' sheet with the 8x12 plate grid of OD readings."
        )
    if layout_name is None:
        raise ValueError(
            "Workbook is missing a 'Plate Layout' sheet describing what is in each well."
        )
    if std_name is None:
        raise ValueError(
            "Workbook is missing a 'Standards' sheet mapping standard labels to concentrations."
        )

    od_grid = _read_grid(sheets[raw_name])
    label_grid = _read_grid(sheets[layout_name])

    wells = []
    for row_letter in ROWS:
        for col_num in COLS:
            wid = f"{row_letter}{col_num}"
            label = label_grid.get(wid)
            label = str(label).strip() if label is not None and str(label).strip().lower() != "nan" else None
            od = od_grid.get(wid)
            try:
                od = float(od) if od is not None and str(od).strip() != "" else None
            except (ValueError, TypeError):
                od = None
            wells.append(Well(wid, row_letter, col_num, label, od))

    std_df = sheets[std_name].copy()
    std_df.columns = [str(c).strip() for c in std_df.iloc[0]]
    std_df = std_df.iloc[1:].reset_index(drop=True)
    std_df = std_df.rename(columns=lambda c: c.strip())
    label_col = next(c for c in std_df.columns if c.lower().startswith("label"))
    conc_col = next(c for c in std_df.columns if c.lower().startswith("conc"))
    std_df = std_df[[label_col, conc_col]].dropna(how="all")
    std_df.columns = ["Label", "Concentration"]
    std_df["Label"] = std_df["Label"].astype(str).str.strip()
    std_df["Concentration"] = pd.to_numeric(std_df["Concentration"], errors="coerce")
    std_df = std_df.dropna(subset=["Concentration"]).reset_index(drop=True)

    if sample_name is not None:
        smp_df = sheets[sample_name].copy()
        smp_df.columns = [str(c).strip() for c in smp_df.iloc[0]]
        smp_df = smp_df.iloc[1:].reset_index(drop=True)
        cols_lower = {c.lower(): c for c in smp_df.columns}
        label_c = next(v for k, v in cols_lower.items() if k.startswith("label"))
        name_c = next((v for k, v in cols_lower.items() if "name" in k), None)
        dil_c = next((v for k, v in cols_lower.items() if "dilut" in k), None)
        out = pd.DataFrame()
        out["Label"] = smp_df[label_c].astype(str).str.strip()
        out["SampleName"] = smp_df[name_c].astype(str).str.strip() if name_c else out["Label"]
        out["Dilution"] = pd.to_numeric(smp_df[dil_c], errors="coerce") if dil_c else 1.0
        out["Dilution"] = out["Dilution"].fillna(1.0)
        out = out.dropna(subset=["Label"]).reset_index(drop=True)
        samples_df = out
    else:
        samples_df = pd.DataFrame(columns=["Label", "SampleName", "Dilution"])

    return PlateData(wells=wells, standards=std_df, samples=samples_df)
