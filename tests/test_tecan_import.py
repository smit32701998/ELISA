import os

import pytest

from elisa_analysis.analysis import analyze
from elisa_analysis.io_utils import extract_od_tables, load_plate, select_od_table

RAW_PATH = os.path.join(os.path.dirname(__file__), "..", "examples", "tecan_spark_raw_example.xlsx")
LAYOUT_PATH = os.path.join(os.path.dirname(__file__), "..", "examples", "tecan_spark_layout_example.xlsx")


@pytest.fixture(scope="module")
def paths_exist():
    if not (os.path.exists(RAW_PATH) and os.path.exists(LAYOUT_PATH)):
        pytest.skip("Tecan example workbooks not generated; run scripts/make_tecan_example.py first")


def test_extract_od_tables_finds_all_three(paths_exist):
    tables = extract_od_tables(RAW_PATH)
    assert set(tables.keys()) >= {"Reference", "Difference"}
    assert len(tables) == 3


def test_select_od_table_prefers_difference(paths_exist):
    tables = extract_od_tables(RAW_PATH)
    label, grid = select_od_table(tables)
    assert label == "Difference"
    assert grid["A1"] is not None


def test_select_od_table_explicit_override(paths_exist):
    tables = extract_od_tables(RAW_PATH)
    label, grid = select_od_table(tables, table="reference")
    assert label == "Reference"


def test_load_plate_with_separate_layout(paths_exist):
    plate = load_plate(RAW_PATH, layout_path=LAYOUT_PATH)
    assert plate.source_table == "Difference"
    assert len(plate.available_tables) == 3
    assert len(plate.wells) == 96
    non_null = [w for w in plate.wells if w.od is not None]
    assert len(non_null) == 26  # 8 standards x2 + 5 samples x2


def test_full_analysis_on_tecan_import(paths_exist):
    plate = load_plate(RAW_PATH, layout_path=LAYOUT_PATH)
    result = analyze(plate, model="4PL", weight_mode="1/y2")
    assert result.fit.r_squared > 0.99
    assert len(result.samples_table) == 5
