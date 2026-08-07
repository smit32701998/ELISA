"""End-to-end ELISA plate analysis pipeline: raw OD -> standard curve -> sample results."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from .fitting import FitResult, fit_curve
from .io_utils import BLANK_LABELS, PlateData

CV_WARN_THRESHOLD = 15.0  # % CV above which a replicate group is flagged


@dataclass
class AnalysisResult:
    plate_df: pd.DataFrame
    fit: FitResult
    standards_table: pd.DataFrame
    samples_table: pd.DataFrame
    blank_od: float
    units: str
    source_table: str = None
    available_tables: list = None
    analyte: str = ""


def _well_dataframe(plate: PlateData) -> pd.DataFrame:
    rows = [
        {"Well": w.well_id, "Row": w.row, "Col": w.col, "Label": w.label, "OD": w.od, "RawFlag": w.flag}
        for w in plate.wells
    ]
    return pd.DataFrame(rows)


def _replicate_stats(df: pd.DataFrame) -> pd.DataFrame:
    g = df.dropna(subset=["Label", "OD"]).groupby("Label")["OD"]
    stats = g.agg(MeanOD="mean", SD_OD="std", N="count").reset_index()
    stats["SD_OD"] = stats["SD_OD"].fillna(0.0)
    stats["PctCV"] = np.where(
        stats["MeanOD"] != 0, 100.0 * stats["SD_OD"] / stats["MeanOD"].abs(), np.nan
    )
    return stats


def _label_markers(plate_df: pd.DataFrame) -> dict:
    """label -> sorted distinct non-numeric instrument markers (e.g. ["OVER"])
    seen among its wells, so a saturated/error reading isn't silently dropped."""
    out = {}
    for label, group in plate_df.dropna(subset=["Label"]).groupby("Label"):
        markers = sorted({m for m in group["RawFlag"] if isinstance(m, str) and m})
        if markers:
            out[label] = markers
    return out


def _flag_text(pct_cv, markers, all_missing) -> str:
    flags = []
    if markers:
        marker_str = "/".join(markers)
        flags.append(marker_str if all_missing else f"{marker_str} (partial)")
    if pd.notna(pct_cv) and pct_cv > CV_WARN_THRESHOLD:
        flags.append(f"CV>{CV_WARN_THRESHOLD:.0f}%")
    return ", ".join(flags)


