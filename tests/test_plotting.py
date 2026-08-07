import os

import pytest

from elisa_analysis.analysis import analyze
from elisa_analysis.io_utils import load_plate_workbook
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


def test_sample_bar_dilution_labels_clear_tick_labels(result):
    # Regression test: the dilution-factor row used to sit at a fixed pixel
    # offset that overlapped long, rotated sample-name tick labels (e.g.
    # "Patient 001"). It's now measured from the actual rendered tick label
    # height, so this should render without the two rows colliding.
    fig = plot_sample_bar(result)
    ax = fig.axes[0]
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    tick_bboxes = [lbl.get_window_extent(renderer) for lbl in ax.get_xticklabels()]
    assert all(bbox.height > 0 for bbox in tick_bboxes)
