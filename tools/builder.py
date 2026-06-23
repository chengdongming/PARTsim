#!/usr/bin/env python3

import runpy
from pathlib import Path


runpy.run_path(
    Path(__file__).resolve().parents[1] / "cmakeopts" / "cmake-builder.py",
    run_name="__main__",
)
