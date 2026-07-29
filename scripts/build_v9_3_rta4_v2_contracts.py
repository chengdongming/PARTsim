#!/usr/bin/env python3
"""Build/check RTA4 V2 schema, numeric, config and plan identities."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_formal_config import canonical_json  # noqa: E402
from experiments.v9_3.rta4_formal_config_v2 import (  # noqa: E402
    load_rta4_formal_config_v2,
    rta4_formal_config_hash_v2,
)
from experiments.v9_3.rta4_formal_environment import load_strict_json  # noqa: E402
from experiments.v9_3.rta4_formal_plan_v2 import (  # noqa: E402
    describe_all_formal_plans_v2,
)
from experiments.v9_3.rta4_formal_schema_v2 import (  # noqa: E402
    formal_schema_manifest_v2,
)
from experiments.v9_3.rta4_numeric_contract_v2 import (  # noqa: E402
    numeric_contract_v2_metadata,
)


CORES = ("CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5A", "CORE-5B")


def build_document() -> dict:
    configs = {
        core: load_rta4_formal_config_v2(
            ROOT / "configs" /
            f"v9_3_rta4_{core.lower().replace('-', '')}_unauthorized_pre_pilot_v2_shared_energy.yaml",
            expected_core=core,
        )
        for core in CORES
    }
    return {
        "profile": "ASAP_BLOCK_V9_3_RTA4_FORMAL_V2_SHARED_ENERGY",
        "authorization_status": "UNAUTHORIZED_PRE_PILOT",
        "formal_authorization": False,
        "numeric_contract": numeric_contract_v2_metadata(),
        "formal_schema": formal_schema_manifest_v2(),
        "config_sha256": {
            core: rta4_formal_config_hash_v2(configs[core]) for core in CORES
        },
        "plans": describe_all_formal_plans_v2(configs),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    document = build_document()
    if args.check and args.output is not None:
        if load_strict_json(args.output) != document:
            raise SystemExit("RTA4 V2 contract artifact drift")
    elif args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(canonical_json(document) + "\n", encoding="utf-8")
    print(canonical_json({
        "profile": document["profile"],
        "schema_sha256": document["formal_schema"]["schema_sha256"],
        "numeric_contract_sha256": document["numeric_contract"]["numeric_contract_sha256"],
        "all_plan_digest": document["plans"]["all_plan_digest"],
        "authorization_status": document["authorization_status"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
