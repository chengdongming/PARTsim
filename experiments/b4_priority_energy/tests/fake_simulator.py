#!/usr/bin/env python3
"""Test-only simulator implementing the public rtsim argv shape."""

import json
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path


SIMULATOR_VERSION = "b4pe-fake-simulator-r2-file-fd"


def parse_args(argv):
    if len(argv) < 7:
        raise ValueError("expected system taskset duration -t result --run-id id")
    system = Path(argv[0])
    taskset = Path(argv[1])
    int(argv[2])
    trace_index = argv.index("-t")
    run_index = argv.index("--run-id")
    return system, taskset, Path(argv[trace_index + 1]), argv[run_index + 1]


def write_result(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv=None):
    system, taskset, result, run_id = parse_args(sys.argv[1:] if argv is None else argv)
    if not taskset.is_file():
        print("missing taskset", file=sys.stderr)
        return 9
    config = json.loads(system.read_text(encoding="utf-8"))
    mode = config.get("mode", "success")
    attempt_one = ".attempt-1." in result.name
    if config.get("stdout"):
        print(config["stdout"], flush=True)
    if config.get("stderr"):
        print(config["stderr"], file=sys.stderr, flush=True)

    if mode == "nonzero":
        return int(config.get("exit_code", 7))
    if mode == "first_fail_then_success" and attempt_one:
        return int(config.get("exit_code", 7))
    if mode in {"sleep", "first_timeout_then_success"} and (
        mode == "sleep" or attempt_one
    ):
        time.sleep(float(config.get("sleep_seconds", 30)))
        return 0
    if mode in {"result_then_child_hang", "first_child_timeout_then_success"} and (
        mode == "result_then_child_hang" or attempt_one
    ):
        write_result(result, f"partial {run_id}\n")
        child_code = "import time; time.sleep(60)"
        if config.get("child_ignore_sigterm"):
            child_code = (
                "import signal,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
            )
        child = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if config.get("child_pid_path"):
            Path(config["child_pid_path"]).write_text(str(child.pid), encoding="ascii")
        time.sleep(float(config.get("sleep_seconds", 30)))
        return 0
    if mode == "missing_result":
        return 0
    if mode == "empty_result":
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_bytes(b"")
        return 0
    if mode == "snapshot_digest":
        source = Path(os.environ["B4PE_SOURCE_SNAPSHOT"])
        simulator_bytes = Path(sys.argv[0]).read_bytes()
        payload = {
            "simulator_sha256": hashlib.sha256(simulator_bytes).hexdigest(),
            "simulator_version": SIMULATOR_VERSION,
            "system_sha256": hashlib.sha256(system.read_bytes()).hexdigest(),
            "taskset_sha256": hashlib.sha256(taskset.read_bytes()).hexdigest(),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
        write_result(result, json.dumps(payload, sort_keys=True) + "\n")
        return 0
    write_result(result, config.get("result_text", f"result {run_id}\n"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
