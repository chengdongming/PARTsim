#!/usr/bin/env python3
"""Run the new unlimited-capacity RTA on explicit same-slot cases."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.same_slot import ab_rta

def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True,
                        help="JSON {cases:[...]} with exact scaled model inputs")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bound", choices=("analytic", "flow"), default="analytic")
    parser.add_argument("--translated-seq", action="store_true")
    parser.add_argument("--reinterpret-generated-floor", action="store_true",
        help="explicitly reuse hypothetical old generated inputs, treating the "
             "same numeric floor as PRE_HARVEST; never use for measured floors")
    parser.add_argument("--max-cases", type=int)
    args = parser.parse_args()
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
    for source in cases:
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
        result = ab_rta.analyze_case(case, bound=args.bound,
                                    translated_seq=args.translated_seq)
        row = dict(name=case.get("name"), source_case_id=source.get("case_id"),
                   case_id=case_id, model=model, result=result)
        rows.append(row)
        with (args.output/"results.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True)+"\n")
        print(case.get("name"), result["status"], flush=True)
    summary = dict(model=ab_rta.MODEL, source_sha256=hashlib.sha256(
                   args.input.read_bytes()).hexdigest(), bound=args.bound,
                   translated_seq=args.translated_seq, cases=len(rows),
                   certified=sum(row["result"]["status"] == "CERTIFIED" for row in rows))
    (args.output/"summary.json").write_text(json.dumps(summary, indent=2)+"\n")

if __name__ == "__main__":
    main()
