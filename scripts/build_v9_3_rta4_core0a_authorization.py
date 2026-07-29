#!/usr/bin/env python3
"""Generate or check a non-executable RTA4 CORE-0A authorization candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_core0a_authorization_v2 import (
    build_core0a_authorization_candidate_v2,
    validate_core0a_authorization_candidate_v2,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable-bundle", type=Path, required=True)
    parser.add_argument("--selection-artifact", type=Path, required=True)
    parser.add_argument("--candidate-config", type=Path, required=True)
    parser.add_argument("--production-manifest", type=Path, required=True)
    parser.add_argument("--deployment-manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--deployment-workspace-root", type=Path, required=True)
    parser.add_argument("--authorization-output", type=Path, required=True)
    parser.add_argument("--run-nonce")
    parser.add_argument("--issued-at")
    parser.add_argument("--expires-at")
    parser.add_argument(
        "--check", action="store_true",
        help="revalidate an existing candidate without writing",
    )
    args = parser.parse_args()
    common = {
        "portable_bundle_path": args.portable_bundle,
        "selection_artifact_path": args.selection_artifact,
        "candidate_config_path": args.candidate_config,
        "production_manifest_path": args.production_manifest,
        "deployment_manifest_path": args.deployment_manifest,
        "source_root": args.source_root,
        "deployment_workspace_root": args.deployment_workspace_root,
    }
    try:
        if args.check:
            candidate = validate_core0a_authorization_candidate_v2(
                authorization_candidate_path=args.authorization_output,
                **common,
            )
            mode = "check"
        else:
            if None in (args.run_nonce, args.issued_at, args.expires_at):
                parser.error(
                    "generation requires --run-nonce, --issued-at, "
                    "and --expires-at"
                )
            candidate = build_core0a_authorization_candidate_v2(
                authorization_output_path=args.authorization_output,
                run_nonce=args.run_nonce,
                issued_at=args.issued_at,
                expires_at=args.expires_at,
                **common,
            )
            mode = "generate"
        print(json.dumps({
            "authorization_candidate_identity": candidate[
                "authorization_candidate_identity"
            ],
            "authorization_output": str(args.authorization_output.resolve()),
            "authorization_review_passed": False,
            "executable_authorization": False,
            "mode": mode,
            "pilot_execution_allowed": False,
            "status": candidate["status"],
        }, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
