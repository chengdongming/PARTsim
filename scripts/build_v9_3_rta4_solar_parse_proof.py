#!/usr/bin/env python3
"""Build/check a version-bound C++ std::stod proof for RTA4 solar rows."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.config import canonical_json  # noqa: E402
from experiments.v9_3.solar_parse_proof import (  # noqa: E402
    SolarParseProofError,
    build_solar_stod_parse_proof,
    write_solar_stod_parse_proof,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--base-system", required=True)
    parser.add_argument("--energy-support", required=True)
    parser.add_argument("--solar-csv", required=True)
    parser.add_argument("--day-of-year", required=True, type=int)
    parser.add_argument("--time-of-day-ms", required=True, type=int)
    parser.add_argument("--horizon", required=True, type=int)
    parser.add_argument("--compiler", default="c++")
    parser.add_argument("--output", required=True)
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = Path(args.output).resolve()
    try:
        proof = build_solar_stod_parse_proof(
            source_root=args.source_root,
            base_system_path=args.base_system,
            energy_support=args.energy_support,
            solar_csv_path=args.solar_csv,
            day_of_year=args.day_of_year,
            time_of_day_ms=args.time_of_day_ms,
            horizon=args.horizon,
            compiler=args.compiler,
            expected_proof_path=output if args.check else None,
        )
        if not args.check:
            write_solar_stod_parse_proof(output, proof)
    except (OSError, SolarParseProofError) as exc:
        print(f"solar parse proof error: {exc}", file=sys.stderr)
        return 1
    print(canonical_json({
        "checked": bool(args.check),
        "output": str(output),
        "proof_id": proof["proof_id"],
        "semantic_service_source_identity": (
            proof["semantic_service_source_identity"]
        ),
        "parser_environment_identity": proof["parser_environment_identity"],
        "verifier_binary_sha256": (
            proof["parser_environment"]["verifier_binary"]["sha256"]
        ),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
