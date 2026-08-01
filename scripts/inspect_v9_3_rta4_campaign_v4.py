#!/usr/bin/env python3
"""Parse and inspect a V4 plan without writing execution namespaces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_formal_config_v4 import load_rta4_campaign_v4
from experiments.v9_3.rta4_formal_lifecycle_v4 import dry_run_campaign_v4


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign", type=Path)
    args = parser.parse_args()
    campaign = load_rta4_campaign_v4(args.campaign)
    print(json.dumps(
        dry_run_campaign_v4(campaign),
        ensure_ascii=False, indent=2, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
