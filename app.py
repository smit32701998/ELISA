"""Streamlit interface for elisa_analysis: upload raw plate-reader data, edit
the assay layout interactively, and get a 4PL/5PL analysis with Prism-style
charts -- no command line required.

Run with:
    streamlit run app.py
"""
import io
import re
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from elisa_analysis.analysis import analyze
from elisa_analysis.io_utils import (
    build_plate_data,
    dataframe_to_layout_grid,
    default_layout_dataframe,
    default_samples_dataframe,
    default_standards_dataframe,
    detect_raw_data_sheet,
    extract_od_tables,
    layout_grid_to_dataframe,
    normalize_samples_df,
    normalize_standards_df,
    read_layout_workbook,
    select_od_table,
    write_layout_workbook,
)
from elisa_analysis.plotting import plot_sample_bar, plot_standard_curve
from elisa_analysis.report import write_report
from scripts.make_layout_template import build_layout_template
from scripts.make_template import build_template

st.set_page_config(page_title="ELISA Analysis", layout="wide")
st.title("ELISA 96-Well Plate Analysis")
st.caption(
    "4PL/5PL curve fitting with Prism-style charts. Upload your plate reader's raw "
    "OD export, describe the plate layout, and get back a full results report."
)

if "analyte" not in st.session_state:
    st.session_state["analyte"] = ""
if "analyte_prompted" not in st.session_state:
    st.session_state["analyte_prompted"] = False


@st.dialog("What is this ELISA measuring?")
def _prompt_analyte():
    st.write(
        "Name the protein/analyte being measured -- it will be used as the title "
        "on every chart and in the Excel report, so the output is always clearly "
        "labeled with what it's showing."
    )
    name = st.text_input(
        "Protein / analyte name", value=st.session_state["analyte"],
        placeholder="e.g. Human IL-6, CXCL10",
    )
    col1, col2 = st.columns(2)
    if col1.button("Continue", type="primary", width="stretch"):
        st.session_state["analyte"] = name.strip()
        st.session_state["analyte_prompted"] = True
        st.rerun()
    if col2.button("Skip for now", width="stretch"):
        st.session_state["analyte_prompted"] = True
        st.rerun()


if not st.session_state["analyte_prompted"]:
    _prompt_analyte()

label_col, button_col = st.columns([5, 1])
label_col.markdown(
    f"**Measuring:** {st.session_state['analyte']}" if st.session_state["analyte"]
    else "**Measuring:** _not set_"
)
if button_col.button("Set/edit"):
    st.session_state["analyte_prompted"] = False
    st.rerun()


@st.cache_data(show_spinner=False)
def _extract_tables_cached(file_bytes: bytes):
    # If this is a combined template (also has Plate Layout/Standards sheets),
    # restrict the OD-grid scan to the Raw Data sheet -- otherwise the Plate
    # Layout sheet's own 8x12 grid of well labels would be mistaken for a
    # second OD table.
    raw_sheet = detect_raw_data_sheet(io.BytesIO(file_bytes))
    return extract_od_tables(io.BytesIO(file_bytes), sheet_name=raw_sheet)


@st.cache_data(show_spinner=False)
def _try_read_combined_layout(file_bytes: bytes):
    try:
        return read_layout_workbook(io.BytesIO(file_bytes))
    except ValueError:
        return None


st.header("1. Raw data")
raw_file = st.file_uploader(
    "Upload your plate reader's raw-data export (.xlsx) -- a native instrument "
    "export (e.g. Tecan Spark) or the combined template both work.",
    type=["xlsx"],
)

if raw_file is None:
    st.info("Upload a raw-data workbook to get started, or grab a blank template below.")
    col1, col2 = st.columns(2)
    with col1:
        buf = io.BytesIO()
        build_template().save(buf)
        st.download_button(
            "Download combined template (.xlsx)",
            buf.getvalue(),
            file_name="elisa_template.xlsx",
            help="One workbook with Raw Data + Plate Layout + Standards + Samples sheets.",
        )
    with col2:
        buf2 = io.BytesIO()
        build_layout_template().save(buf2)
        st.download_button(
            "Download layout-only template (.xlsx)",
            buf2.getvalue(),
            file_name="elisa_layout_template.xlsx",
            help="Pair this with a raw instrument export instead of the combined template.",
        )
    st.stop()

raw_bytes = raw_file.getvalue()

try:
    all_tables = _extract_tables_cached(raw_bytes)
except ValueError as exc:
    st.error(str(exc))
    st.stop()

table_override = None
if len(all_tables) > 1:
    labels = list(all_tables.keys())
    default_label, _ = select_od_table(all_tables)
    table_override = st.selectbox(
        "This file has multiple OD tables -- pick one",
        labels,
        index=labels.index(default_label),
        help="Dual-wavelength reads export raw / reference / difference tables; "
        "'Difference' (reference-corrected) is preselected when present.",
    )

source_label, od_grid = select_od_table(all_tables, table=table_override)
st.success(
    f"Using OD table **{source_label}**"
    + (f" (of {len(all_tables)} found in this file)" if len(all_tables) > 1 else "")
)

combined_layout = _try_read_combined_layout(raw_bytes)

