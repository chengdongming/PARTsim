#!/usr/bin/env python3
"""Describe or execute one independently authorized RTA4 formal plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_formal_config import load_rta4_formal_config
from experiments.v9_3.rta4_formal_config_v2 import (
    RTA4FormalConfigV2Error, load_rta4_formal_config_v2,
)
from experiments.v9_3.rta4_formal_plan_v2 import describe_formal_plan_v2
from experiments.v9_3.rta4_formal_execution import (
    AuthorizedRTA4Runner, ProductionSimulationExecutor,
)
from experiments.v9_3.rta4_formal_lifecycle_v2 import (
    RTA4_PREPARED_CONFIG_SCHEMA_V2,
)
from experiments.v9_3.rta4_formal_runner_v2 import AuthorizedRTA4RunnerV2
from experiments.v9_3.rta4_formal_environment import load_strict_json
from experiments.v9_3.result_writer import atomic_write_json


def _json(path: Path):
    return load_strict_json(path)


def _load_describable_config(path: Path):
    try:
        return "V2", load_rta4_formal_config_v2(path)
    except RTA4FormalConfigV2Error:
        return "V1", load_rta4_formal_config(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--campaign-config", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prepared-config", type=Path)
    parser.add_argument("--authorization", type=Path)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--execute", action="store_true")
    operation.add_argument("--resume", action="store_true")
    operation.add_argument("--validate-only", action="store_true")
    parser.add_argument("--max-records", type=int)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--taskset-store", type=Path)
    parser.add_argument("--source-taskset-store", type=Path)
    parser.add_argument("--worker-count", type=int)
    parser.add_argument("--max-in-flight", type=int)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--log-path", type=Path)
    parser.add_argument("--production-manifest", type=Path)
    parser.add_argument("--source-binding", type=Path)
    parser.add_argument("--write-prepared-config", type=Path)
    parser.add_argument("--write-authorization", type=Path)
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument(
        "--thread-workers", action="store_true",
        help="use threads for an explicitly authorized TEST run",
    )
    parser.add_argument("--base-system", type=Path)
    parser.add_argument("--energy-config", type=Path)
    parser.add_argument(
        "--synthetic-ordinal", type=int, action="append",
        help="TEST-authorization-only bounded trusted ordinal",
    )
    args = parser.parse_args()
    if args.campaign_config is not None:
        if args.config is not None:
            parser.error("--campaign-config and --config are mutually exclusive")
        if args.source or args.base_system is not None or args.energy_config is not None:
            parser.error("V3 campaign mode rejects legacy source/config overrides")
        if args.synthetic_ordinal or args.thread_workers:
            parser.error("V3 campaign mode rejects test execution overrides")
        from experiments.v9_3.rta4_formal_config_v3 import (
            load_rta4_campaign_v3,
        )
        from experiments.v9_3.rta4_formal_lifecycle_v3 import (
            build_authorization_v3, build_prepared_config_v3,
            validate_authorization_v3, validate_prepared_config_v3,
        )
        from experiments.v9_3.rta4_formal_plan_v3 import (
            describe_formal_plan_v3,
        )

        try:
            campaign = load_rta4_campaign_v3(args.campaign_config)
            description = describe_formal_plan_v3(
                campaign.normalized_scientific_config,
            )
            report = {
                "campaign_path": str(campaign.campaign_path),
                "raw_campaign_file_sha256": campaign.raw_campaign_file_sha256,
                "normalized_scientific_config_sha256": (
                    campaign.normalized_scientific_config_sha256
                ),
                **description,
            }
            if (
                (args.execute or args.resume or args.validate_only)
                and args.prepared_config is None
            ):
                raise ValueError(
                    "V3 authorized operation requires --prepared-config and --authorization"
                )
            if args.prepared_config is not None or args.authorization is not None:
                if args.prepared_config is None or args.authorization is None:
                    raise ValueError(
                        "V3 authorized operations require both prepared artifacts"
                    )
                prepared = validate_prepared_config_v3(_json(args.prepared_config))
                authorization = validate_authorization_v3(
                    _json(args.authorization), prepared_config=prepared,
                )
                if (
                    prepared["campaign_file"]["absolute_path"]
                    != str(campaign.campaign_path)
                    or prepared["campaign_file"]["raw_campaign_file_sha256"]
                    != campaign.raw_campaign_file_sha256
                    or prepared["normalized_scientific_config_sha256"]
                    != campaign.normalized_scientific_config_sha256
                ):
                    raise ValueError("V3 CLI campaign/prepared identity drift")
                report["prepared_config_id"] = prepared["prepared_config_id"]
                report["authorization_id"] = authorization["authorization_id"]
                if args.execute or args.resume or args.validate_only:
                    if any(value is not None for value in (
                        args.output_root, args.taskset_store,
                        args.source_taskset_store, args.worker_count,
                        args.max_in_flight, args.timeout, args.log_path,
                        args.production_manifest, args.source_binding,
                    )):
                        raise ValueError(
                            "V3 authorized operations reject prepared/runtime overrides"
                        )
                    from experiments.v9_3.rta4_formal_runner_v3 import (
                        AuthorizedRTA4RunnerV3,
                    )

                    summary = AuthorizedRTA4RunnerV3(
                        prepared, authorization,
                    ).run(
                        resume=args.resume,
                        validate_only=args.validate_only,
                        max_records=args.max_records,
                    )
                    report.update({
                        "core": summary.core,
                        "execution_class": summary.execution_class,
                        "production_build_manifest_identity": (
                            summary.production_build_manifest_identity
                        ),
                        "processed_records": summary.processed_records,
                        "pending_records": summary.pending_records,
                        "complete": summary.complete,
                        "checkpoint_path": str(summary.checkpoint_path),
                    })
            if args.write_prepared_config is not None or args.write_authorization is not None:
                if args.execute or args.resume or args.validate_only:
                    raise ValueError(
                        "V3 preparation and authorized operation are separate commands"
                    )
                if (
                    args.write_prepared_config is None
                    or args.write_authorization is None
                    or args.production_manifest is None
                ):
                    raise ValueError(
                        "V3 preparation requires --production-manifest, "
                        "--write-prepared-config and --write-authorization"
                    )
                prepared = build_prepared_config_v3(
                    campaign,
                    production_manifest_path=args.production_manifest,
                    output_root=args.output_root,
                    taskset_store=args.taskset_store,
                    worker_count=args.worker_count,
                    max_in_flight=args.max_in_flight,
                    timeout_seconds=args.timeout,
                    max_records=args.max_records,
                    log_path=args.log_path,
                    resume=False,
                    source_taskset_store=args.source_taskset_store,
                    observed_source_binding=(
                        None if args.source_binding is None
                        else _json(args.source_binding)
                    ),
                )
                authorization = build_authorization_v3(prepared)
                atomic_write_json(args.write_prepared_config, prepared)
                atomic_write_json(args.write_authorization, authorization)
                report["prepared_config_id"] = prepared["prepared_config_id"]
                report["authorization_id"] = authorization["authorization_id"]
            print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.dry_run:
        if args.config is None:
            parser.error("--dry-run requires --config")
        version, config = _load_describable_config(args.config)
        description = (
            describe_formal_plan_v2(config)
            if version == "V2" else _describe_v1(config)
        )
        print(json.dumps(description, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.execute or args.resume or args.validate_only:
        if args.prepared_config is None or args.authorization is None:
            parser.error(
                "authorized operations require --prepared-config and --authorization"
            )
        try:
            prepared = _json(args.prepared_config)
            authorization = _json(args.authorization)
            if prepared.get("prepared_schema") == RTA4_PREPARED_CONFIG_SCHEMA_V2:
                if args.source or args.base_system is not None or args.energy_config is not None:
                    raise ValueError(
                        "V2 workers refuse caller source/compiler/binary/config overrides"
                    )
                if args.synthetic_ordinal:
                    raise ValueError(
                        "V2 bounded ordinals are frozen in the prepared artifact"
                    )
                summary = AuthorizedRTA4RunnerV2(
                    prepared, authorization,
                ).run(
                    resume=args.resume, validate_only=args.validate_only,
                    max_records=args.max_records,
                )
                print(json.dumps({
                    "authorization_id": summary.authorization_id,
                    "core": summary.core,
                    "execution_class": summary.execution_class,
                    "production_build_manifest_identity": (
                        summary.production_build_manifest_identity
                    ),
                    "processed_records": summary.processed_records,
                    "pending_records": summary.pending_records,
                    "complete": summary.complete,
                    "checkpoint_path": str(summary.checkpoint_path),
                }, ensure_ascii=False, sort_keys=True, indent=2))
                return 0
            if prepared.get("profile") == (
                "ASAP_BLOCK_V9_3_RTA4_FORMAL_V2_SHARED_ENERGY"
            ):
                raise ValueError("unknown or obsolete V2 prepared profile")
            sources = {}
            for value in args.source:
                core, separator, path = value.partition("=")
                if not separator or core in sources:
                    raise ValueError("--source must use unique CORE=path")
                sources[core] = Path(path)
            simulator = None
            if prepared["core"] == "CORE-3" and not args.validate_only:
                if args.base_system is None or args.energy_config is None:
                    raise ValueError(
                        "CORE-3 execution requires --base-system and --energy-config"
                    )
                simulator = ProductionSimulationExecutor(
                    prepared, base_system_path=args.base_system,
                    energy_config_path=args.energy_config,
                    energy_config=_json(args.energy_config),
                    source_manifest=authorization["source_manifest"],
                )
            summary = AuthorizedRTA4Runner(
                prepared, authorization, source_closures=sources,
                live_argv=sys.argv, live_cwd=Path.cwd(),
            ).run(
                resume=args.resume, validate_only=args.validate_only,
                max_records=args.max_records,
                synthetic_ordinals=args.synthetic_ordinal,
                simulator_executor=simulator,
                use_processes=(False if args.thread_workers else None),
            )
            print(json.dumps({
                "authorization_id": summary.authorization_id,
                "core": summary.core,
                "execution_class": summary.execution_class,
                "processed_records": summary.processed_records,
                "pending_records": summary.pending_records,
                "complete": summary.complete,
                "checkpoint_path": str(summary.checkpoint_path),
                "closure_sha256": (
                    None if summary.closure is None
                    else summary.closure.closure_sha256
                ),
            }, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.config is None:
        parser.error("select --dry-run or an authorized operation")
    version, config = _load_describable_config(args.config)
    if version == "V2":
        print(
            "RTA4 formal V2 is UNAUTHORIZED_PRE_PILOT; no authorization was generated",
            file=sys.stderr,
        )
        return 2
    from experiments.v9_3.rta4_formal_pipeline import (
        RTA4FormalAuthorizationError, RTA4FormalRunner,
    )

    runner = RTA4FormalRunner(config)
    try:
        runner.run()
    except RTA4FormalAuthorizationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _describe_v1(config):
    from experiments.v9_3.rta4_formal_pipeline import RTA4FormalRunner

    return RTA4FormalRunner(config).describe()


if __name__ == "__main__":
    raise SystemExit(main())
