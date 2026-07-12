#!/usr/bin/env python3
"""Schema-driven binding for the frozen ASAP-BLOCK v1.3.12 contract.

This module intentionally loads the authoritative YAML and validation-common
implementation at runtime.  It does not carry a second handwritten table or
enum schema.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional


VERSION = "1.3.12"
CONTRACT_DIRECTORY_NAME = "ASAP_BLOCK_v1_3_12_机器合同静态冻结候选包"
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONTRACT_ROOT = PROJECT_ROOT / "docs" / CONTRACT_DIRECTORY_NAME


class ContractBindingError(ValueError):
    """Raised when a row or frozen contract surface is not canonical."""


def _load_validation_common(contract_root: Path):
    path = contract_root / "ASAP_BLOCK_validation_common_v1_3_12.py"
    spec = importlib.util.spec_from_file_location(
        "_asap_block_validation_common_v1_3_12", str(path)
    )
    if spec is None or spec.loader is None:
        raise ContractBindingError("cannot load v1.3.12 validation common")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        # The frozen contract directory is an immutable input surface; loading
        # its helper must not leave a __pycache__ artifact beside it.
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return "{}/{}".format(value.numerator, value.denominator)


class V1312SchemaBinding:
    """Read, validate, canonicalize, and write v1.3.12 table rows."""

    def __init__(self, contract_root: Optional[Path] = None) -> None:
        self.contract_root = Path(contract_root or DEFAULT_CONTRACT_ROOT).resolve()
        if self.contract_root.name != CONTRACT_DIRECTORY_NAME:
            raise ContractBindingError(
                "the active contract must be {}".format(CONTRACT_DIRECTORY_NAME)
            )
        if not self.contract_root.is_dir():
            raise ContractBindingError("v1.3.12 contract directory is missing")
        self.common = _load_validation_common(self.contract_root)
        self.schema = self.common.load_yaml_strict(
            self.contract_root / "ASAP_BLOCK_experiment_schema_v1_3_12.yaml"
        )
        self.dictionary = self.common.load_yaml_strict(
            self.contract_root / "ASAP_BLOCK_data_dictionary_v1_3_12.yaml"
        )
        self.canonical = self.common.load_yaml_strict(
            self.contract_root / "ASAP_BLOCK_canonical_serialization_v1_3_12.yaml"
        )
        self.interface_manifest = self.common.load_yaml_strict(
            self.contract_root / "ASAP_BLOCK_machine_interface_manifest_v1_3_12.yaml"
        )
        self.formal_template = self.common.load_yaml_strict(
            self.contract_root / "ASAP_BLOCK_formal_contract_template_v1_3_12.yaml"
        )
        self._validate_contract_surface()

    @property
    def table_names(self) -> List[str]:
        return list(self.schema["tables"])

    def table(self, table_name: str) -> Mapping[str, Any]:
        try:
            return self.schema["tables"][table_name]
        except KeyError as exc:
            raise ContractBindingError("unknown contract table: {}".format(table_name)) from exc

    def fields(self, table_name: str) -> Mapping[str, Any]:
        self.table(table_name)
        return self.dictionary["tables"][table_name]["fields"]

    def canonical_columns(self, table_name: str) -> List[str]:
        return list(self.table(table_name)["canonical_column_order"])

    def required_columns(self, table_name: str) -> List[str]:
        return list(self.table(table_name)["required"])

    def primary_key(self, table_name: str) -> List[str]:
        return list(self.table(table_name)["primary_key"])

    def enum_values(self, enum_name: str) -> List[str]:
        try:
            return list(self.schema["enums"][enum_name])
        except KeyError as exc:
            raise ContractBindingError("unknown contract enum: {}".format(enum_name)) from exc

    def empty_row(self, table_name: str) -> Dict[str, Any]:
        return {column: None for column in self.canonical_columns(table_name)}

    def assert_python_enum(self, enum_type: Any, contract_enum: str) -> None:
        observed = [member.value for member in enum_type]
        expected = self.enum_values(contract_enum)
        if observed != expected:
            raise ContractBindingError(
                "Python enum differs from {}: {} != {}".format(
                    contract_enum, observed, expected
                )
            )

    def _validate_contract_surface(self) -> None:
        if str(self.schema.get("schema_metadata", {}).get("version")) != VERSION:
            raise ContractBindingError("schema version is not v1.3.12")
        if str(self.dictionary.get("data_dictionary_metadata", {}).get("version")) != VERSION:
            raise ContractBindingError("data dictionary version is not v1.3.12")
        tables = self.schema.get("tables", {})
        if set(tables) != set(self.dictionary.get("tables", {})):
            raise ContractBindingError("schema/data-dictionary table set mismatch")
        manifest_tables = self.interface_manifest.get("projection", {}).get("table_names")
        if manifest_tables != list(tables):
            raise ContractBindingError("interface-manifest table order mismatch")
        for table_name, table in tables.items():
            classified: List[str] = []
            for field_class in (
                "required",
                "conditionally_required",
                "optional_diagnostic",
            ):
                classified.extend(table.get(field_class, []))
            if classified != table.get("canonical_column_order"):
                raise ContractBindingError(
                    "canonical field classification mismatch: {}".format(table_name)
                )
            if set(classified) != set(
                self.dictionary["tables"][table_name]["fields"]
            ):
                raise ContractBindingError(
                    "schema/dictionary field mismatch: {}".format(table_name)
                )

    def _encode_value(self, value: Any, spec: Mapping[str, Any]) -> str:
        if value is None:
            if not spec.get("nullable"):
                raise ContractBindingError("null supplied for non-nullable field")
            return ""
        if isinstance(value, float):
            raise ContractBindingError("ordinary float is forbidden in canonical rows")
        field_type = spec.get("type")
        if field_type == "boolean":
            if not isinstance(value, bool):
                raise ContractBindingError("boolean field requires bool")
            return "true" if value else "false"
        if field_type == "integer":
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ContractBindingError("integer field requires int or canonical string")
            return str(value)
        if field_type == "canonical_number":
            if isinstance(value, bool):
                raise ContractBindingError("canonical number cannot be bool")
            if isinstance(value, Fraction):
                return _fraction_text(value)
            if isinstance(value, int):
                return str(value)
            if isinstance(value, str):
                return value
            raise ContractBindingError(
                "canonical number requires Fraction, int, or canonical string"
            )
        if hasattr(value, "value") and isinstance(value.value, str):
            value = value.value
        if not isinstance(value, str):
            raise ContractBindingError(
                "{} field requires a string".format(field_type or "string")
            )
        if value == "":
            raise ContractBindingError("empty string is not canonical null; use None")
        return value

    def encode_row(
        self, table_name: str, row: Mapping[str, Any], validate: bool = True
    ) -> Dict[str, str]:
        columns = self.canonical_columns(table_name)
        missing = set(columns) - set(row)
        extra = set(row) - set(columns)
        if missing or extra:
            raise ContractBindingError(
                "row shape mismatch {}: missing={} extra={}".format(
                    table_name, sorted(missing), sorted(extra)
                )
            )
        specs = self.fields(table_name)
        encoded: Dict[str, str] = {}
        for column in columns:
            encoded[column] = self._encode_value(row[column], specs[column])
        if validate:
            self.validate_encoded_row(table_name, encoded)
        return encoded

    def validate_encoded_row(
        self, table_name: str, row: Mapping[str, str]
    ) -> None:
        columns = self.canonical_columns(table_name)
        if set(row) != set(columns):
            raise ContractBindingError("encoded row has unknown or missing columns")
        specs = self.fields(table_name)
        table = self.table(table_name)
        for column in table["required"]:
            if row.get(column, "") == "":
                raise ContractBindingError(
                    "required null: {}.{}".format(table_name, column)
                )
        for column in columns:
            value = row[column]
            if value != "":
                try:
                    self.common.validate_scalar(
                        value,
                        specs[column],
                        self.schema["enums"],
                        self.schema["failure_masks"],
                    )
                except Exception as exc:
                    raise ContractBindingError(
                        "invalid {}.{}: {}".format(table_name, column, exc)
                    ) from exc
        for rule in table.get("conditional_rules", []):
            if self.common.condition_matches(dict(row), rule["if"]):
                for column in rule.get("then_required", []):
                    if row.get(column, "") == "":
                        raise ContractBindingError(
                            "conditional required null: {}.{}".format(
                                table_name, column
                            )
                        )
                for column in rule.get("then_null", []):
                    if row.get(column, "") != "":
                        raise ContractBindingError(
                            "conditional non-null: {}.{}".format(
                                table_name, column
                            )
                        )
            else:
                for column in rule.get("else_null", []):
                    if row.get(column, "") != "":
                        raise ContractBindingError(
                            "conditional else-non-null: {}.{}".format(
                                table_name, column
                            )
                        )

    def decode_row(self, table_name: str, row: Mapping[str, str]) -> Dict[str, Any]:
        self.validate_encoded_row(table_name, row)
        specs = self.fields(table_name)
        decoded: Dict[str, Any] = {}
        for column in self.canonical_columns(table_name):
            value = row[column]
            if value == "":
                decoded[column] = None
            elif specs[column].get("type") == "integer":
                decoded[column] = int(value)
            elif specs[column].get("type") == "boolean":
                decoded[column] = value == "true"
            elif specs[column].get("type") == "canonical_number":
                numerator, denominator = self.common.parse_canonical_number(value)
                decoded[column] = Fraction(numerator, denominator)
            else:
                decoded[column] = value
        return decoded

    def row_hash(
        self, table_name: str, row: Mapping[str, Any], hash_field: str, domain: str
    ) -> str:
        if hash_field not in self.canonical_columns(table_name):
            raise ContractBindingError("hash field is absent from table")
        specs = self.fields(table_name)
        preimage: Dict[str, Any] = {}
        for column in self.canonical_columns(table_name):
            if column == hash_field:
                continue
            text = self._encode_value(row[column], specs[column])
            preimage[column] = None if text == "" else text
        return self.common.domain_hash(domain, preimage)

    def task_result_hash(self, row: Mapping[str, Any]) -> str:
        return self.row_hash(
            "per_task_results.csv",
            row,
            "task_result_hash",
            "ASAP_BLOCK:TASK_RESULT:v1.3.12",
        )

    def dependency_record_hash(self, row: Mapping[str, Any]) -> str:
        return self.row_hash(
            "rta_dependency_records.csv",
            row,
            "dependency_record_hash",
            "ASAP_BLOCK:DEPENDENCY_RECORD:v1.3.12",
        )

    def carry_in_vector_hash(
        self,
        analysis_run_id: str,
        target_task_id: str,
        entries: Iterable[Mapping[str, Any]],
    ) -> str:
        ordered = sorted(entries, key=lambda entry: int(entry["hp_task_id"]))
        preimage = {
            "analysis_run_id": analysis_run_id,
            "target_task_id": str(target_task_id),
            "entries": [
                {
                    "hp_task_id": str(entry["hp_task_id"]),
                    "theta_value": str(entry["theta_value"]),
                    "source_analysis_run_id": str(entry["source_analysis_run_id"]),
                    "source_task_id": str(entry["source_task_id"]),
                    "source_task_certification_status": str(
                        entry["source_task_certification_status"]
                    ),
                }
                for entry in ordered
            ],
        }
        return self.common.domain_hash(
            "ASAP_BLOCK:CARRY_IN_VECTOR:v1.3.12", preimage
        )

    def validate_dataset(
        self, rows_by_table: Mapping[str, Iterable[Mapping[str, Any]]]
    ) -> Dict[str, List[Dict[str, str]]]:
        if set(rows_by_table) != set(self.table_names):
            raise ContractBindingError(
                "runtime table set mismatch: missing={} extra={}".format(
                    sorted(set(self.table_names) - set(rows_by_table)),
                    sorted(set(rows_by_table) - set(self.table_names)),
                )
            )
        encoded: Dict[str, List[Dict[str, str]]] = {}
        for table_name in self.table_names:
            table_rows = [self.encode_row(table_name, row) for row in rows_by_table[table_name]]
            primary_key = self.primary_key(table_name)
            keys = [tuple(row[column] for column in primary_key) for row in table_rows]
            if len(keys) != len(set(keys)):
                raise ContractBindingError("duplicate primary key: {}".format(table_name))
            encoded[table_name] = sorted(
                table_rows,
                key=lambda row: self.common.canonical_json_bytes(
                    [row[column] for column in primary_key]
                ),
            )
        for table_name in self.table_names:
            table = self.table(table_name)
            for local, target in table.get("foreign_keys", {}).items():
                target_table, target_column = target.rsplit(".", 1)
                target_values = {
                    row[target_column] for row in encoded[target_table]
                }
                for row in encoded[table_name]:
                    if row[local] and row[local] not in target_values:
                        raise ContractBindingError(
                            "dangling foreign key: {}.{}".format(table_name, local)
                        )
            for relation in table.get("composite_foreign_keys", []):
                local_columns = relation["local"]
                target_table = relation["references"]["table"]
                target_columns = relation["references"]["columns"]
                targets = {
                    tuple(row[column] for column in target_columns)
                    for row in encoded[target_table]
                }
                for row in encoded[table_name]:
                    key = tuple(row[column] for column in local_columns)
                    # The frozen validator treats a composite reference as
                    # absent when any component is canonical null.
                    if all(key) and key not in targets:
                        raise ContractBindingError(
                            "dangling composite foreign key: {}:{}".format(
                                table_name, local_columns
                            )
                        )
        return encoded

    def write_tables(
        self, output_root: Path, rows_by_table: Mapping[str, Iterable[Mapping[str, Any]]]
    ) -> Dict[str, str]:
        output_root = Path(output_root)
        output_root.mkdir(parents=True, exist_ok=True)
        encoded = self.validate_dataset(rows_by_table)
        hashes: Dict[str, str] = {}
        for table_name in self.table_names:
            path = output_root / table_name
            columns = self.canonical_columns(table_name)
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=columns, lineterminator="\n"
                )
                writer.writeheader()
                writer.writerows(encoded[table_name])
            hashes[table_name] = self.common.sha256_file(path)
        return hashes

    def read_tables(self, output_root: Path) -> Dict[str, List[Dict[str, Any]]]:
        output_root = Path(output_root)
        raw: Dict[str, List[Dict[str, str]]] = {}
        decoded: Dict[str, List[Dict[str, Any]]] = {}
        for table_name in self.table_names:
            header, rows = self.common.read_csv_strict(output_root / table_name)
            if header != self.canonical_columns(table_name):
                raise ContractBindingError(
                    "noncanonical CSV header: {}".format(table_name)
                )
            raw[table_name] = rows
            decoded[table_name] = [self.decode_row(table_name, row) for row in rows]
        # Re-encode decoded canonical nulls as ``None`` before validating the
        # complete key/lineage graph.  Raw CSV uses ``""`` for null, while the
        # writer API deliberately rejects callers that pass an empty string.
        self.validate_dataset(decoded)
        return decoded


def main(argv: Optional[List[str]] = None) -> int:
    """Validate a runtime table directory against the frozen v1.3.12 schema."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime_table_root", type=Path)
    parser.add_argument(
        "--contract-root",
        type=Path,
        default=DEFAULT_CONTRACT_ROOT,
        help="frozen v1.3.12 contract directory",
    )
    args = parser.parse_args(argv)
    try:
        binding = V1312SchemaBinding(args.contract_root)
        tables = binding.read_tables(args.runtime_table_root)
    except Exception as exc:
        print("v1.3.12 schema validation failed: {}".format(exc), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "contract_version": VERSION,
                "status": "VALID",
                "table_count": len(tables),
                "row_count": sum(len(rows) for rows in tables.values()),
            },
            sort_keys=True,
        )
    )
    return 0


__all__ = [
    "CONTRACT_DIRECTORY_NAME",
    "ContractBindingError",
    "DEFAULT_CONTRACT_ROOT",
    "V1312SchemaBinding",
    "VERSION",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
