import os

import pandas as pd
import pytest

from elisa_analysis.analysis import analyze
from elisa_analysis.io_utils import build_plate_data, load_plate_workbook
from elisa_analysis.plotting import plot_sample_bar, plot_standard_curve

EXAMPLE_PATH = os.path.join(os.path.dirname(__file__), "..", "examples", "example_raw_data.xlsx")


@pytest.fixture(scope="module")
def result():
    if not os.path.exists(EXAMPLE_PATH):
        pytest.skip("example workbook not generated; run scripts/make_example.py first")
    plate = load_plate_workbook(EXAMPLE_PATH)
    return analyze(plate, model="4PL", weight_mode="1/y2")


def test_standard_curve_reserves_headroom_above_data(result):
    # The R^2/params annotation box sits near the top of the axes -- if the
    # y-axis top were left at the data's natural max, it would overlap the
    # highest standard point or the fitted curve.
    fig = plot_standard_curve(result)
    ax = fig.axes[0]
    data_top = max(
        (result.standards_table["CorrectedOD"] + result.standards_table["SD_OD"].fillna(0)).max(),
        result.fit.response_at(result.standards_table["Concentration"].max()),
    )
    _, y1 = ax.get_ylim()
    assert y1 > data_top * 1.15


def test_sample_bar_reserves_headroom_above_tallest_bar(result):
    fig = plot_sample_bar(result)
    ax = fig.axes[0]
    tallest_bar = result.samples_table["FinalConc"].max()
    _, y1 = ax.get_ylim()
    assert y1 > tallest_bar * 1.15


def test_titles_include_analyte_name_when_set(result):
    result.analyte = "Human CXCL10"
    fig1 = plot_standard_curve(result)
    fig2 = plot_sample_bar(result)
    assert fig1.axes[0].get_title() == "Human CXCL10 Standard Curve"
    assert fig2.axes[0].get_title() == "Human CXCL10 Sample Concentrations"


def test_titles_fall_back_when_analyte_not_set(result):
    result.analyte = ""
    fig1 = plot_standard_curve(result)
    fig2 = plot_sample_bar(result)
    assert fig1.axes[0].get_title() == "Standard Curve"
    assert fig2.axes[0].get_title() == "Sample Concentrations"


def test_unmeasurable_sample_gets_no_bar():
    # A full-height placeholder bar (even labeled "OVER") reads at a glance
    # like a real value and can be misread -- a sample with no calculable
    # concentration should draw no bar rectangle at all, just its x-tick
    # and a small text label (checked in test_saturated_wells.py's
    # analysis-level coverage; this confirms the chart itself omits the bar).
    rows = list("ABCDEFGH")
    concs = [100, 50, 25, 12.5, 6.25, 3.125, 1.5625, 0]
    label_grid, od_grid = {}, {}
    for i, row in enumerate(rows):
        label_grid[f"{row}1"] = f"STD{i + 1}"
        od_grid[f"{row}1"] = 0.05 + concs[i] * 0.02
    label_grid["A2"] = "UNK1"
    od_grid["A2"] = 1.0
    label_grid["B2"] = "UNK2"
    od_grid["B2"] = "OVER"

    standards = pd.DataFrame({"Label": [f"STD{i + 1}" for i in range(8)], "Concentration": concs})
    samples = pd.DataFrame({"Label": ["UNK1", "UNK2"], "SampleName": ["Normal", "Saturated"], "Dilution": [1.0, 1.0]})
    plate = build_plate_data(od_grid, label_grid, standards, samples)
    result = analyze(plate, model="4PL", weight_mode="none")

    fig = plot_sample_bar(result)
    ax = fig.axes[0]
    bar_patches = [p for p in ax.patches if p.get_width() > 0 and p.get_height() > 0]
    assert len(bar_patches) == 1  # only the finite sample gets a bar
