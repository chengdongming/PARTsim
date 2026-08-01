#!/usr/bin/env python3
"""Build the read-only V4 regression manifest from verified T10 evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_task_source_v4 import (
    EXPLICIT_MANIFEST_SCHEMA_V1,
    FROZEN_T10_BACKGROUND_TASKS,
    FROZEN_T10_CORE_GENERATOR_CONTRACT,
    GENERATED_FAMILY,
    PRIORITY_POLICY_RM,
    T10_BALANCED_V1,
    load_explicit_taskset_manifest_v4,
    normalize_generated_family_v4,
)
from experiments.v9_3.rta4_t10_parity_audit import (
    canonical_json,
    normalize_t10_record,
    verify_evidence,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verified = verify_evidence(args.evidence_root)
    holdout_manifest = verified["holdout_manifest"]
    generated = normalize_generated_family_v4({
        "mode": GENERATED_FAMILY,
        "family_id": T10_BALANCED_V1,
        "parameters": {
            "processors": 4,
            "priority_policy": PRIORITY_POLICY_RM,
            "task_count": 10,
            "mechanism_core_task_count": 7,
            "background_utilization": "1/12",
            "background_tasks": FROZEN_T10_BACKGROUND_TASKS,
            "taskset_count": 176,
            "base_seed": 1918273645,
            "generation_indices": holdout_manifest["holdout_original_indices"],
            "core_generator_contract": FROZEN_T10_CORE_GENERATOR_CONTRACT,
        },
    })
    normalized_evidence = [
        normalize_t10_record(row) for row in verified["holdout"]
    ]
    for index, (generated_taskset, evidence_taskset) in enumerate(
        zip(generated.tasksets, normalized_evidence)
    ):
        if (
            [task.material() for task in generated_taskset.tasks]
            != evidence_taskset["tasks"]
            or generated_taskset.source_seed != evidence_taskset["seed"]
        ):
            raise RuntimeError(
                f"frozen generator differs from evidence at taskset {index}"
            )
    manifest = {
        "schema": EXPLICIT_MANIFEST_SCHEMA_V1,
        "processors": 4,
        "priority_policy": PRIORITY_POLICY_RM,
        "task_count": 10,
        "taskset_count": 176,
        "task_order": [f"tau_{index}" for index in range(1, 11)],
        "tasksets": [
            {
                "taskset_id": f"t10-holdout-regression-{index:03d}",
                "source_seed": taskset.source_seed,
                "tasks": [task.material() for task in taskset.tasks],
            }
            for index, taskset in enumerate(generated.tasksets)
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    explicit = load_explicit_taskset_manifest_v4(args.output)
    print(f"holdout_sha256={verified['holdout_sha256']}")
    print(f"generated_task_source_identity={generated.identity}")
    print(f"explicit_task_source_identity={explicit.identity}")
    print(
        "explicit_content_certificate_identity="
        f"{explicit.content_certificate['content_certificate_identity']}"
    )
    print(f"manifest_file_sha256={explicit.manifest_file_sha256}")
    print(f"manifest_semantic_sha256={explicit.manifest_semantic_sha256}")
    print(f"canonical_manifest_sha256={__import__('hashlib').sha256(canonical_json(manifest).encode('utf-8')).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
