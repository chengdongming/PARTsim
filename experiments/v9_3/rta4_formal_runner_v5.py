#!/usr/bin/env python3
"""Preflight or explicitly execute a local, non-paper RTA V5 campaign."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .rta4_formal_config_v5 import (
    RTA4FormalConfigV5Error,
    load_rta4_campaign_v5,
    source_closure_identity_v5,
)
from .rta4_formal_plan_v5 import (
    RTA4FormalPlanV5Error,
    describe_formal_plan_v5,
)
from .rta4_formal_schema_v5 import formal_schema_hash_v5
from .rta4_local_execution_v5 import (
    RTA4LocalExecutionV5Error,
    execute_local_campaign_v5,
)


def preflight_campaign_v5(path: Path | str) -> dict[str, object]:
    campaign = load_rta4_campaign_v5(path)
    plan = describe_formal_plan_v5(
        campaign.normalized_scientific_config,
        campaign.task_sources,
        campaign.service_curve,
    )
    return {
        "profile": campaign.normalized_scientific_config["profile"],
        "campaign_id": campaign.normalized_scientific_config["campaign_id"],
        "core": campaign.normalized_scientific_config["core"],
        "raw_campaign_file_sha256": campaign.raw_campaign_file_sha256,
        "normalized_scientific_config_sha256": (
            campaign.normalized_scientific_config_sha256
        ),
        "formal_schema_sha256": formal_schema_hash_v5(),
        "source_closure_identity": source_closure_identity_v5(
            campaign.normalized_scientific_config
        ),
        "service_curve_identity": campaign.service_curve.identity,
        **({
            "simulation_tick_ms": campaign.normalized_scientific_config[
                "simulation_tick_ms"
            ],
        } if campaign.normalized_scientific_config["core"] == "CORE-3" else {}),
        "task_source_identities": [
            binding.source.identity for binding in campaign.task_sources
        ],
        "plan_sha256": plan["plan_sha256"],
        "counts": {
            key: plan[key] for key in (
                "taskset_skeleton_count",
                "mathematical_request_count",
                "ordered_stream_count",
            )
        },
        "runtime": dict(campaign.runtime),
        "formal_campaign_authorization_status": plan[
            "formal_campaign_authorization_status"
        ],
        "execution_started": False,
        "formal_campaign_started": False,
        "paper_result_authorized": False,
        "not_for_paper": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate and describe only; never calls a solver or simulator",
    )
    mode.add_argument(
        "--execute-local",
        action="store_true",
        help="execute through V3 worker facilities as non-paper evidence",
    )
    parser.add_argument("--acknowledge-not-for-paper", action="store_true")
    parser.add_argument("--output-root")
    parser.add_argument("--resume", action="store_true", default=None)
    parser.add_argument("--max-records", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.execute_local and not args.acknowledge_not_for_paper:
        print(
            "RTA V5 local execution requires "
            "--acknowledge-not-for-paper"
        )
        return 2
    try:
        summary = (
            preflight_campaign_v5(args.campaign)
            if args.preflight_only
            else execute_local_campaign_v5(
                args.campaign,
                acknowledge_not_for_paper=args.acknowledge_not_for_paper,
                output_root=args.output_root,
                resume=args.resume,
                max_records=args.max_records,
            )
        )
    except (
        RTA4FormalConfigV5Error,
        RTA4FormalPlanV5Error,
        RTA4LocalExecutionV5Error,
        OSError,
    ) as exc:
        print(f"RTA V5 operation failed: {exc}")
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main", "preflight_campaign_v5"]
