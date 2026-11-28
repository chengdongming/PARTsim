#!/usr/bin/env python3
"""Run AB/AN unlimited-capacity RTA on explicit same-slot cases."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.same_slot import ab_rta, campaign

def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True,
                        help="JSON {cases:[...]} with exact scaled model inputs")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bound", choices=("analytic", "flow"),
                        help="legacy AB-only selection (default: analytic)")
    parser.add_argument("--method", choices=campaign.FORMAL_METHODS, action="append",
                        help="formal AB/AN method; repeat for paired evaluation")
    parser.add_argument("--timeout", type=float, default=30,
                        help="wall-clock budget per case/method with --method")
    parser.add_argument("--an-gap", choices=("auto", "base"), default="auto",
                        help="freeze AN auxiliary bound to base for controlled comparison")
    parser.add_argument("--translated-seq", action="store_true")
    parser.add_argument("--reinterpret-generated-floor", action="store_true",
        help="explicitly reuse hypothetical old generated inputs, treating the "
             "same numeric floor as PRE_HARVEST; never use for measured floors")
    parser.add_argument("--max-cases", type=int)
    args = parser.parse_args()
    if args.method:
        if args.bound or args.translated_seq:
            parser.error("--method cannot be combined with legacy AB flags")
        if len(set(args.method)) != len(args.method):
            parser.error("duplicate --method")
        if args.an_gap != "auto" and not any(m in campaign.AN_METHODS for m in args.method):
            parser.error("--an-gap requires an AN method")
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            parser.error("timeout must be finite and positive")
    elif args.an_gap != "auto":
        parser.error("--an-gap requires --method")
    args.bound = args.bound or "analytic"
    data = json.loads(args.input.read_text())
    cases = data["cases"]
    if args.max_cases is not None:
        if args.max_cases <= 0:
            parser.error("--max-cases must be positive")
        cases = cases[:args.max_cases]
    if not cases:
        parser.error("empty case set")
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    for index, source in enumerate(cases):
        case = copy.deepcopy(source)
        model = case["model"]
        if model["harvest_order"] == "DEBIT_BEFORE_HARVEST":
            if not args.reinterpret_generated_floor:
                parser.error("old inputs require explicit hypothetical-floor reinterpretation")
            # The sealed old generator validates identity, integer scaling
            # and the numeric model before the deliberate reinterpretation.
            from importlib.util import spec_from_file_location, module_from_spec
            spec = spec_from_file_location("frozen_case_data", ROOT /
                "experiments/asapblock_unlimited/runtime/reproduction/optimized/experiments/ab_v7_2/data.py")
            frozen_data = module_from_spec(spec)
            spec.loader.exec_module(frozen_data)
            frozen_data.validate_case(source)
            model["harvest_order"] = "HARVEST_BEFORE_DEBIT"
            model["release_floor_stage"] = "PRE_HARVEST"
        case_id = digest(model)
        if args.method:
            # Use the same bounded, status-preserving worker as the campaign.
            # Explicit cases can lack generator metadata; use deterministic
            # model identity rather than fabricating a frozen source ID.
            case["source_case_id"] = source.get("case_id") or case_id
            case["input_sha256"] = campaign.digest(model)
            case["case_id"] = campaign.digest(dict(index=index,
                source_case_id=case["source_case_id"], model=model))
            for method in args.method:
                row = campaign.execute(case, method, args.timeout, args.an_gap)
                row.update(name=case.get("name"), model=model)
                row["row_sha256"] = campaign.digest({k:v for k,v in row.items()
                                                     if k != "row_sha256"})
                rows.append(row)
                with (args.output/"results.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                       allow_nan=False)+"\n")
                print(case.get("name"), method, row["status"], flush=True)
            continue
        result = ab_rta.analyze_case(case, bound=args.bound,
                                    translated_seq=args.translated_seq)
        row = dict(name=case.get("name"), source_case_id=source.get("case_id"),
                   case_id=case_id, model=model, result=result)
        rows.append(row)
        with (args.output/"results.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True)+"\n")
        print(case.get("name"), result["status"], flush=True)
    if args.method:
        summary = dict(model="HARVEST_BEFORE_DEBIT_UNLIMITED",
                       source_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
                       methods=args.method, cases=len(cases), requests=len(rows),
                       source_code_sha256=campaign.source_identity(),
                       timeout_wall_seconds=args.timeout, an_gap=args.an_gap,
                       window_search="FULL_INTEGER_WINDOWS",
                       counts={m:{status:sum(r["method"] == m and r["status"] == status
                                           for r in rows) for status in sorted(campaign.TERMINAL)}
                               for m in args.method})
        (args.output/"summary.json").write_text(json.dumps(summary, indent=2)+"\n")
        return 1 if any(r["status"] == "ERROR" for r in rows) else 0
    summary = dict(model=ab_rta.MODEL, source_sha256=hashlib.sha256(
                   args.input.read_bytes()).hexdigest(), bound=args.bound,
                   translated_seq=args.translated_seq, cases=len(rows),
                   certified=sum(row["result"]["status"] == "CERTIFIED" for row in rows))
    (args.output/"summary.json").write_text(json.dumps(summary, indent=2)+"\n")

if __name__ == "__main__":
    raise SystemExit(main())
