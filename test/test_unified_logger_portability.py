from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_log_directory_environment_override_is_cwd_independent(tmp_path: Path) -> None:
    read_only_cwd = tmp_path / "read-only-cwd"
    read_only_cwd.mkdir()
    read_only_cwd.chmod(0o555)
    external_logs = tmp_path / "external-logs"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT)
    environment["PARTSIM_LOG_DIR"] = str(external_logs)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from utils.unified_logger import get_energy_logger; "
            "get_energy_logger().info('portability')",
        ],
        cwd=read_only_cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert external_logs.is_dir()
    assert any(path.is_file() for path in external_logs.iterdir())
