#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from experiments.v9_3.ext4_robustness import analyze_ext4
parser = argparse.ArgumentParser()
parser.add_argument("--output-root", type=Path, required=True)
args = parser.parse_args()
print(json.dumps(analyze_ext4(args.output_root), sort_keys=True))
