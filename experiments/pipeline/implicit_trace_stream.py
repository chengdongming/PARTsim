"""Bounded-memory reader and publication checks for v6 RM implicit traces.

This module is deliberately separate from the legacy full-document parser.
It replaces only the source of the schema-2 ``events`` iterable when the
runtime-only implicit resume opt-in is enabled.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterator, Mapping, TextIO


CHUNK_SIZE = 1024 * 1024
MAX_SINGLE_EVENT_SIZE = 64 * 1024 * 1024
_DECODER = json.JSONDecoder()
_STRING_SPECIAL_RE = re.compile(r'["\\]')
_STRUCTURAL_SPECIAL_RE = re.compile(r'["\\[\]{}]')
_VALUE_DELIMITER_RE = re.compile(r'[,\]} \t\r\n]')


def _duplicate_check(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


class _ChunkReader:
    def __init__(self, handle: TextIO, *, chunk_size: int = CHUNK_SIZE) -> None:
        if chunk_size < 1:
            raise ValueError("chunk size must be positive")
        self.handle = handle
        self.chunk_size = chunk_size
        self.buffer = ""
        self.index = 0
        self.eof = False
        self.position = 0
        self.value_limit: int | None = None

    def _fill(self) -> None:
        if self.index < len(self.buffer) or self.eof:
            return
        self.buffer = self.handle.read(self.chunk_size)
        self.index = 0
        if not self.buffer:
            self.eof = True

    def peek(self) -> str:
        self._fill()
        return self.buffer[self.index:self.index + 1]

    def take(self) -> str:
        if self.value_limit is not None and self.position >= self.value_limit:
            raise ValueError("JSON value exceeds bounded size")
        value = self.peek()
        if not value:
            raise ValueError("truncated JSON")
        self.index += 1
        self.position += 1
        return value

    def take_until(self, specials: str, *, collect: bool = True) -> str:
        """Consume ordinary text in C-backed slices up to the next delimiter."""
        if specials == '"\\':
            special_re = _STRING_SPECIAL_RE
        elif specials == '"[]{}':
            special_re = _STRUCTURAL_SPECIAL_RE
        else:
            special_re = _VALUE_DELIMITER_RE
        pieces: list[str] = []
        while True:
            self._fill()
            if self.eof:
                return "".join(pieces) if collect else ""
            match = special_re.search(self.buffer, self.index)
            stop = len(self.buffer) if match is None else match.start()
            count = stop - self.index
            if self.value_limit is not None:
                remaining = self.value_limit - self.position
                if count > remaining:
                    self.index += remaining
                    self.position += remaining
                    raise ValueError("JSON value exceeds bounded size")
            if count:
                if collect:
                    pieces.append(self.buffer[self.index:stop])
                self.index = stop
                self.position += count
            if self.index < len(self.buffer):
                return "".join(pieces) if collect else ""

    def skip_whitespace(self) -> None:
        while True:
            self._fill()
            start = self.index
            while self.index < len(self.buffer) and self.buffer[self.index] in " \t\r\n":
                self.index += 1
            self.position += self.index - start
            if self.index < len(self.buffer) or self.eof:
                return
            if self.index == start:
                return


def _decode_complete(raw: str, name: str) -> Any:
    try:
        value, end = _DECODER.raw_decode(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed {name}") from exc
    if raw[end:].strip():
        raise ValueError(f"trailing data in {name}")
    return value


def _consume_json_string(reader: _ChunkReader) -> str:
    raw = [reader.take()]
    while True:
        text = reader.take_until('"\\')
        if text:
            raw.append(text)
        char = reader.take()
        raw.append(char)
        if char == '"':
            return "".join(raw)
        if char == "\\":
            raw.append(reader.take())


def _consume_json_value(reader: _ChunkReader, *, validate: bool = True) -> str:
    reader.skip_whitespace()
    first = reader.peek()
    if not first:
        raise ValueError("truncated JSON value")
    if first == '"':
        raw = _consume_json_string(reader)
        if validate:
            _decode_complete(raw, "string")
        return raw
    if first not in "[{":
        text = reader.take_until(",]} \t\r\n")
        if not text:
            raise ValueError("invalid JSON value")
        if validate:
            _decode_complete(text, "value")
        return text

    raw = []
    stack: list[str] = []
    in_string = False
    while True:
        text = reader.take_until('"[]{}' if not in_string else '"\\')
        if text:
            raw.append(text)
        char = reader.take()
        raw.append(char)
        if in_string:
            if char == "\\":
                raw.append(reader.take())
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            if not stack or (char == "]" and stack[-1] != "[") or (
                char == "}" and stack[-1] != "{"
            ):
                raise ValueError("invalid JSON nesting")
            stack.pop()
            if not stack:
                text = "".join(raw)
                if validate:
                    _decode_complete(text, "value")
                return text
        if not stack and not in_string:
            raise ValueError("truncated JSON value")


def _skip_json_string(reader: _ChunkReader) -> None:
    if reader.take() != '"':
        raise ValueError("JSON value is not a string")
    while True:
        reader.take_until('"\\', collect=False)
        char = reader.take()
        if char == '"':
            return
        if char == "\\":
            reader.take()


def _skip_json_value(reader: _ChunkReader) -> None:
    """Skip one JSON value without materializing it or decoding its contents."""
    reader.skip_whitespace()
    first = reader.peek()
    if not first:
        raise ValueError("truncated JSON value")
    if first == '"':
        _skip_json_string(reader)
        return
    if first not in "[{":
        consumed = False
        while True:
            char = reader.peek()
            if not char or char in ",]} \t\r\n":
                break
            reader.take()
            consumed = True
        if not consumed:
            raise ValueError("invalid JSON value")
        return

    stack: list[str] = []
    in_string = False
    while True:
        reader.take_until(
            '"[]{}' if not in_string else '"\\', collect=False
        )
        char = reader.take()
        if in_string:
            if char == "\\":
                reader.take()
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            if not stack or (char == "]" and stack[-1] != "[") or (
                char == "}" and stack[-1] != "{"
            ):
                raise ValueError("invalid JSON nesting")
            stack.pop()
            if not stack:
                return


def _skip_events_array(reader: _ChunkReader) -> None:
    """Structurally skip events during the metadata-only first pass."""
    reader.skip_whitespace()
    if reader.take() != "[":
        raise ValueError("events is not an array")
    reader.skip_whitespace()
    if reader.peek() == "]":
        raise ValueError("events array is empty")
    while True:
        reader.skip_whitespace()
        if reader.peek() != "{":
            raise ValueError("event is not an object")
        reader.value_limit = reader.position + MAX_SINGLE_EVENT_SIZE
        try:
            _skip_json_value(reader)
        finally:
            reader.value_limit = None
        reader.skip_whitespace()
        separator = reader.peek()
        if separator == ",":
            reader.take()
            reader.skip_whitespace()
            if reader.peek() == "]":
                raise ValueError("trailing comma in events")
            continue
        if separator == "]":
            reader.take()
            return
        raise ValueError("missing comma in events")


def _decode_event(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_duplicate_check)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed event") from exc
    if not isinstance(value, dict):
        raise ValueError("event is not an object")
    return value


def _scan_events(reader: _ChunkReader) -> Iterator[dict[str, Any]]:
    reader.skip_whitespace()
    if reader.take() != "[":
        raise ValueError("events is not an array")
    reader.skip_whitespace()
    if reader.peek() == "]":
        raise ValueError("events array is empty")
    while True:
        reader.value_limit = reader.position + MAX_SINGLE_EVENT_SIZE
        try:
            raw = _consume_json_value(reader, validate=False)
        finally:
            reader.value_limit = None
        event = _decode_event(raw)
        yield event
        reader.skip_whitespace()
        separator = reader.peek()
        if separator == ",":
            reader.take()
            reader.skip_whitespace()
            if reader.peek() == "]":
                raise ValueError("trailing comma in events")
            continue
        if separator == "]":
            reader.take()
            return
        raise ValueError("missing comma in events")


def _scan_document(
    reader: _ChunkReader, *, decode_events: bool,
) -> Iterator[dict[str, Any]]:
    reader.skip_whitespace()
    if reader.take() != "{":
        raise ValueError("top level is not an object")
    fragments = ["{"]
    seen: set[str] = set()
    events_seen = False
    first_member = True
    while True:
        reader.skip_whitespace()
        if reader.peek() == "}":
            if first_member:
                raise ValueError("missing events field")
            reader.take()
            fragments.append("}")
            break
        if not first_member:
            if reader.take() != ",":
                raise ValueError("missing comma in top-level object")
            fragments.append(",")
            reader.skip_whitespace()
        key_raw = _consume_json_string(reader) if reader.peek() == '"' else ""
        if not key_raw:
            raise ValueError("object key is not a string")
        key = _decode_complete(key_raw, "object key")
        if not isinstance(key, str):
            raise ValueError("object key is not a string")
        if key in seen:
            raise ValueError(f"duplicate JSON key: {key}")
        seen.add(key)
        reader.skip_whitespace()
        if reader.take() != ":":
            raise ValueError("invalid object separator")
        if key == "events":
            if events_seen:
                raise ValueError("duplicate events field")
            events_seen = True
            fragments.append(key_raw + ":[]")
            if decode_events:
                yield from _scan_events(reader)
            else:
                _skip_events_array(reader)
        else:
            raw = _consume_json_value(reader)
            try:
                json.loads(raw, object_pairs_hook=_duplicate_check)
            except json.JSONDecodeError as exc:
                raise ValueError(f"malformed metadata field {key}") from exc
            fragments.append(key_raw + ":" + raw)
        first_member = False
    reader.skip_whitespace()
    if reader.peek():
        raise ValueError("trailing garbage after JSON document")
    if not events_seen:
        raise ValueError("missing events field")
    try:
        metadata = json.loads("".join(fragments), object_pairs_hook=_duplicate_check)
    except json.JSONDecodeError as exc:
        raise ValueError("malformed metadata document") from exc
    if not isinstance(metadata, dict) or metadata.get("events") != []:
        raise ValueError("invalid metadata document")
    return metadata


def _finish_scan(scanner: Iterator[dict[str, Any]]) -> Mapping[str, Any]:
    while True:
        try:
            next(scanner)
        except StopIteration as completed:
            return completed.value


def open_strict_stream(path: Path) -> tuple[Mapping[str, Any], Iterator[dict[str, Any]]]:
    """Validate a trace and return metadata plus a one-event-at-a-time iterator."""
    with path.open("r", encoding="utf-8") as handle:
        metadata = _finish_scan(
            _scan_document(_ChunkReader(handle), decode_events=False)
        )

    def events() -> Iterator[dict[str, Any]]:
        with path.open("r", encoding="utf-8") as handle:
            scanner = _scan_document(_ChunkReader(handle), decode_events=True)
            yield from scanner

    return metadata, events()


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} is boolean")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is not numeric") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} is not finite/nonnegative")
    return result


def validate_publication_trace(
    trace_path: Path,
    *,
    run_id: str,
    expected_scheduler: str,
    expected_display_name: str,
    expected_implementation: str,
    expected_horizon: str,
    taskset_hash: str,
    initial_energy: str,
    capacity: str,
    processors: str,
    expected_schema: str,
    observability_contract_version: str,
) -> None:
    """Apply the schema-2 publication checks without loading the events list."""
    if int(expected_schema) != 2:
        raise ValueError("implicit streaming publication validation requires schema 2")
    metadata, events = open_strict_stream(trace_path)
    if metadata.get("trace_schema_version") != 2:
        raise ValueError("invalid trace_schema_version")
    if metadata.get("run_id") != run_id:
        raise ValueError("run_id mismatch")
    semantic_hash = metadata.get("taskset_semantic_hash")
    if not isinstance(semantic_hash, str) or re.fullmatch(r"[0-9a-f]{64}", semantic_hash) is None:
        raise ValueError("taskset semantic hash mismatch")
    if semantic_hash != taskset_hash:
        raise ValueError("taskset semantic hash mismatch")
    if metadata.get("run_count") != 1:
        raise ValueError("run_count must equal one")
    generation = metadata.get("target_run_generation")
    if type(generation) is not int or generation <= 0:
        raise ValueError("invalid target_run_generation")
    if metadata.get("run_generation") != generation:
        raise ValueError("top-level generation mismatch")
    if metadata.get("configured_scheduler") != expected_scheduler:
        raise ValueError("configured scheduler mismatch")
    for name, expected in (
        ("configured_scheduler", expected_scheduler),
        ("scheduler_display_name", expected_display_name),
        ("scheduler_implementation", expected_implementation),
    ):
        value = metadata.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"invalid scheduler identity: {name}")
        if value != expected:
            raise ValueError(f"scheduler identity mismatch: {name}")
    if metadata.get("simulation_completed") is not True:
        raise ValueError("simulation did not report complete horizon")
    if metadata.get("simulation_completion_reason") != "reached_horizon":
        raise ValueError("simulation completion reason is not reached_horizon")
    expected = _finite(metadata.get("expected_simulation_horizon_ms"), "expected horizon")
    caller_expected = _finite(expected_horizon, "caller expected horizon")
    observed = _finite(metadata.get("observed_simulation_end_ms"), "observed horizon")
    horizon = caller_expected
    if not math.isclose(expected, caller_expected, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("expected horizon mismatch")

    last_time = 0.0
    has_arrival = False
    deadline_misses: set[str] = set()
    for event in events:
        if type(event.get("run_generation")) is not int or event["run_generation"] != generation:
            raise ValueError("event generation mismatch")
        event_time = _finite(event.get("time"), "event time")
        if event_time > horizon:
            raise ValueError("event occurs after horizon")
        last_time = max(last_time, event_time)
        event_type = event.get("event_type")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("invalid event type")
        if event_type == "arrival":
            has_arrival = True
        if event_type == "dline_miss":
            job_id = event.get("job_id")
            task_name = event.get("task_name")
            release = _finite(event.get("arrival_time"), "miss arrival")
            deadline = _finite(event.get("deadline"), "miss deadline")
            remaining = _finite(
                event.get("remaining_execution_ms"), "miss remaining"
            )
            if (
                not isinstance(job_id, str) or not job_id
                or job_id in deadline_misses
                or not isinstance(task_name, str) or not task_name
                or release > deadline or event_time < deadline or remaining <= 0
            ):
                raise ValueError("malformed deadline miss")
            deadline_misses.add(job_id)
    if not has_arrival:
        raise ValueError("trace has no arrival event")
    if not math.isclose(expected, observed, rel_tol=0.0, abs_tol=1e-9) or observed + 1e-9 < last_time:
        raise ValueError("last event occurs after horizon")
