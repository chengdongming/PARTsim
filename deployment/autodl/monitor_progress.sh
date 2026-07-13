#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

python3 - "$PARTSIM_OUTPUT_ROOT" <<'PY'
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
for path in sorted(root.glob("*/checkpoint.json")):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"{path.parent.name}: invalid checkpoint: {exc}")
        continue
    fields = [
        key for key in (
            "requested_count", "completed_count", "terminal_count", "pending",
            "rta_requested", "rta_terminal", "simulation_requested",
            "simulation_terminal", "stop_requested",
        ) if key in value
    ]
    print(path.parent.name + ": " + ", ".join(f"{key}={value[key]}" for key in fields))
PY
pgrep -af 'run_v9_3_(core|ext)|rtsim' || true
