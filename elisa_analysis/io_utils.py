"""Reading raw 96-well ELISA data + plate layout from Excel workbook(s).

Three input modes are supported:

1. **Combined template** (see scripts/make_template.py / examples/example_raw_data.xlsx):
   a single workbook with "Raw Data", "Plate Layout", "Standards", and
   optional "Samples" sheets, each shaped as an 8x12 grid mirroring the
   physical plate (or a two-column table for Standards/Samples). Use
   `load_plate(raw_path)` with no `layout_path`.

2. **Annotated raw export** (see scripts/make_annotated_tecan_example.py):
   some labs hand-annotate their raw instrument export with a "PLATE MAP"
   and "DILUTION MAP" alongside the OD grid before analyzing it. When
   these headers are found, `load_plate(raw_path)` parses them directly --
   no separate layout file or manual entry needed at all. See
   `extract_embedded_plate_map` for the exact convention expected.

3. **Native instrument export + reusable layout** (e.g. a Tecan Spark /
   SparkControl "Result sheet" .xlsx exported straight from the reader,
   with no annotation): the raw OD grid is read directly from the
   instrument's own export, and the assay layout (which wells are
   standards/samples, their concentrations/dilutions) comes from a
   separate small workbook built from scripts/make_layout_template.py --
   typically reused across many runs of the same kit. Use
   `load_plate(raw_path, layout_path=...)`.

Instrument exports often contain more than one 8x12 grid per sheet (e.g. a
raw absorbance table, a reference-wavelength table, and a blank/reference
corrected "Difference" table for dual-wavelength reads), preceded by
several rows of run metadata. `load_plate` auto-detects every grid in the
raw-data source and prefers a "Difference" (already reference-corrected)
table when present; pass `table=` to force a specific one, or computes the
difference itself if only raw + reference tables are found.
"""
from __future__ import annotations

import re
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
    flag: Optional[str] = None  # instrument marker when od couldn't be parsed, e.g. "OVER"


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


def detect_raw_data_sheet(source) -> Optional[str]:
    """Return the "Raw Data"-ish sheet name if `source` looks like a combined
    template (also has Plate Layout/Standards sheets alongside the OD grid),
    else None -- meaning the whole workbook should be scanned for grids (the
    case for a native instrument export with no separate layout sheets)."""
    sheets = pd.read_excel(source, sheet_name=None, header=None)
    return _sheet_name(sheets, "Raw Data", "RawData", "Data", "OD")


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


