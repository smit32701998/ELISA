#!/usr/bin/env python3
"""Generate a synthetic "annotated" Tecan Spark-style raw export: a single
file with the OD grid AND a hand-added "PLATE MAP" / "DILUTION MAP" next to
it, matching the convention extract_embedded_plate_map() parses. Unlike
scripts/make_tecan_example.py, no separate layout workbook is produced --
uploading this one file is the entire input.

Usage:
    python scripts/make_annotated_tecan_example.py [output_path]
"""
import sys

import numpy as np
from openpyxl import Workbook

sys.path.insert(0, ".")
from elisa_analysis.fitting import logistic_4pl

ROWS = list("ABCDEFGH")
COLS = list(range(1, 13))
TRUE_PARAMS = dict(a=0.05, b=1.15, c=25.0, d=2.9)
STD_CONCS = [1000, 500, 250, 125, 62.5, 31.3, 15.6, 0]
REFERENCE_OD = 0.045

# Column 2 samples + their "sample/diluent" ratio (out of 100 uL total).
COL2 = {
    "A": ("Donor 1 2hr", "10.0/90.0"), "B": ("Donor 2 2hr", "50.0/50.0"),
    "C": ("Donor 3 2hr", "50.0/50.0"), "D": ("Donor 1 24hr", "10.0/90.0"),
    "E": ("Donor 2 24hr", "50.0/50.0"), "F": ("Donor 3 24hr", "50.0/50.0"),
    "G": ("Untreated", "50.0/50.0"), "H": ("BLANK", "BLANK"),
}
# Column 3 repeats Donor 1 2hr as a replicate well (same text -> should be
# grouped with column 2's "Donor 1 2hr" during analysis).
COL3 = {
    "A": ("Donor 1 2hr", "10.0/90.0"), "B": ("Free drug", "20.0/80.0"),
    "C": ("Free drug", "20.0/80.0"), "D": ("", ""), "E": ("", ""),
    "F": ("", ""), "G": ("", ""), "H": ("", ""),
}


def build_annotated_example(seed: int = 11):
    rng = np.random.default_rng(seed)

    raw = {}
    for i, row_letter in enumerate(ROWS):
        conc = STD_CONCS[i]
        true_y = logistic_4pl(np.array([conc if conc > 0 else 1e-6]), **TRUE_PARAMS)[0]
        if conc == 0:
            true_y = TRUE_PARAMS["a"]
        noisy = true_y * (1 + rng.normal(0, 0.05)) + rng.normal(0, 0.004)
        raw[f"{row_letter}1"] = round(max(noisy, 0.0) + REFERENCE_OD, 4)

    sample_true_conc = {
        "Donor 1 2hr": 40.0, "Donor 2 2hr": 18.0, "Donor 3 2hr": 8.0,
        "Donor 1 24hr": 60.0, "Donor 2 24hr": 2.0, "Donor 3 24hr": 33.0,
        "Untreated": 0.5, "Free drug": 70.0,
    }
    for col_num, mapping in ((2, COL2), (3, COL3)):
        for row_letter, (name, _ratio) in mapping.items():
            if not name or name == "BLANK":
                if name == "BLANK":
                    raw[f"{row_letter}{col_num}"] = round(REFERENCE_OD + rng.normal(0, 0.003), 4)
                continue
            true_y = logistic_4pl(np.array([sample_true_conc[name]]), **TRUE_PARAMS)[0]
            noisy = true_y * (1 + rng.normal(0, 0.06)) + rng.normal(0, 0.004)
            raw[f"{row_letter}{col_num}"] = round(max(noisy, 0.0) + REFERENCE_OD, 4)

    reference = {f"{r}{c}": round(REFERENCE_OD + rng.normal(0, 0.003), 4) for r in ROWS for c in COLS}
    difference = {wid: round(raw[wid] - reference[wid], 4) for wid in raw}

    wb = Workbook()
    ws = wb.active
    ws.title = "Result sheet"

    metadata = [
        ["Method name: Method 1"],
        ["Application: SparkControl", None, None, None, "V3.1 SP1"],
        ["Device: Spark", None, None, None, "Serial number: 0000000000"],
        [],
        ["Date:", None, None, None, "2026-08-07"],
        ["Measurement wavelength [nm]", None, None, None, 450],
        ["Reference wavelength [nm]", None, None, None, 570],
        [],
        ["Synthetic CXCL10 Demo"],
    ]
    for row in metadata:
        ws.append(row)

    def grid_row(row_letter, values):
        return [row_letter] + [values.get(f"{row_letter}{c}", "") for c in COLS]

    ws.append(["<>"] + [str(c) for c in COLS])
    for r in ROWS:
        ws.append(grid_row(r, raw))
    ws.append([])
    ws.append(["Reference"])
    ws.append(["<>"] + [str(c) for c in COLS])
    for r in ROWS:
        ws.append(grid_row(r, reference))
    ws.append([])

    ws.append(["Difference"])
    header = ["<>"] + [str(c) for c in COLS] + [None, "PLATE MAP", None, None, None,
                                                 "DILUTION MAP (SAMPLE/DILUENT, total 100 uL)", None, None]
    ws.append(header)
    for row_letter in ROWS:
        diff_vals = [difference.get(f"{row_letter}{c}", "") for c in COLS]
        std_label = f"Std {STD_CONCS[ROWS.index(row_letter)]:g}"
        col2_name, col2_ratio = COL2[row_letter]
        col3_name, col3_ratio = COL3[row_letter]
        row = (
            [row_letter] + diff_vals
            + [None, std_label, col2_name, col3_name]
            + [None, std_label, col2_ratio, col3_ratio]
        )
        ws.append(row)

    return wb


if __name__ == "__main__":
    out_path = sys.argv[1] if len(sys.argv) > 1 else "examples/tecan_annotated_example.xlsx"
    build_annotated_example().save(out_path)
    print(f"Wrote {out_path}")
