import os

import pytest

from elisa_analysis.analysis import analyze
from elisa_analysis.io_utils import extract_embedded_plate_map, load_plate

EXAMPLE_PATH = os.path.join(os.path.dirname(__file__), "..", "examples", "tecan_annotated_example.xlsx")


@pytest.fixture(scope="module")
def parsed():
    if not os.path.exists(EXAMPLE_PATH):
        pytest.skip("annotated example not generated; run scripts/make_annotated_tecan_example.py first")
    result = extract_embedded_plate_map(EXAMPLE_PATH)
    assert result is not None
    return result


def test_standards_parsed_from_plate_map_text(parsed):
    label_grid, standards_df, samples_df = parsed
    concs = dict(zip(standards_df["Label"], standards_df["Concentration"]))
    assert concs == {
        "STD_1000": 1000.0, "STD_500": 500.0, "STD_250": 250.0, "STD_125": 125.0,
        "STD_62.5": 62.5, "STD_31.3": 31.3, "STD_15.6": 15.6, "STD_0": 0.0,
    }


def test_dilution_ratio_converted_to_factor(parsed):
    _, _, samples_df = parsed
    by_name = samples_df.set_index("SampleName")["Dilution"]
    assert by_name["Donor 1 2hr"] == pytest.approx(10.0)  # 10.0/90.0 -> 100/10
    assert by_name["Donor 2 2hr"] == pytest.approx(2.0)  # 50.0/50.0 -> 100/50
    assert by_name["Free drug"] == pytest.approx(5.0)  # 20.0/80.0 -> 100/20


def test_replicate_wells_with_matching_text_share_a_label(parsed):
    label_grid, _, _ = parsed
    # "Donor 1 2hr" appears in both A2 and A3 -- same underlying sample.
    assert label_grid["A2"] == label_grid["A3"]
    # "Free drug" appears in both B3 and C3.
    assert label_grid["B3"] == label_grid["C3"]


def test_blank_well_recognized(parsed):
    label_grid, _, _ = parsed
    assert label_grid["H2"] == "BLANK"


def test_load_plate_needs_no_separate_layout_file(parsed):
    # The whole point: uploading just this one file is enough.
    plate = load_plate(EXAMPLE_PATH)
    assert len(plate.wells) == 96
    non_null = [w for w in plate.wells if w.label is not None]
    assert len(non_null) > 0

    result = analyze(plate, model="4PL", weight_mode="1/y2")
    assert len(result.samples_table) > 0
