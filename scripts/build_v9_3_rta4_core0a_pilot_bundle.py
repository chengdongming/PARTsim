#!/usr/bin/env python3
"""Generate or check the portable RTA4 CORE-0A engineering-pilot freeze."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_core0a_pilot_v2 import (
    CANDIDATE_CONFIG_PATH,
    CORE0A_AUTODL_CONTROLLED_EXECUTION_ENVIRONMENT_CLASSIFICATION,
    CORE0A_TEST_ONLY_EXECUTION_ENVIRONMENT_CLASSIFICATION,
    PROJECT_ROOT,
    SELECTION_ARTIFACT_PATH,
    build_autodl_deployment_manifest_v2,
    build_autodl_handoff_v2,
    build_core0a_selection_v2,
    build_portable_candidate_bundle_v2,
    load_strict_canonical_json,
    load_core0a_selection_v2,
    validate_autodl_handoff_v2,
    validate_autodl_deployment_manifest_v2,
    validate_portable_candidate_bundle_v2,
    write_canonical_json,
)
from experiments.v9_3.rta4_production_build_manifest import (
    load_and_validate_production_build_manifest,
)


DEFAULT_BUNDLE_OUTPUT = Path(
    "/tmp/partsim_v9_3_rta4_core0a_candidate_bundle.json"
)
DEFAULT_HANDOFF_OUTPUT = Path(
    "/tmp/partsim_v9_3_rta4_core0a_autodl_handoff.json"
)
DEFAULT_DEPLOYMENT_OUTPUT = Path(
    "/tmp/partsim_v9_3_rta4_core0a_autodl_deployment.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--selection-output", type=Path,
        default=PROJECT_ROOT / SELECTION_ARTIFACT_PATH,
    )
    parser.add_argument("--bundle-output", type=Path, default=DEFAULT_BUNDLE_OUTPUT)
    parser.add_argument("--handoff-output", type=Path, default=DEFAULT_HANDOFF_OUTPUT)
    parser.add_argument(
        "--deployment-output", type=Path, default=DEFAULT_DEPLOYMENT_OUTPUT,
    )
    parser.add_argument(
        "--candidate-config", type=Path,
        default=PROJECT_ROOT / CANDIDATE_CONFIG_PATH,
    )
    parser.add_argument("--production-manifest", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--deployment-workspace-root", type=Path)
    parser.add_argument(
        "--execution-environment-classification",
        choices=(
            CORE0A_TEST_ONLY_EXECUTION_ENVIRONMENT_CLASSIFICATION,
            CORE0A_AUTODL_CONTROLLED_EXECUTION_ENVIRONMENT_CLASSIFICATION,
        ),
        default=CORE0A_TEST_ONLY_EXECUTION_ENVIRONMENT_CLASSIFICATION,
        help=(
            "freeze the explicit deployment execution class; controlled "
            "classification is selected only during AutoDL deployment"
        ),
    )
    parser.add_argument(
        "--artifact",
        choices=("selection", "bundle", "handoff", "deployment", "all"),
        default="all",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        details = {"mode": "check" if args.check else "generate"}
        if args.check or args.artifact not in {"selection", "all"}:
            document = load_core0a_selection_v2(args.selection_output)
        else:
            document = build_core0a_selection_v2()
            write_canonical_json(args.selection_output, document)
        if args.artifact in {"selection", "all"}:
            details.update({
                "selection_output": str(args.selection_output.resolve()),
                "selection_count": len(document["ordered_records"]),
                "core0a_selection_identity": document[
                    "core0a_selection_identity"
                ],
            })
        bundle = None
        if args.artifact in {"bundle", "handoff", "deployment", "all"}:
            if args.check:
                bundle = validate_portable_candidate_bundle_v2(
                    load_strict_canonical_json(args.bundle_output),
                )
            else:
                bundle = build_portable_candidate_bundle_v2(
                    selection=document,
                )
                write_canonical_json(args.bundle_output, bundle)
            details.update({
                "bundle_output": str(args.bundle_output.resolve()),
                "portable_freeze_identity": bundle[
                    "portable_freeze_identity"
                ],
            })
        if args.artifact in {"handoff", "all"}:
            assert bundle is not None
            if args.check:
                handoff = validate_autodl_handoff_v2(
                    load_strict_canonical_json(args.handoff_output), bundle,
                )
            else:
                handoff = build_autodl_handoff_v2(bundle)
                write_canonical_json(args.handoff_output, handoff)
            details.update({
                "handoff_output": str(args.handoff_output.resolve()),
                "autodl_handoff_identity": handoff[
                    "autodl_handoff_identity"
                ],
            })
        if args.artifact == "deployment":
            assert bundle is not None
            if (
                args.production_manifest is None
                or args.source_root is None
                or args.deployment_workspace_root is None
            ):
                raise ValueError(
                    "deployment requires --production-manifest, "
                    "--source-root, and --deployment-workspace-root"
                )
            if args.check:
                validated = validate_autodl_deployment_manifest_v2(
                    portable_bundle_path=args.bundle_output,
                    selection_artifact_path=args.selection_output,
                    candidate_config_path=args.candidate_config,
                    production_manifest_path=args.production_manifest,
                    deployment_manifest_path=args.deployment_output,
                    source_root=args.source_root,
                    deployment_workspace_root=(
                        args.deployment_workspace_root
                    ),
                )
                deployment = dict(validated.deployment_manifest)
                if deployment[
                    "execution_environment_classification"
                ] != args.execution_environment_classification:
                    raise ValueError(
                        "deployment execution classification differs from "
                        "the explicit deployment check"
                    )
                execution_identity = validated.execution_identity
            else:
                production = load_and_validate_production_build_manifest(
                    args.production_manifest,
                    require_default_closure=True,
                )
                deployment = build_autodl_deployment_manifest_v2(
                    bundle=bundle,
                    production_manifest=production,
                    source_root=args.source_root,
                    deployment_workspace_root=(
                        args.deployment_workspace_root
                    ),
                    execution_environment_classification=(
                        args.execution_environment_classification
                    ),
                )
                write_canonical_json(args.deployment_output, deployment)
                execution_identity = None
            details.update({
                "deployment_output": str(args.deployment_output.resolve()),
                "deployment_manifest_identity": deployment[
                    "deployment_manifest_identity"
                ],
                "actual_output_root": deployment["actual_output_root"],
                "taskset_store_root": deployment["taskset_store_root"],
                "worker_count": deployment["worker_count"],
                "max_in_flight": deployment["max_in_flight"],
                "execution_environment_classification": deployment[
                    "execution_environment_classification"
                ],
            })
            if execution_identity is not None:
                details["execution_identity"] = execution_identity
        print(json.dumps(
            details, ensure_ascii=False, sort_keys=True, indent=2,
        ))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
