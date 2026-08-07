#!/usr/bin/env python3
"""Generate a filled-in example ELISA raw-data workbook for demoing/testing elisa_analysis.

Simulates an 8-point standard curve (duplicates) following a known 4PL curve
plus realistic noise, a blank, and 10 unknown samples (duplicates) at random
concentrations (some intentionally out of range) with dilution factors.

Usage:
    python scripts/make_example.py [output_path] [--seed N]
"""
import sys

import numpy as np
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, ".")
from elisa_analysis.fitting import logistic_4pl

ROWS = list("ABCDEFGH")
COLS = list(range(1, 13))
HEADER_FILL = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
HEADER_FONT = Font(bold=True)

TRUE_PARAMS = dict(a=0.05, b=1.15, c=25.0, d=2.9)  # a=bottom, d=top asymptote OD
STD_CONCS = [100, 50, 25, 12.5, 6.25, 3.125, 1.5625, 0]  # ng/mL, last is the zero standard


def _new_grid_sheet(wb, title, corner="Well"):
    ws = wb.create_sheet(title) if title not in wb.sheetnames else wb[title]
    ws.cell(row=1, column=1, value=corner).font = HEADER_FONT
    for j, col_num in enumerate(COLS):
        c = ws.cell(row=1, column=2 + j, value=col_num)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center")
    for i, row_letter in enumerate(ROWS):
        c = ws.cell(row=2 + i, column=1, value=row_letter)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
    ws.column_dimensions["A"].width = 8
    for j in range(len(COLS)):
        ws.column_dimensions[get_column_letter(2 + j)].width = 10
    return ws


def build_example(seed: int = 42) -> Workbook:
    rng = np.random.default_rng(seed)
    wb = Workbook()
    wb.remove(wb.active)

    ws_layout = _new_grid_sheet(wb, "Plate Layout")
    ws_raw = _new_grid_sheet(wb, "Raw Data")

    label_grid = {f"{r}{c}": "" for r in ROWS for c in COLS}
    od_grid = {f"{r}{c}": None for r in ROWS for c in COLS}

    # Standards: columns 1 and 2, rows A-H = STD1..STD8 (STD8 is the 0 standard)
    for i, row_letter in enumerate(ROWS):
        label = f"STD{i + 1}"
        conc = STD_CONCS[i]
        for col_num in (1, 2):
            label_grid[f"{row_letter}{col_num}"] = label
            true_y = logistic_4pl(np.array([conc if conc > 0 else 1e-6]), **TRUE_PARAMS)[0]
            if conc == 0:
                true_y = TRUE_PARAMS["a"]
            noisy = true_y * (1 + rng.normal(0, 0.05)) + rng.normal(0, 0.004)
            od_grid[f"{row_letter}{col_num}"] = round(max(noisy, 0.0), 4)

    # 10 unknown samples in duplicate across columns 3-12, rows A-E; a couple deliberately
    # out-of-range (very high / very low) to exercise QC flags.
    sample_concs = {
        "UNK1": 40.0,
        "UNK2": 18.0,
        "UNK3": 8.0,
        "UNK4": 60.0,
        "UNK5": 2.0,
        "UNK6": 0.4,   # below LLOQ -> should flag OOR
        "UNK7": 300.0,  # above ULOQ -> should flag OOR
        "UNK8": 15.0,
        "UNK9": 33.0,
        "UNK10": 5.5,
    }
    dilutions = {k: (2.0 if i % 3 == 0 else 1.0) for i, k in enumerate(sample_concs)}

    wells_cycle = [(r, c) for c in range(3, 13) for r in ROWS]
    wi = 0
    for name, true_conc in sample_concs.items():
        dilution = dilutions[name]
        measured_conc = true_conc / dilution  # what's actually in the well after dilution
        true_y = logistic_4pl(np.array([max(measured_conc, 1e-6)]), **TRUE_PARAMS)[0]
        for _rep in range(2):
            row_letter, col_num = wells_cycle[wi]
            wi += 1
            noisy = true_y * (1 + rng.normal(0, 0.06)) + rng.normal(0, 0.004)
            label_grid[f"{row_letter}{col_num}"] = name
            od_grid[f"{row_letter}{col_num}"] = round(max(noisy, 0.0), 4)

    for r in ROWS:
        for c in COLS:
            wid = f"{r}{c}"
            ri = ROWS.index(r)
            ci = c - 1
            ws_layout.cell(row=2 + ri, column=2 + ci, value=label_grid[wid] or None)
            ws_raw.cell(row=2 + ri, column=2 + ci, value=od_grid[wid])

    ws_std = wb.create_sheet("Standards")
    ws_std.append(["Label", "Concentration"])
    for c in ws_std[1]:
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
    for i, conc in enumerate(STD_CONCS):
        ws_std.append([f"STD{i + 1}", conc])
    ws_std.column_dimensions["A"].width = 12
    ws_std.column_dimensions["B"].width = 14

    ws_smp = wb.create_sheet("Samples")
    ws_smp.append(["Label", "Sample Name", "Dilution Factor"])
    for c in ws_smp[1]:
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
    names = {
        "UNK1": "Patient 001", "UNK2": "Patient 002", "UNK3": "Patient 003",
        "UNK4": "Patient 004", "UNK5": "Patient 005", "UNK6": "Patient 006",
        "UNK7": "Patient 007", "UNK8": "Patient 008", "UNK9": "Patient 009",
        "UNK10": "Patient 010",
    }
    for label, dilution in dilutions.items():
        ws_smp.append([label, names[label], dilution])
    ws_smp.column_dimensions["A"].width = 12
    ws_smp.column_dimensions["B"].width = 16
    ws_smp.column_dimensions["C"].width = 16

    return wb


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--seed")]
    seed_arg = next((a for a in sys.argv[1:] if a.startswith("--seed")), None)
    seed = int(seed_arg.split("=")[1]) if seed_arg and "=" in seed_arg else 42
    out_path = args[0] if args else "examples/example_raw_data.xlsx"
    build_example(seed=seed).save(out_path)
    print(f"Wrote example workbook to {out_path}")