def analyze(
    plate: PlateData,
    model: Literal["4PL", "5PL"] = "4PL",
    weight_mode: Literal["none", "1/y", "1/y2"] = "1/y2",
    blank_subtract: bool = True,
    units: str = "conc. units",
    analyte: str = "",
) -> AnalysisResult:
    plate_df = _well_dataframe(plate)
    stats = _replicate_stats(plate_df)
    label_markers = _label_markers(plate_df)

    blank_labels = {lbl.upper() for lbl in BLANK_LABELS}
    std_labels = set(plate.standards["Label"])
    zero_std_labels = set(plate.standards.loc[plate.standards["Concentration"] == 0, "Label"])
    is_blank = plate_df["Label"].astype(str).str.upper().isin(blank_labels) | plate_df["Label"].isin(
        zero_std_labels
    )
    blank_od = float(plate_df.loc[is_blank, "OD"].mean()) if is_blank.any() else 0.0
    blank_od = 0.0 if np.isnan(blank_od) else blank_od
    blank_od_used = blank_od if blank_subtract else 0.0

    stats["CorrectedOD"] = stats["MeanOD"] - blank_od_used

    # --- Standard curve fit (replicate-level points, blank-corrected) ---
    std_merge = plate.standards.merge(
        plate_df.dropna(subset=["Label", "OD"]), on="Label", how="left"
    )
    std_merge["CorrectedOD"] = std_merge["OD"] - blank_od_used
    fit_points = std_merge[std_merge["Concentration"] > 0].dropna(subset=["CorrectedOD"])
    fit = fit_curve(
        fit_points["Concentration"].to_numpy(),
        fit_points["CorrectedOD"].to_numpy(),
        model=model,
        weight_mode=weight_mode,
    )

    # --- Standards summary table (back-fit / % recovery QC) ---
    std_stats = plate.standards.merge(stats, on="Label", how="left")
    std_stats["BackCalcConc"] = fit.concentration_at(std_stats["CorrectedOD"].to_numpy())
    with np.errstate(invalid="ignore", divide="ignore"):
        std_stats["PctRecovery"] = np.where(
            std_stats["Concentration"] > 0,
            100.0 * std_stats["BackCalcConc"] / std_stats["Concentration"],
            np.nan,
        )
    std_stats = std_stats.sort_values("Concentration").reset_index(drop=True)
    std_stats["Flag"] = [
        _flag_text(row["PctCV"], label_markers.get(row["Label"], []), pd.isna(row["MeanOD"]))
        for _, row in std_stats.iterrows()
    ]

    # --- Samples ---
    pos_concs = plate.standards.loc[plate.standards["Concentration"] > 0, "Concentration"]
    std_conc_lo, std_conc_hi = float(pos_concs.min()), float(pos_concs.max())
    # A response beyond the fit's asymptote has no real solution for the
    # inverse formula (it would require a negative number raised to a
    # fractional power) -- concentration_at() returns NaN for those. That's
    # mathematically correct (the curve can never produce that response at
    # any finite concentration) but useless for reporting: comparing the
    # response to what the highest/lowest *tested* standard itself predicts
    # tells us which side it's beyond, so we can report a censored bound
    # ("> highest standard" / "< lowest standard") instead of a blank value.
    resp_at_std_hi = float(fit.response_at(np.array([std_conc_hi]))[0])
    resp_at_std_lo = float(fit.response_at(np.array([std_conc_lo]))[0])
    assay_increasing = resp_at_std_hi >= resp_at_std_lo

    non_std_labels = set(plate_df["Label"].dropna().unique()) - std_labels - blank_labels
    sample_info = plate.samples.set_index("Label") if not plate.samples.empty else pd.DataFrame()
    sample_rows = []
    for label in sorted(non_std_labels):
        if label.upper() in blank_labels:
            continue
        row_stats = stats[stats["Label"] == label]
        markers = label_markers.get(label, [])
        if row_stats.empty and not markers:
            continue  # no numeric data and no instrument marker -- nothing to report

        if label in getattr(sample_info, "index", []):
            sample_name = sample_info.loc[label, "SampleName"]
            dilution = float(sample_info.loc[label, "Dilution"])
        else:
            sample_name = label
            dilution = 1.0

        if row_stats.empty:
            # every replicate for this label was a non-numeric instrument
            # reading (e.g. "OVER") -- report it instead of dropping it.
            sample_rows.append(
                {
                    "Label": label,
                    "SampleName": sample_name,
                    "N": 0,
                    "MeanOD": np.nan,
                    "SD_OD": np.nan,
                    "PctCV": np.nan,
                    "CorrectedOD": np.nan,
                    "Dilution": dilution,
                    "InterpolatedConc": np.nan,
                    "FinalConc": np.nan,
                    "Flag": _flag_text(np.nan, markers, True),
                }
            )
            continue

        mean_od = float(row_stats["MeanOD"].iloc[0])
        sd_od = float(row_stats["SD_OD"].iloc[0])
        n = int(row_stats["N"].iloc[0])
        pct_cv = row_stats["PctCV"].iloc[0]
        corrected_od = mean_od - blank_od_used

        interp_conc = float(fit.concentration_at(np.array([corrected_od]))[0])
        bound_note = None
        if not np.isfinite(interp_conc):
            above_hi = corrected_od >= resp_at_std_hi if assay_increasing else corrected_od <= resp_at_std_hi
            if above_hi:
                interp_conc, bound_note = std_conc_hi, f">{std_conc_hi:g}"
            else:
                interp_conc, bound_note = std_conc_lo, f"<{std_conc_lo:g}"
        final_conc = interp_conc * dilution if np.isfinite(interp_conc) else np.nan

        lo, hi = fit.valid_response_range()
        response_out_of_range = not (min(lo, hi) <= corrected_od <= max(lo, hi))
        conc_out_of_range = bound_note is not None or not (
            std_conc_lo <= interp_conc <= std_conc_hi
        )

        flags = []
        if response_out_of_range or conc_out_of_range:
            flags.append(f"OOR ({bound_note})" if bound_note else "OOR")
        marker_flag = _flag_text(pct_cv, markers, False)
        if marker_flag:
            flags.append(marker_flag)

        sample_rows.append(
            {
                "Label": label,
                "SampleName": sample_name,
                "N": n,
                "MeanOD": mean_od,
                "SD_OD": sd_od,
                "PctCV": pct_cv,
                "CorrectedOD": corrected_od,
                "Dilution": dilution,
                "InterpolatedConc": interp_conc,
                "FinalConc": final_conc,
                "Flag": ", ".join(flags),
            }
        )
    samples_table = pd.DataFrame(sample_rows)

    return AnalysisResult(
        plate_df=plate_df,
        fit=fit,
        standards_table=std_stats,
        samples_table=samples_table,
        blank_od=blank_od_used,
        units=units,
        source_table=plate.source_table,
        available_tables=plate.available_tables,
        analyte=analyte.strip(),
    )
