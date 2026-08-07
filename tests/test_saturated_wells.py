import numpy as np
import pandas as pd
import pytest

from elisa_analysis.analysis import analyze
from elisa_analysis.io_utils import build_plate_data, parse_od_cell

STD_CONCS = [100, 50, 25, 12.5, 6.25, 3.125, 1.5625, 0]
ROWS = list("ABCDEFGH")


def _make_plate(sample_col3_a="OVER", sample_col3_b=1.2):
    # Standards in column 1 (A1 deliberately saturated), a fully-saturated
    # sample in column 2 (both replicates "OVER"), and a partially-saturated
    # sample in column 3 (one real reading, one "OVER").
    od_grid = {}
    label_grid = {}
    for i, row in enumerate(ROWS):
        label_grid[f"{row}1"] = f"STD{i + 1}"
        od_grid[f"{row}1"] = "OVER" if i == 0 else 0.05 + STD_CONCS[i] * 0.01
    for row in ("A", "B"):
        label_grid[f"{row}2"] = "UNK1"
        od_grid[f"{row}2"] = "OVER"
    label_grid["A3"] = "UNK2"
    od_grid["A3"] = sample_col3_a
    label_grid["B3"] = "UNK2"
    od_grid["B3"] = sample_col3_b

    standards = pd.DataFrame({"Label": [f"STD{i+1}" for i in range(8)], "Concentration": STD_CONCS})
    samples = pd.DataFrame({"Label": ["UNK1", "UNK2"], "SampleName": ["S1", "S2"], "Dilution": [1.0, 1.0]})
    return build_plate_data(od_grid, label_grid, standards, samples)


def test_parse_od_cell_distinguishes_over_from_blank():
    assert parse_od_cell("OVER") == (None, "OVER")
    assert parse_od_cell("") == (None, None)
    assert parse_od_cell(None) == (None, None)
    assert parse_od_cell(float("nan")) == (None, None)
    assert parse_od_cell(1.23) == (1.23, None)
    assert parse_od_cell("1.23") == (1.23, None)


def test_saturated_standard_well_has_no_od_but_is_flagged():
    plate = _make_plate()
    std1 = next(w for w in plate.wells if w.well_id == "A1")
    assert std1.od is None
    assert std1.flag == "OVER"

    result = analyze(plate, model="4PL", weight_mode="none")
    row = result.standards_table.set_index("Label").loc["STD1"]
    assert pd.isna(row["MeanOD"])
    assert "OVER" in row["Flag"]


def test_fully_saturated_sample_is_reported_not_dropped():
    plate = _make_plate()
    result = analyze(plate, model="4PL", weight_mode="none")
    samples = result.samples_table.set_index("Label")
    assert "UNK1" in samples.index  # previously silently dropped
    row = samples.loc["UNK1"]
    assert pd.isna(row["FinalConc"])
    assert row["Flag"] == "OVER"


def test_partially_saturated_sample_uses_valid_replicate_and_flags_partial():
    plate = _make_plate(sample_col3_a="OVER", sample_col3_b=0.4)
    result = analyze(plate, model="4PL", weight_mode="none")
    row = result.samples_table.set_index("Label").loc["UNK2"]
    assert row["N"] == 1  # only the numeric replicate counted
    assert row["MeanOD"] == pytest.approx(0.4)
    assert "OVER" in row["Flag"] and "partial" in row["Flag"]
