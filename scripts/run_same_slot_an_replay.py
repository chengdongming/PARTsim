#!/usr/bin/env python3
"""Replay one exact same-slot AN trajectory, optionally audit native PARTsim."""
import argparse
import json
from pathlib import Path
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.same_slot import an_replay


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, type=Path,
                   help="JSON case, harvest and release data in exact integer energy units")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--native-binary", type=Path,
                   help="optional freshly built PARTsim for a passing periodic WCET trace")
    args = p.parse_args()
    data = json.loads(args.input.read_text())
    case = data["case"]
    H = data["harvest"]
    e0 = data.get("initial_energy", 0)
    cert = data.get("certificate")
    if "releases" in data:
        releases = data["releases"]
        if args.native_binary:
            p.error("native projection requires periodic offsets and release_horizon, not arbitrary releases")
    else:
        releases = an_replay.periodic_releases(case, data["offsets"], data["release_horizon"])
    reference = an_replay.replay(case, H, releases, e0, cert)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/"reference.json").write_text(json.dumps(reference, indent=2)+"\n")
    status = {k: reference[k] for k in ("status", "first_deadline_miss",
                "certificate_violation", "max_observed_first_completion", "max_observed_response",
                "response_bound_to_observed_max", "right_censored_jobs")}
    if args.native_binary:
        if reference["first_deadline_miss"] is None:
            try:
                status["native"] = an_replay.run_native_periodic(
                    case, H, e0, data["offsets"], data["release_horizon"],
                    args.native_binary, args.output/"native", cert)
            except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                status["native"] = {"status": "AUDIT_FAILED", "reason": str(exc)}
        else:
            status["native"] = {"status":"NOT_AUDITED_REFERENCE_DEADLINE_MISS"}
    (args.output/"summary.json").write_text(json.dumps(status, indent=2)+"\n")
    print(json.dumps(status, indent=2))
    return 1 if (reference["certificate_violation"] or
                 status.get("native", {}).get("status") == "AUDIT_FAILED") else 0


if __name__ == "__main__":
    raise SystemExit(main())
