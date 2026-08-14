"""Small checkpoint throttler shared by the direct execution path."""

from __future__ import annotations

import time
from typing import Any, Mapping


class CheckpointThrottle:
    """Bound full checkpoint rewrites while terminal rows remain immediate."""

    def __init__(
        self,
        writer: Any,
        completed: Mapping[str, Mapping[str, Any]],
        *,
        every_records: int,
        every_seconds: int,
    ) -> None:
        self.writer = writer
        self.completed = completed
        self.every_records = every_records
        self.every_seconds = every_seconds
        self.records_since_write = 0
        self.last_write = time.monotonic()
        self.write_count = 0
        self.last_checkpoint: Mapping[str, Any] | None = None

    def terminal_committed(self) -> None:
        self.records_since_write += 1

    def write_if_due(self, *, force: bool = False) -> Mapping[str, Any] | None:
        due = (
            force
            or self.records_since_write >= self.every_records
            or time.monotonic() - self.last_write >= self.every_seconds
        )
        if not due:
            return None
        self.last_checkpoint = self.writer.write_checkpoint(self.completed)
        self.write_count += 1
        self.records_since_write = 0
        self.last_write = time.monotonic()
        return self.last_checkpoint


__all__ = ["CheckpointThrottle"]