def parse_od_cell(val) -> tuple:
    """Split a raw OD cell into (numeric_value, marker). marker is None for a
    normal number or a truly blank/unread well; for a non-numeric instrument
    reading (e.g. Tecan's "OVER" on a saturated well) numeric_value is None
    and marker carries the original text so it isn't silently discarded."""
    if val is None:
        return None, None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return (None, None) if np.isnan(val) else (float(val), None)
    text = str(val).strip()
    if text == "" or text.lower() == "nan":
        return None, None
    try:
        return float(text), None
    except ValueError:
        return None, text.upper()


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
    generic fallback). Cell values are returned as-read (float/str/None) --
    use parse_od_cell to split a value into (numeric, marker)."""
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
            grid = _read_grid_at(df, r, c)
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
        computed = {}
        for wid, praw in primary.items():
            p_val, p_marker = parse_od_cell(praw)
            if p_marker is not None:
                computed[wid] = p_marker  # e.g. "OVER" on the primary read -- still saturated
                continue
            r_val, _ = parse_od_cell(ref.get(wid))
            computed[wid] = (p_val - r_val) if p_val is not None and r_val is not None else None
        return f"{primary_label} minus {ref_label} (computed)", computed

    first_label = next(iter(tables))
    return first_label, tables[first_label]


def normalize_standards_df(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a (Label, Concentration)-ish DataFrame with real column headers
    into the canonical ["Label", "Concentration"] form."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    label_col = next(c for c in df.columns if c.lower().startswith("label"))
    conc_col = next(c for c in df.columns if c.lower().startswith("conc"))
    out = df[[label_col, conc_col]].copy()
    out.columns = ["Label", "Concentration"]
    out["Label"] = out["Label"].astype(str).str.strip()
    out = out[(out["Label"] != "") & (out["Label"].str.lower() != "none")]
    out["Concentration"] = pd.to_numeric(out["Concentration"], errors="coerce")
    return out.dropna(subset=["Concentration"]).reset_index(drop=True)


def normalize_samples_df(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a (Label, Sample Name, Dilution Factor)-ish DataFrame with real
    column headers into the canonical ["Label", "SampleName", "Dilution"] form."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    cols_lower = {c.lower(): c for c in df.columns}
    label_c = next(v for k, v in cols_lower.items() if k.startswith("label"))
    name_c = next((v for k, v in cols_lower.items() if "name" in k), None)
    dil_c = next((v for k, v in cols_lower.items() if "dilut" in k), None)
    out = pd.DataFrame()
    out["Label"] = df[label_c].astype(str).str.strip()
    out["SampleName"] = df[name_c].astype(str).str.strip() if name_c else out["Label"]
    out["Dilution"] = pd.to_numeric(df[dil_c], errors="coerce") if dil_c else 1.0
    out["Dilution"] = out["Dilution"].fillna(1.0)
    out = out[(out["Label"] != "") & (out["Label"].str.lower() != "none")]
    return out.reset_index(drop=True)


def _parse_standards_sheet(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.iloc[0]]
    return normalize_standards_df(df.iloc[1:].reset_index(drop=True))


def _parse_samples_sheet(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.iloc[0]]
    return normalize_samples_df(df.iloc[1:].reset_index(drop=True))


def read_layout_workbook(source) -> tuple:
    """Parse Plate Layout / Standards / Samples sheets out of a workbook
    (path or file-like). Returns (label_grid, standards_df, samples_df)."""
    sheets = pd.read_excel(source, sheet_name=None, header=None)

    layout_name = _sheet_name(sheets, "Plate Layout", "Layout")
    std_name = _sheet_name(sheets, "Standards", "Standard Concentrations")
    sample_name = _sheet_name(sheets, "Samples", "Sample Info")

    if layout_name is None:
        raise ValueError("Workbook is missing a 'Plate Layout' sheet describing what is in each well.")
    if std_name is None:
        raise ValueError("Workbook is missing a 'Standards' sheet mapping standard labels to concentrations.")

    origins = _find_all_grid_origins(sheets[layout_name])
    label_grid = _read_grid_at(sheets[layout_name], *origins[0])
    label_grid = {
        wid: (str(v).strip() if v is not None and str(v).strip().lower() != "nan" else None)
        for wid, v in label_grid.items()
    }

    std_df = _parse_standards_sheet(sheets[std_name])
    samples_df = _parse_samples_sheet(sheets[sample_name]) if sample_name else pd.DataFrame(
        columns=["Label", "SampleName", "Dilution"]
    )
    return label_grid, std_df, samples_df


_STD_LABEL_RE = re.compile(r"(?i)\bstd\.?\s*([\d]*\.?[\d]+)\b")
_DILUTION_RATIO_RE = re.compile(r"^\s*([\d.]+)\s*/\s*([\d.]+)\s*$")
_TOTAL_VOLUME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*u?l", re.IGNORECASE)


def _find_cell_containing(df: pd.DataFrame, needle: str):
    needle = needle.upper()
    n_rows, n_cols = df.shape
    for r in range(n_rows):
        for c in range(n_cols):
            val = df.iat[r, c]
            if val is not None and needle in str(val).strip().upper():
                return r, c
    return None


def _annotation_column_block(df: pd.DataFrame, header_row: int, start_col: int, stop_col: Optional[int] = None, n_rows: int = 8):
    """Columns starting at start_col holding at least one non-empty value in
    the n_rows rows beneath header_row, stopping at the first fully-empty
    column (or at stop_col, exclusive)."""
    cols = []
    max_col = (stop_col - 1) if stop_col is not None else df.shape[1] - 1
    c = start_col
    while c <= max_col:
        has_value = any(
            df.iat[header_row + i, c] is not None and str(df.iat[header_row + i, c]).strip() not in ("", "nan")
            for i in range(1, n_rows + 1)
            if header_row + i < df.shape[0]
        )
        if not has_value:
            break
        cols.append(c)
        c += 1
    return cols


def _classify_annotation_cell(val):
    """Split a PLATE MAP well entry into ("blank"|"standard"|"sample", payload)."""
    if val is None:
        return None, None
    text = str(val).strip()
    if text == "" or text.lower() == "nan":
        return None, None
    if text.upper() in BLANK_LABELS or text.upper() == "BLANK":
        return "blank", None
    m = _STD_LABEL_RE.search(text)
    if m:
        return "standard", float(m.group(1))
    return "sample", text


def extract_embedded_plate_map(source) -> Optional[tuple]:
    """Detect a hand-added "PLATE MAP" (+ optional "DILUTION MAP") annotation
    placed next to the raw OD grid, as some labs add before analyzing --
    letting a single uploaded file fully describe the assay with no separate
    layout workbook or manual grid entry.

    Convention (inferred from a real annotated export): a "PLATE MAP" header
    cell sits on the same row as one of the OD grid's own column-header
    rows; the 8 rows beneath it (A-H) hold, one column per physical plate
    column in left-to-right order starting at column 1: a standard's label
    ("Std 1000"), the word BLANK, or a free-text sample name. An optional,
    similarly-shaped "DILUTION MAP" block index-aligned with it gives a
    "sample/diluent" ratio (e.g. "10.0/90.0" out of a stated total volume,
    parsed from the header text, defaulting to 100) for the same wells --
    any column in that block that isn't ratio-shaped (e.g. a copy-pasted
    label used just for visual alignment) is ignored.

    Returns (label_grid, standards_df, samples_df), or None if no "PLATE
    MAP" header is found anywhere in the workbook.
    """
    sheets = pd.read_excel(source, sheet_name=None, header=None)
    for _, df in sheets.items():
        pm_pos = _find_cell_containing(df, "PLATE MAP")
        if pm_pos is None:
            continue
        pm_row, pm_col = pm_pos

        dm_pos = _find_cell_containing(df, "DILUTION MAP")
        total_volume = 100.0
        if dm_pos is not None:
            header_text = str(df.iat[dm_pos])
            m = _TOTAL_VOLUME_RE.search(header_text)
            if m:
                total_volume = float(m.group(1))

        pm_stop = dm_pos[1] if dm_pos is not None and dm_pos[0] == pm_row else None
        pm_cols = _annotation_column_block(df, pm_row, pm_col, stop_col=pm_stop)
        dm_cols = _annotation_column_block(df, dm_pos[0], dm_pos[1]) if dm_pos is not None else []

        label_grid = {}
        standards: dict = {}
        sample_label_by_text: dict = {}
        samples: dict = {}
        sample_counter = 1

        for i, col in enumerate(pm_cols):
            plate_col = i + 1
            dm_col = dm_cols[i] if i < len(dm_cols) else None
            for row_idx, row_letter in enumerate(ROWS):
                r = pm_row + 1 + row_idx
                if r >= df.shape[0]:
                    continue
                kind, payload = _classify_annotation_cell(df.iat[r, col])
                if kind is None:
                    continue
                wid = f"{row_letter}{plate_col}"
                if kind == "blank":
                    label_grid[wid] = "BLANK"
                elif kind == "standard":
                    label = f"STD_{payload:g}"
                    label_grid[wid] = label
                    standards[label] = payload
                else:
                    text = payload
                    if text not in sample_label_by_text:
                        sample_label_by_text[text] = f"S{sample_counter:02d}"
                        sample_counter += 1
                    label = sample_label_by_text[text]
                    label_grid[wid] = label
                    if label not in samples:
                        dilution = 1.0
                        if dm_col is not None and r < df.shape[0]:
                            dm_val = df.iat[r, dm_col]
                            dm_text = str(dm_val).strip() if dm_val is not None else ""
                            ratio_match = _DILUTION_RATIO_RE.match(dm_text)
                            if ratio_match:
                                sample_part = float(ratio_match.group(1))
                                if sample_part > 0:
                                    dilution = total_volume / sample_part
                        samples[label] = (text, dilution)

        if not label_grid:
            continue

        standards_df = pd.DataFrame(
            {"Label": list(standards.keys()), "Concentration": list(standards.values())}
        )
        samples_df = pd.DataFrame(
            [{"Label": k, "SampleName": v[0], "Dilution": v[1]} for k, v in samples.items()]
        )
        return label_grid, standards_df, samples_df
    return None


def build_plate_data(
    od_grid: dict,
    label_grid: dict,
    standards: pd.DataFrame,
    samples: pd.DataFrame,
    source_table: Optional[str] = None,
    available_tables: Optional[list] = None,
) -> PlateData:
    """Assemble a PlateData from already-parsed pieces (used by both the
    file-based loaders below and interfaces like the Streamlit app that let
    a user edit the layout/standards/samples directly)."""
    wells = []
    for row_letter in ROWS:
        for col_num in COLS:
            wid = f"{row_letter}{col_num}"
            label = label_grid.get(wid)
            label = str(label).strip() if label is not None and str(label).strip().lower() != "nan" else None
            label = label or None
            od_val, od_marker = parse_od_cell(od_grid.get(wid))
            wells.append(Well(wid, row_letter, col_num, label, od_val, od_marker))
    return PlateData(
        wells=wells,
        standards=standards,
        samples=samples,
        source_table=source_table,
        available_tables=available_tables or [],
    )


def load_plate(raw_path: str, layout_path: Optional[str] = None, table: Optional[str] = None) -> PlateData:
    """Load raw OD data + assay layout into a PlateData.

    If layout_path is omitted, raw_path is tried, in order, as: (1) a
    combined single-workbook template containing "Raw Data", "Plate
    Layout", and "Standards" sheets (plus optional "Samples"); (2) a raw
    instrument export annotated with an embedded "PLATE MAP" (see
    extract_embedded_plate_map) needing no separate layout at all. If
    layout_path is given, raw_path can be any file with one or more
    embedded 8x12 OD grids (e.g. a native Tecan Spark / SparkControl
    export), and the layout/standards/samples come from layout_path instead.
    """
    if layout_path is None:
        raw_sheets = pd.read_excel(raw_path, sheet_name=None, header=None)
        raw_name = _sheet_name(raw_sheets, "Raw Data", "RawData", "Data", "OD")
        if raw_name is not None:
            all_tables = extract_od_tables(raw_path, sheet_name=raw_name)
            label_grid, std_df, samples_df = read_layout_workbook(raw_path)
        else:
            embedded = extract_embedded_plate_map(raw_path)
            if embedded is None:
                raise ValueError(
                    "Workbook is missing a 'Raw Data' sheet with the 8x12 plate grid of OD readings, "
                    "and no embedded 'PLATE MAP' annotation was found either. If this is a native "
                    "instrument export, pass layout_path= pointing at a separate layout workbook instead."
                )
            label_grid, std_df, samples_df = embedded
            all_tables = extract_od_tables(raw_path)
    else:
        all_tables = extract_od_tables(raw_path)
        label_grid, std_df, samples_df = read_layout_workbook(layout_path)

    source_label, od_grid = select_od_table(all_tables, table=table)

    return build_plate_data(
        od_grid, label_grid, std_df, samples_df,
        source_table=source_label,
        available_tables=list(all_tables.keys()),
    )


def load_plate_workbook(path: str) -> PlateData:
    """Backwards-compatible alias for the combined single-workbook template."""
    return load_plate(path)


def layout_grid_to_dataframe(label_grid: Optional[dict] = None) -> pd.DataFrame:
    """8x12 DataFrame (index A-H, columns 1-12) for editing in a UI grid widget."""
    data = {
        col_num: [
            (label_grid or {}).get(f"{row_letter}{col_num}", "") or ""
            for row_letter in ROWS
        ]
        for col_num in COLS
    }
    return pd.DataFrame(data, index=ROWS)


def dataframe_to_layout_grid(df: pd.DataFrame) -> dict:
    """Inverse of layout_grid_to_dataframe: an edited 8x12 DataFrame back to a well->label dict."""
    grid = {}
    for row_letter in ROWS:
        if row_letter not in df.index:
            continue
        for col_num in COLS:
            col_key = col_num if col_num in df.columns else str(col_num)
            if col_key not in df.columns:
                continue
            val = df.at[row_letter, col_key]
            text = str(val).strip() if val is not None else ""
            grid[f"{row_letter}{col_num}"] = text if text.lower() != "nan" else ""
    return grid


def default_layout_dataframe() -> pd.DataFrame:
    grid = {}
    for i, row_letter in enumerate(ROWS):
        grid[f"{row_letter}1"] = f"STD{i + 1}"
        grid[f"{row_letter}2"] = f"STD{i + 1}"
    return layout_grid_to_dataframe(grid)


def default_standards_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        {"Label": [f"STD{i}" for i in range(8, 0, -1)], "Concentration": [None] * 8}
    )


def default_samples_dataframe() -> pd.DataFrame:
    return pd.DataFrame({"Label": ["UNK1"], "SampleName": ["Patient 001"], "Dilution": [1.0]})


def write_layout_workbook(label_grid: dict, standards_df: pd.DataFrame, samples_df: pd.DataFrame, out):
    """Write Plate Layout / Standards / Samples sheets to `out` (a path or
    file-like/BytesIO), in the same shape read_layout_workbook expects back."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    header_fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
    header_font = Font(bold=True)

    wb = Workbook()
    ws_layout = wb.active
    ws_layout.title = "Plate Layout"
    ws_layout.cell(row=1, column=1, value="Well").font = header_font
    for j, col_num in enumerate(COLS):
        c = ws_layout.cell(row=1, column=2 + j, value=col_num)
        c.font = header_font
        c.fill = header_fill
    for i, row_letter in enumerate(ROWS):
        c = ws_layout.cell(row=2 + i, column=1, value=row_letter)
        c.font = header_font
        c.fill = header_fill
        for j, col_num in enumerate(COLS):
            ws_layout.cell(row=2 + i, column=2 + j, value=label_grid.get(f"{row_letter}{col_num}") or None)

    ws_std = wb.create_sheet("Standards")
    ws_std.append(["Label", "Concentration"])
    for c in ws_std[1]:
        c.font, c.fill = header_font, header_fill
    for _, row in standards_df.iterrows():
        ws_std.append([row["Label"], row["Concentration"]])

    ws_smp = wb.create_sheet("Samples")
    ws_smp.append(["Label", "Sample Name", "Dilution Factor"])
    for c in ws_smp[1]:
        c.font, c.fill = header_font, header_fill
    for _, row in samples_df.iterrows():
        ws_smp.append([row["Label"], row.get("SampleName", row["Label"]), row.get("Dilution", 1.0)])

    wb.save(out)
    return out
