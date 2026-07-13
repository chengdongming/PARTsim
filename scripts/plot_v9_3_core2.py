#!/usr/bin/env python3
"""Render CORE-2 PNG/PDF figures from core2_plot_data.csv only."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.plot_cli import render_plot_data

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    render_plot_data(
        args.run_root / "core2_plot_data.csv",
        args.config or args.run_root / "run_config.yaml",
        args.output_dir or args.run_root / "plots",
    )