st.header("2. Assay layout")
st.caption(
    "Which wells are standards/samples, their concentrations, and dilution factors. "
    "Edit the grids below, or upload a previously saved layout workbook to reuse it."
)

layout_file = st.file_uploader(
    "Optional: upload a saved layout workbook (.xlsx)", type=["xlsx"], key="layout_upload"
)

if "label_grid_df" not in st.session_state:
    if combined_layout is not None:
        label_grid0, std0, smp0 = combined_layout
        st.session_state["label_grid_df"] = layout_grid_to_dataframe(label_grid0)
        st.session_state["standards_df"] = std0 if not std0.empty else default_standards_dataframe()
        st.session_state["samples_df"] = smp0 if not smp0.empty else default_samples_dataframe()
    else:
        st.session_state["label_grid_df"] = default_layout_dataframe()
        st.session_state["standards_df"] = default_standards_dataframe()
        st.session_state["samples_df"] = default_samples_dataframe()

if layout_file is not None:
    try:
        label_grid_u, std_u, smp_u = read_layout_workbook(io.BytesIO(layout_file.getvalue()))
        st.session_state["label_grid_df"] = layout_grid_to_dataframe(label_grid_u)
        st.session_state["standards_df"] = std_u
        st.session_state["samples_df"] = smp_u if not smp_u.empty else default_samples_dataframe()
        st.success("Layout loaded from the uploaded workbook.")
    except ValueError as exc:
        st.error(str(exc))

st.subheader("Plate layout")
st.caption(
    "STDn for standards (must match the Standards table), BLANK for zero/blank wells, "
    "any short code (e.g. UNK1) for a sample. Repeat a label across wells for replicates."
)
layout_df = st.data_editor(st.session_state["label_grid_df"], key="layout_editor", width="stretch")

col_a, col_b = st.columns(2)
with col_a:
    st.subheader("Standards")
    standards_df = st.data_editor(
        st.session_state["standards_df"], num_rows="dynamic", key="standards_editor", width="stretch"
    )
with col_b:
    st.subheader("Samples (optional)")
    st.caption("Leave a sample out of this table to default it to Dilution = 1.")
    samples_df = st.data_editor(
        st.session_state["samples_df"], num_rows="dynamic", key="samples_editor", width="stretch"
    )

label_grid = dataframe_to_layout_grid(layout_df)
std_clean = normalize_standards_df(standards_df)
samples_nonblank = samples_df.dropna(how="all")
smp_clean = (
    normalize_samples_df(samples_nonblank)
    if not samples_nonblank.empty
    else pd.DataFrame(columns=["Label", "SampleName", "Dilution"])
)

layout_buf = io.BytesIO()
write_layout_workbook(label_grid, std_clean, smp_clean, layout_buf)
st.download_button(
    "Download this layout for reuse next time",
    layout_buf.getvalue(),
    file_name="elisa_layout.xlsx",
)

st.header("3. Options")
c1, c2, c3, c4 = st.columns(4)
with c1:
    model = st.selectbox("Curve model", ["4PL", "5PL"])
with c2:
    weight = st.selectbox(
        "Weighting", ["1/y2", "1/y", "none"],
        help="1/Y² is the standard choice for ELISA since replicate variance grows with signal.",
    )
with c3:
    units = st.text_input("Concentration units", value="pg/mL")
with c4:
    blank_subtract = st.checkbox("Subtract blank", value=True)

if st.button("Run analysis", type="primary"):
    if std_clean.empty:
        st.error("Enter at least one standard with a valid Label and Concentration first.")
        st.stop()

    plate = build_plate_data(
        od_grid, label_grid, std_clean, smp_clean,
        source_table=source_label, available_tables=list(all_tables.keys()),
    )
    try:
        result = analyze(
            plate, model=model, weight_mode=weight, blank_subtract=blank_subtract,
            units=units, analyte=st.session_state["analyte"],
        )
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    st.header("Results")
    m1, m2, m3 = st.columns(3)
    m1.metric("Model", result.fit.model)
    m2.metric("R²", f"{result.fit.r_squared:.4f}")
    m3.metric("Blank OD subtracted", f"{result.blank_od:.4f}")

    for w in result.fit.warnings:
        st.warning(w)

    fig1 = plot_standard_curve(result)
    fig2 = plot_sample_bar(result)
    p1, p2 = st.columns(2)
    p1.pyplot(fig1)
    p2.pyplot(fig2)

    st.subheader("Standards")
    st.dataframe(result.standards_table, width="stretch")

    st.subheader("Samples")
    if result.samples_table.empty:
        st.info("No sample wells were found in the plate layout.")
    else:
        st.dataframe(result.samples_table, width="stretch")

    with tempfile.TemporaryDirectory() as tmp:
        report_path = str(Path(tmp) / "report.xlsx")
        write_report(result, report_path, str(Path(tmp) / "plot"))
        report_name = (
            f"{re.sub(r'[^A-Za-z0-9]+', '_', result.analyte).strip('_')}_elisa_report.xlsx"
            if result.analyte else "elisa_report.xlsx"
        )
        with open(report_path, "rb") as f:
            st.download_button(
                "Download full Excel report",
                f.read(),
                file_name=report_name,
                type="primary",
            )
