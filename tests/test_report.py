import os

import pytest

from elisa_analysis.analysis import analyze
from elisa_analysis.io_utils import load_plate_workbook
from elisa_analysis.report import write_report

EXAMPLE_PATH = os.path.join(os.path.dirname(__file__), "..", "examples", "example_raw_data.xlsx")


def test_write_report_with_no_saturated_wells(tmp_path):
    # Regression test: a dataset with no "OVER"-style markers leaves the
    # plate_df's RawFlag column entirely empty/NaN, which previously broke
    # the report's column-width sizing (int(nan) raised ValueError).
    if not os.path.exists(EXAMPLE_PATH):
        pytest.skip("example workbook not generated; run scripts/make_example.py first")
    plate = load_plate_workbook(EXAMPLE_PATH)
    result = analyze(plate, model="4PL", weight_mode="1/y2")

    out_path = str(tmp_path / "report.xlsx")
    write_report(result, out_path, str(tmp_path / "plot"))

    assert os.path.exists(out_path)
