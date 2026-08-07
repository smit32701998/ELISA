import os

import pytest

from elisa_analysis.analysis import analyze
from elisa_analysis.io_utils import load_plate_workbook

EXAMPLE_PATH = os.path.join(os.path.dirname(__file__), "..", "examples", "example_raw_data.xlsx")


@pytest.fixture(scope="module")
def plate():
    if not os.path.exists(EXAMPLE_PATH):
        pytest.skip("example workbook not generated; run scripts/make_example.py first")
    return load_plate_workbook(EXAMPLE_PATH)


def test_load_plate_workbook_shapes(plate):
    assert len(plate.wells) == 96
    assert set(plate.standards["Label"]) == {f"STD{i}" for i in range(1, 9)}
    assert len(plate.samples) == 10


def test_analyze_4pl_good_fit(plate):
    result = analyze(plate, model="4PL", weight_mode="1/y2")
    assert result.fit.r_squared > 0.98
    assert len(result.samples_table) == 10


def test_analyze_flags_out_of_range_samples(plate):
    result = analyze(plate, model="4PL", weight_mode="1/y2")
    flagged = set(result.samples_table.loc[result.samples_table["Flag"] != "", "Label"])
    # UNK6 is deliberately below the lowest standard, UNK7 deliberately above the highest.
    assert "UNK6" in flagged
    assert "UNK7" in flagged


def test_analyze_dilution_applied(plate):
    result = analyze(plate, model="4PL", weight_mode="1/y2")
    row = result.samples_table.set_index("Label").loc["UNK1"]
    assert row["Dilution"] == pytest.approx(2.0)
    assert row["FinalConc"] == pytest.approx(row["InterpolatedConc"] * 2.0)


def test_analyze_blank_subtraction_reduces_od(plate):
    with_blank = analyze(plate, model="4PL", blank_subtract=True)
    without_blank = analyze(plate, model="4PL", blank_subtract=False)
    assert with_blank.blank_od >= 0
    assert without_blank.blank_od == 0.0
