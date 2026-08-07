"""Reading raw 96-well ELISA data + plate layout from Excel workbook(s).

Two input modes are supported:

1. **Combined template** (see scripts/make_template.py / examples/example_raw_data.xlsx):
   a single workbook with "Raw Data", "Plate Layout", "Standards", and
   optional "Samples" sheets, each shaped as an 8x12 grid mirroring the
   physical plate (or a two-column table for Standards/Samples). Use
   `load_plate(raw_path)` with no `layout_path`.

2. **Native instrument export + reusable layout** (e.g. a Tecan Spark /
   SparkControl "Result sheet" .xlsx exported straight from the reader):
   the raw OD grid is read directly from the instrument's own export, and
   the assay layout (which wells are standards/samples, their
   concentrations/dilutions) comes from a separate small workbook built
   from scripts/make_layout_template.py -- typically reused across many
   runs of the same kit. Use `load_plate(raw_path, layout_path=...)`.

Instrument exports often contain more than one 8x12 grid per sheet (e.g. a
raw absorbance table, a reference-wavelength table, and a blank/reference
corrected "Difference" table for dual-wavelength reads), preceded by
several rows of run metadata. `load_plate` auto-detects every grid in the
raw-data source and prefers a "Difference" (already reference-corrected)
table when present; pass `table=` to force a specific one, or computes the
difference itself if only raw + reference tables are found.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    source_table: Optional[str] = None  # which OD table was used, when multiple were found
    available_tables: list = field(default_factory=list)


def _sheet_name(sheets: dict, *candidates: str) -> Optional[str]:
    lower_map = {s.lower().strip(): s for s in sheets}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def _find_all_grid_origins(df: pd.DataFrame):
    """Find every (header_row, header_col) where a 1..12 column header row
    is immediately followed by 8 rows labeled A..H in the preceding column."""
    n_rows, n_cols = df.shape
    origins = []
    for r in range(n_rows):
        row_vals = df.iloc[r].tolist()
        for c in range(n_cols - 11):
            window = row_vals[c : c + 12]
            try:
                nums = [int(float(v)) for v in window]
            except (ValueError, TypeError):
                continue
            if nums != list(range(1, 13)):
                continue
            row_labels = []
            for rr in range(r + 1, r + 9):
                if rr >= n_rows:
                    break
                val = df.iat[rr, c - 1] if c - 1 >= 0 else None
                row_labels.append(str(val).strip().upper() if val is not None else "")
            if row_labels == ROWS:
                origins.append((r, c))
    if not origins:
        raise ValueError(
            "Could not locate an 8x12 plate grid (columns 1-12, rows A-H) in this sheet."
        )
    return origins


def _nearest_label_above(df: pd.DataFrame, header_row: int, search_back: int = 6) -> Optional[str]:
    for rr in range(header_row - 1, max(-1, header_row - 1 - search_back), -1):
        for cc in range(min(3, df.shape[1])):
            val = df.iat[rr, cc]
            if val is None:
                continue
            text = str(val).strip()
            if text and text.lower() != "nan":
                return text
    return None


def _cell_to_od(val) -> Optional[float]:
    if val is None:
        return None
    text = str(val).strip()
    if text == "" or text.lower() == "nan":
        return None
    try:
        return float(text)
    except ValueError:
        return None  # e.g. "OVER"/saturation markers from the instrument


def _read_grid_at(df: pd.DataFrame, header_row: int, header_col: int) -> dict:
    grid = {}
    for i, row_letter in enumerate(ROWS):
        data_row = header_row + 1 + i
        for j, col_num in enumerate(COLS):
            data_col = header_col + j
            val = df.iat[data_row, data_col] if data_row < df.shape[0] and data_col < df.shape[1] else None
            grid[f"{row_letter}{col_num}"] = val
    return grid


def extract_od_tables(path: str, sheet_name: Optional[str] = None) -> dict:
    """Find every 8x12 OD grid in the workbook (optionally restricted to one
    sheet), keyed by a human-readable label (nearby heading text, or a
    generic fallback). Cell values are returned as raw floats/None."""
    sheets = pd.read_excel(path, sheet_name=sheet_name, header=None)
    if isinstance(sheets, pd.DataFrame):
        sheets = {sheet_name or 0: sheets}

    tables = {}
    for sname, df in sheets.items():
        try:
            origins = _find_all_grid_origins(df)
        except ValueError:
            continue
        for idx, (r, c) in enumerate(origins):
            label = _nearest_label_above(df, r) or f"{sname} Table {idx + 1}"
            label = str(label).strip()
            raw_grid = _read_grid_at(df, r, c)
            grid = {wid: _cell_to_od(v) for wid, v in raw_grid.items()}
            if label in tables:
                label = f"{label} ({sname}#{idx + 1})"
            tables[label] = grid
    return tables


def select_od_table(tables: dict, table: Optional[str] = None):
    """Pick which OD table to use for analysis out of everything found by
    extract_od_tables. Prefers an already reference-corrected "Difference"
    table; falls back to computing raw - reference if both exist but no
    difference table was exported; otherwise uses the only/first table."""
    if not tables:
        raise ValueError("No 8x12 plate grid of OD readings was found in the raw-data source.")

    if table:
        matches = [k for k in tables if table.lower() in k.lower()]
        if not matches:
            raise ValueError(
                f"Requested table '{table}' not found. Available tables: {list(tables)}"
            )
        return matches[0], tables[matches[0]]

    diff_matches = [k for k in tables if "differ" in k.lower()]
    if diff_matches:
        return diff_matches[0], tables[diff_matches[0]]

    if len(tables) == 1:
        only = next(iter(tables))
        return only, tables[only]

    ref_matches = [k for k in tables if "reference" in k.lower()]
    non_ref = [k for k in tables if k not in ref_matches]
    if ref_matches and non_ref:
        primary_label, ref_label = non_ref[0], ref_matches[0]
        primary, ref = tables[primary_label], tables[ref_label]
        computed = {
            wid: (primary[wid] - ref[wid]) if primary.get(wid) is not None and ref.get(wid) is not None else None
            for wid in primary
        }
        return f"{primary_label} minus {ref_label} (computed)", computed

    first_label = next(iter(tables))
    return first_label, tables[first_label]


def _parse_standards_sheet(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.iloc[0]]
    df = df.iloc[1:].reset_index(drop=True)
    label_col = next(c for c in df.columns if c.lower().startswith("label"))
    conc_col = next(c for c in df.columns if c.lower().startswith("conc"))
    df = df[[label_col, conc_col]].dropna(how="all")
    df.columns = ["Label", "Concentration"]
    df["Label"] = df["Label"].astype(str).str.strip()
    df["Concentration"] = pd.to_numeric(df["Concentration"], errors="coerce")
    return df.dropna(subset=["Concentration"]).reset_index(drop=True)


def _parse_samples_sheet(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.iloc[0]]
    df = df.iloc[1:].reset_index(drop=True)
    cols_lower = {c.lower(): c for c in df.columns}
    label_c = next(v for k, v in cols_lower.items() if k.startswith("label"))
    name_c = next((v for k, v in cols_lower.items() if "name" in k), None)
    dil_c = next((v for k, v in cols_lower.items() if "dilut" in k), None)
    out = pd.DataFrame()
    out["Label"] = df[label_c].astype(str).str.strip()
    out["SampleName"] = df[name_c].astype(str).str.strip() if name_c else out["Label"]
    out["Dilution"] = pd.to_numeric(df[dil_c], errors="coerce") if dil_c else 1.0
    out["Dilution"] = out["Dilution"].fillna(1.0)
    return out.dropna(subset=["Label"]).reset_index(drop=True)


def load_plate(raw_path: str, layout_path: Optional[str] = None, table: Optional[str] = None) -> PlateData:
    """Load raw OD data + assay layout into a PlateData.

    If layout_path is omitted, raw_path must be a combined single-workbook
    template containing "Raw Data", "Plate Layout", and "Standards" sheets
    (plus optional "Samples"). If layout_path is given, raw_path can be any
    file with one or more embedded 8x12 OD grids (e.g. a native Tecan Spark
    / SparkControl export), and the layout/standards/samples come from
    layout_path instead.
    """
    layout_source = layout_path or raw_path
    layout_sheets = pd.read_excel(layout_source, sheet_name=None, header=None)

    layout_name = _sheet_name(layout_sheets, "Plate Layout", "Layout")
    std_name = _sheet_name(layout_sheets, "Standards", "Standard Concentrations")
    sample_name = _sheet_name(layout_sheets, "Samples", "Sample Info")

    if layout_name is None:
        raise ValueError(
            f"'{layout_source}' is missing a 'Plate Layout' sheet describing what is in each well."
        )
    if std_name is None:
        raise ValueError(
            f"'{layout_source}' is missing a 'Standards' sheet mapping standard labels to concentrations."
        )

    if layout_path is None:
        raw_name = _sheet_name(layout_sheets, "Raw Data", "RawData", "Data", "OD")
        if raw_name is None:
            raise ValueError(
                "Workbook is missing a 'Raw Data' sheet with the 8x12 plate grid of OD readings. "
                "If this is a native instrument export, pass layout_path= pointing at a separate "
                "layout workbook instead."
            )
        all_tables = extract_od_tables(raw_path, sheet_name=raw_name)
    else:
        all_tables = extract_od_tables(raw_path)

    source_label, od_grid = select_od_table(all_tables, table=table)

    origins = _find_all_grid_origins(layout_sheets[layout_name])
    label_grid = _read_grid_at(layout_sheets[layout_name], *origins[0])

    wells = []
    for row_letter in ROWS:
        for col_num in COLS:
            wid = f"{row_letter}{col_num}"
            label = label_grid.get(wid)
            label = str(label).strip() if label is not None and str(label).strip().lower() != "nan" else None
            wells.append(Well(wid, row_letter, col_num, label, od_grid.get(wid)))

    std_df = _parse_standards_sheet(layout_sheets[std_name])
    samples_df = _parse_samples_sheet(layout_sheets[sample_name]) if sample_name else pd.DataFrame(
        columns=["Label", "SampleName", "Dilution"]
    )

    return PlateData(
        wells=wells,
        standards=std_df,
        samples=samples_df,
        source_table=source_label,
        available_tables=list(all_tables.keys()),
    )


def load_plate_workbook(path: str) -> PlateData:
    """Backwards-compatible alias for the combined single-workbook template."""
    return load_plate(path)
