"""Read-only helpers for completed v9.3 run directories."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable

from .result_writer import read_csv


class RunResults:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def table(self, name: str) -> list[Dict[str, str]]:
        return read_csv(self.root / name)

    @property
    def tasksets(self) -> list[Dict[str, str]]:
        return self.table("per_taskset_results.csv")

    @property
    def tasks(self) -> list[Dict[str, str]]:
        return self.table("per_task_results.csv")

    @property
    def attempts(self) -> list[Dict[str, str]]:
        return self.table("analysis_attempts.csv")


def index_by(rows: Iterable[Dict[str, str]], *keys: str) -> Dict[tuple, Dict[str, str]]:
    result: Dict[tuple, Dict[str, str]] = {}
    for row in rows:
        key = tuple(row[item] for item in keys)
        if key in result:
            raise ValueError(f"duplicate result key: {key}")
        result[key] = row
    return result
