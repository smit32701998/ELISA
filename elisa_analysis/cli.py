#!/usr/bin/env python3
"""Command-line entry point for ELISA plate analysis.

Example:
    python -m elisa_analysis.cli --input examples/example_raw_data.xlsx \\
        --output-dir results --model 4PL --weight 1/y2
"""
from __future__ import annotations

import argparse
import os
import sys

from .analysis import analyze
from .io_utils import load_plate
from .report import write_report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", "-i", required=True,
        help="Path to the raw-data Excel workbook. Either a combined template (Raw Data + "
        "Plate Layout + Standards sheets) or, when --layout is also given, a native "
        "instrument export (e.g. a Tecan Spark 'Result sheet').",
    )
    parser.add_argument(
        "--layout", "-l", default=None,
        help="Path to a separate layout workbook (Plate Layout + Standards + Samples sheets), "
        "for use when --input is a raw instrument export rather than the combined template. "
        "See scripts/make_layout_template.py.",
    )
    parser.add_argument(
        "--table", "-t", default=None,
        help="If the raw-data source contains multiple OD grids (e.g. raw/Reference/Difference "
        "from a dual-wavelength read), pick one by name (substring match). Defaults to the "
        "reference-corrected 'Difference' table if present, else the only/first table found.",
    )
    parser.add_argument("--output-dir", "-o", default="results", help="Directory to write the report and plots into.")
    parser.add_argument("--model", choices=["4PL", "5PL"], default="4PL", help="Logistic model to fit.")
    parser.add_argument(
        "--weight", choices=["none", "1/y", "1/y2"], default="1/y2", help="Regression weighting scheme."
    )
    parser.add_argument("--units", default="conc. units", help="Concentration unit label for axes/tables.")
    parser.add_argument("--no-blank-subtract", action="store_true", help="Skip blank OD subtraction.")
    args = parser.parse_args(argv)

    os.makedirs(args.output_dir, exist_ok=True)

    plate = load_plate(args.input, layout_path=args.layout, table=args.table)
    if len(plate.available_tables) > 1:
        print(f"Multiple OD tables found {plate.available_tables}; using '{plate.source_table}'.")
    result = analyze(
        plate,
        model=args.model,
        weight_mode=args.weight,
        blank_subtract=not args.no_blank_subtract,
        units=args.units,
    )

    base = os.path.splitext(os.path.basename(args.input))[0]
    report_path = os.path.join(args.output_dir, f"{base}_report.xlsx")
    plot_prefix = os.path.join(args.output_dir, base)
    write_report(result, report_path, plot_prefix)

    print(f"Model: {result.fit.model}   R^2 = {result.fit.r_squared:.5f}")
    print(f"Parameters: {result.fit.params}")
    for w in result.fit.warnings:
        print(f"WARNING: {w}")
    print(f"Report written to {report_path}")
    if not result.samples_table.empty:
        n_flagged = (result.samples_table["Flag"] != "").sum()
        print(f"Samples analyzed: {len(result.samples_table)} ({n_flagged} flagged)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
