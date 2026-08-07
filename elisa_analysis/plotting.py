"""GraphPad Prism-style plotting for ELISA standard curves and sample results."""
from __future__ import annotations

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

from .analysis import AnalysisResult

PRISM_BLUE = "#2E3192"
PRISM_RED = "#EE2E31"
PRISM_BLACK = "#000000"


def _apply_prism_style(ax):
    ax.set_facecolor("white")
    ax.figure.set_facecolor("white")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_linewidth(1.3)
        ax.spines[side].set_color(PRISM_BLACK)
    ax.tick_params(direction="out", length=5, width=1.3, colors=PRISM_BLACK)
    ax.tick_params(which="minor", length=3, width=1.0)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontfamily("sans-serif")
        label.set_fontsize(11)
        label.set_color(PRISM_BLACK)


def plot_standard_curve(result: AnalysisResult, title: str = "Standard Curve", ax=None):
    """Prism-style XY scatter + fitted 4PL/5PL sigmoidal curve, log10 x-axis."""
    own_fig = ax is None
    if own_fig:
        fig, ax = plt.subplots(figsize=(6.4, 5.0), dpi=150)

    fit = result.fit
    std = result.standards_table
    std_pos = std[std["Concentration"] > 0]

    ax.errorbar(
        std_pos["Concentration"],
        std_pos["CorrectedOD"],
        yerr=std_pos["SD_OD"],
        fmt="o",
        markersize=7,
        markerfacecolor=PRISM_BLUE,
        markeredgecolor=PRISM_BLACK,
        markeredgewidth=0.8,
        ecolor=PRISM_BLACK,
        elinewidth=1.1,
        capsize=3,
        capthick=1.1,
        linestyle="none",
        zorder=3,
        label="Standards",
    )

    x_lo = std_pos["Concentration"].min() / 2
    x_hi = std_pos["Concentration"].max() * 2
    x_smooth = np.logspace(np.log10(x_lo), np.log10(x_hi), 400)
    y_smooth = fit.response_at(x_smooth)
    ax.plot(x_smooth, y_smooth, color=PRISM_RED, linewidth=2.0, zorder=2, label=f"{fit.model} fit")

    ax.set_xscale("log")
    ax.set_xlabel(f"Concentration ({result.units})", fontsize=12, fontfamily="sans-serif")
    ax.set_ylabel("OD (450 nm)", fontsize=12, fontfamily="sans-serif")
    ax.set_title(title, fontsize=13, fontfamily="sans-serif", fontweight="bold")
    ax.xaxis.set_major_locator(mticker.LogLocator(base=10))
    ax.grid(False)

    param_str = ", ".join(f"{k}={v:.4g}" for k, v in fit.params.items())
    annotation = f"{fit.model}: R² = {fit.r_squared:.4f}\n{param_str}"
    ax.text(
        0.03,
        0.97,
        annotation,
        transform=ax.transAxes,
        fontsize=8.5,
        va="top",
        ha="left",
        family="monospace",
        bbox=dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor="#999999", linewidth=0.7),
    )

    _apply_prism_style(ax)
    ax.legend(frameon=False, loc="lower right", fontsize=10)

    if own_fig:
        fig.tight_layout()
        return fig
    return ax


def plot_sample_bar(result: AnalysisResult, title: str = "Sample Concentrations", ax=None):
    """Prism-style bar/scatter of final interpolated sample concentrations."""
    own_fig = ax is None
    if own_fig:
        fig, ax = plt.subplots(figsize=(7.5, 5.0), dpi=150)

    samples = result.samples_table.copy()
    if samples.empty:
        ax.text(0.5, 0.5, "No sample wells found", ha="center", va="center")
        _apply_prism_style(ax)
        if own_fig:
            return fig
        return ax

    samples = samples.sort_values("SampleName")
    x_pos = np.arange(len(samples))

    ax.bar(
        x_pos,
        samples["FinalConc"],
        width=0.6,
        color="white",
        edgecolor=PRISM_BLACK,
        linewidth=1.3,
        zorder=2,
    )
    err = samples["SD_OD"] / samples["MeanOD"].replace(0, np.nan) * samples["FinalConc"]
    ax.errorbar(
        x_pos,
        samples["FinalConc"],
        yerr=err.fillna(0),
        fmt="none",
        ecolor=PRISM_BLACK,
        elinewidth=1.1,
        capsize=3,
        zorder=3,
    )

    ax.set_xticks(x_pos)
    ax.set_xticklabels(samples["SampleName"], rotation=45, ha="right", fontsize=9)
    ax.set_ylabel(f"Concentration ({result.units})", fontsize=12, fontfamily="sans-serif")
    ax.set_title(title, fontsize=13, fontfamily="sans-serif", fontweight="bold")
    _apply_prism_style(ax)

    for i, flag in enumerate(samples["Flag"]):
        if flag:
            ax.annotate(
                "*",
                (x_pos[i], samples["FinalConc"].iloc[i]),
                textcoords="offset points",
                xytext=(0, 6),
                ha="center",
                fontsize=13,
                color=PRISM_RED,
                fontweight="bold",
            )

    if own_fig:
        fig.tight_layout()
        return fig
    return ax


def save_all_plots(result: AnalysisResult, out_prefix: str) -> list:
    """Save standard-curve and sample-concentration plots as PNGs. Returns file paths."""
    paths = []

    fig1 = plot_standard_curve(result)
    p1 = f"{out_prefix}_standard_curve.png"
    fig1.savefig(p1, dpi=300, bbox_inches="tight")
    plt.close(fig1)
    paths.append(p1)

    fig2 = plot_sample_bar(result)
    p2 = f"{out_prefix}_sample_concentrations.png"
    fig2.savefig(p2, dpi=300, bbox_inches="tight")
    plt.close(fig2)
    paths.append(p2)

    return paths
