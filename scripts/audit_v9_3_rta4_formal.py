#!/usr/bin/env python3
"""Revalidate an RTA4 authorization and optional run closure."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_formal_authorization import verify_live_authorization
from experiments.v9_3.rta4_formal_validation import validate_formal_run_closure


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--allow-test-authorization", action="store_true")
    args = parser.parse_args()
    try:
        authorization = json.loads(
            args.authorization.read_text(encoding="utf-8")
        )
        verified = verify_live_authorization(
            authorization, allow_test=args.allow_test_authorization,
        )
        result = {
            "authorization_id": verified["authorization_id"],
            "authorization_status": verified["authorization_status"],
        }
        if args.run_root is not None:
            sources = {}
            for value in args.source:
                core, separator, path = value.partition("=")
                if not separator or core in sources:
                    raise ValueError("--source must use unique CORE=path")
                sources[core] = Path(path)
            closure = validate_formal_run_closure(
                args.run_root, require_complete=True,
                require_authorized_formal=True, source_closures=sources,
                allow_test_authorization=args.allow_test_authorization,
            )
            result.update({
                "core": closure.metadata["core"],
                "plan_sha256": closure.metadata["plan_sha256"],
                "closure_sha256": closure.closure_sha256,
            })
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
