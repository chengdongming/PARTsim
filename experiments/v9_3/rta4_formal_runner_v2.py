"""Official bounded execution pipeline for RTA4 formal shared-energy V2."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from . import exact_energy
from .rta4_formal_config import domain_hash
from .rta4_formal_config_v2 import (
    RTA4_FORMAL_PROFILE_V2, formal_taskset_store_identity_v2,
)
from .rta4_formal_execution import (
    ProductionRTAExecutorV2, ProductionSimulationExecutorV2,
    RTA4ExecutionError,
)
from .rta4_formal_lifecycle_v2 import (
    RTA4_CHECKPOINT_V2, RTA4_RESULT_ROW_SCHEMA_V2,
    RTA4FormalResultWriterV2, RTA4FormalTasksetStoreV2,
    retry_resume_identity_v2, validate_prepared_config_v2,
    validate_test_authorization_v2,
)
from .rta4_formal_plan_v2 import iter_formal_plan_v2
from .rta4_formal_schema_v2 import formal_schema_hash_v2
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256
from .rta4_production_build_manifest import (
    load_and_validate_production_build_manifest,
)
from .rta4_shared_energy import (
    FrozenMapping, initialize_shared_energy_run_from_manifest_path,
)
from .rta4_taskset_v2 import (
    ProductionTasksetProviderV2, TasksetIdentityCertificateV2,
)


@dataclass(frozen=True)
class ExecutionSummaryV2:
    core: str
    execution_class: str
    authorization_id: str
    production_build_manifest_identity: str
    processed_records: int
    pending_records: int
    complete: bool
    checkpoint_path: Path


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _timed_simulation(
    executor: ProductionSimulationExecutorV2, record: Any,
    certificate: TasksetIdentityCertificateV2,
) -> Mapping[str, Any]:
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    try:
        result = executor(record, certificate)
        return FrozenMapping({
            "result": result,
            "status": "COMPLETED",
            "error_classification": "NONE",
            "runtime_wall_seconds": format(
                time.perf_counter() - started_wall, ".17g",
            ),
            "runtime_cpu_seconds": format(
                time.process_time() - started_cpu, ".17g",
            ),
        })
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        return FrozenMapping({
            "result": {"failure_reason": f"{type(exc).__name__}: {exc}"[:500]},
            "status": "INTERNAL_ERROR",
            "error_classification": f"{type(exc).__name__}: {exc}"[:500],
            "runtime_wall_seconds": format(
                time.perf_counter() - started_wall, ".17g",
            ),
            "runtime_cpu_seconds": format(
                time.process_time() - started_cpu, ".17g",
            ),
        })


class AuthorizedRTA4RunnerV2:
    """V2-only plan/provider/material/executor/store/writer/resume pipeline.

    Only the explicitly disjoint bounded TEST_ONLY authorization exists today;
    the six checked-in V2 configurations remain UNAUTHORIZED_PRE_PILOT.
    """

    def __init__(
        self, prepared_config: Mapping[str, Any],
        authorization: Mapping[str, Any],
    ) -> None:
        self.prepared = validate_prepared_config_v2(prepared_config)
        self.authorization = validate_test_authorization_v2(
            authorization, prepared_config=self.prepared,
        )
        self.config = self.prepared["scientific_config"]

    def _records(self) -> tuple[Any, ...]:
        selected = set(self.prepared["selected_ordinals"])
        records = tuple(
            record for record in iter_formal_plan_v2(self.config)
            if record.ordinal in selected
        )
        if (
            len(records) != len(selected)
            or tuple(record.ordinal for record in records)
            != tuple(self.prepared["selected_ordinals"])
        ):
            raise RTA4ExecutionError("bounded V2 ordinals lie outside the plan")
        return records

    @staticmethod
    def _retry_identity(
        prepared: Mapping[str, Any], authorization: Mapping[str, Any],
        record: Any, certificate: TasksetIdentityCertificateV2,
        binding: Mapping[str, str], service: Any,
    ) -> str:
        if record.kind == "simulation":
            method = "CORE3_SIMULATION_V2"
            e0 = str(record.material["physical_initial_energy"])
            budgets = [prepared["operational"]["simulation_timeout_seconds"]]
        else:
            method = str(record.material["method"])
            e0 = str(record.material["exact_e0"])
            timeout = prepared["timeout_contract"][method]
            budgets = [
                timeout["initial_timeout_seconds"],
                timeout["retry_timeout_seconds"],
            ][:timeout["maximum_attempts"]]
        return retry_resume_identity_v2(
            prepared_config_id=prepared["prepared_config_id"],
            authorization_id=authorization["authorization_id"],
            plan_identity=prepared["plan_identity"],
            production_manifest_identity=(
                service.production_build_manifest_identity
            ),
            plan_record_identity=record.record_id,
            taskset_identity=certificate.taskset_id,
            task_energy_material_identity=binding[
                "task_energy_material_identity"
            ],
            service_material_identity=binding["service_material_identity"],
            beta_material_identity=service.beta_material_identity,
            method=method, exact_e0=e0, timeout_sequence=budgets,
        )

    def _row(
        self, *, writer: RTA4FormalResultWriterV2, record: Any,
        certificate: TasksetIdentityCertificateV2,
        binding: Mapping[str, str], service: Any, result: Mapping[str, Any],
    ) -> Dict[str, Any]:
        retry_id = self._retry_identity(
            self.prepared, self.authorization, record, certificate,
            binding, service,
        )
        if record.kind == "simulation":
            timed = result
            response = _plain(timed["result"])
            status = str(timed["status"])
            method = "CORE3_SIMULATION_V2"
            e0 = str(record.material["physical_initial_energy"])
            timeout_seconds = self.prepared["operational"][
                "simulation_timeout_seconds"
            ]
            analysis_identity = (
                response.get("simulation_identity")
                if status == "COMPLETED"
                else domain_hash(
                    "ASAP_BLOCK:V9.3:RTA4_FAILED_SIMULATION:v2", {
                        "plan_record_identity": record.record_id,
                        "retry_resume_identity": retry_id,
                    },
                )
            )
            attempts = [{
                "attempt_index": 0,
                "timeout_seconds": timeout_seconds,
                "status": status,
                "runtime_wall_seconds": str(timed["runtime_wall_seconds"]),
                "runtime_cpu_seconds": str(timed["runtime_cpu_seconds"]),
                "peak_rss_bytes": 0,
                "error_classification": str(timed["error_classification"]),
                "analysis_identity": analysis_identity,
                "taskset_identity": certificate.taskset_id,
                "task_energy_material_identity": binding[
                    "task_energy_material_identity"
                ],
                "service_material_identity": binding[
                    "service_material_identity"
                ],
                "beta_material_identity": service.beta_material_identity,
                "production_build_manifest_identity": (
                    writer.production_manifest_identity
                ),
            }]
        else:
            response = {
                key: _plain(value) for key, value in result.items()
                if key not in {
                    "attempts", "timeout_seconds", "runtime_wall_seconds",
                    "runtime_cpu_seconds", "peak_rss_bytes",
                }
            }
            status = str(result["solver_status"])
            method = str(record.material["method"])
            e0 = str(record.material["exact_e0"])
            timeout_seconds = int(result["timeout_seconds"])
            attempts = _plain(result["attempts"])
        material = {
            "row_schema": RTA4_RESULT_ROW_SCHEMA_V2,
            "profile": RTA4_FORMAL_PROFILE_V2,
            "schema_sha256": formal_schema_hash_v2(),
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            "config_identity": self.prepared["config_identity"],
            "plan_identity": self.prepared["plan_identity"],
            "plan_record_identity": record.record_id,
            "execution_identity": record.execution_id,
            "production_build_manifest_identity": (
                writer.production_manifest_identity
            ),
            "source_commit": writer.source_commit,
            "source_tree": writer.source_tree,
            "taskset_source_sha256": certificate.taskset_source_sha256,
            "taskset_identity": certificate.taskset_id,
            "task_energy_material_identity": binding[
                "task_energy_material_identity"
            ],
            "service_material_identity": binding[
                "service_material_identity"
            ],
            "beta_material_identity": service.beta_material_identity,
            "method": method,
            "exact_e0": e0,
            "status": status,
            "response_result": response,
            "timeout_seconds": timeout_seconds,
            "attempts": attempts,
            "retry_resume_identity": retry_id,
        }
        return {
            **material,
            "result_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4_RESULT:v2", material,
            ),
        }

    def run(
        self, *, resume: bool = False, validate_only: bool = False,
        max_records: int | None = None,
    ) -> ExecutionSummaryV2:
        if type(resume) is not bool or type(validate_only) is not bool:
            raise RTA4ExecutionError("V2 resume/validate flags must be boolean")
        if max_records is not None and (
            type(max_records) is not int or max_records < 0
        ):
            raise RTA4ExecutionError("V2 max_records must be non-negative")
        operation = self.prepared["operational"]
        # This is the mandatory live gate.  It occurs before store/output
        # construction and requires the complete default source closure.
        manifest = load_and_validate_production_build_manifest(
            operation["production_manifest_path"],
            require_default_closure=True,
        )
        if manifest["repository"]["source_root"] != operation["source_root"]:
            raise RTA4ExecutionError("V2 manifest/source root mismatch")
        if manifest["manifest_id"] != self.prepared[
            "production_build_manifest_identity"
        ]:
            raise RTA4ExecutionError("V2 prepared/manifest identity drift")
        output = Path(operation["output_root"])
        if not resume and not validate_only and output.exists() and any(output.iterdir()):
            raise RTA4ExecutionError("non-resume V2 execution refuses a non-empty root")
        records = self._records()
        provider = ProductionTasksetProviderV2(self.config)
        context = initialize_shared_energy_run_from_manifest_path(
            records, taskset_provider=provider,
            production_build_manifest_path=operation[
                "production_manifest_path"
            ],
            system_config_path=operation["system_config_path"],
            energy_support_path=operation["energy_support_path"],
            source_root=operation["source_root"],
            taskset_store_identity=formal_taskset_store_identity_v2(),
        )
        if (
            not context.formal_ready
            or context.production_build_manifest_identity != manifest["manifest_id"]
        ):
            raise RTA4ExecutionError("V2 formal run context is not live-validated")
        certificates = {
            record.record_id: provider(record) for record in records
        }
        store = RTA4FormalTasksetStoreV2(
            operation["taskset_store"],
            production_manifest_identity=manifest["manifest_id"],
            require_existing_namespace=resume or validate_only,
        )
        writer = RTA4FormalResultWriterV2(
            operation["output_root"], prepared_config=self.prepared,
            authorization=self.authorization, production_manifest=manifest,
            records=records,
            require_existing_namespace=resume or validate_only,
        )
        completed = dict(writer.completed_rows())
        for record in records:
            certificate = certificates[record.record_id]
            binding = context.binding_for(record.record_id)
            task_energy = context.task_energy_materials[
                binding["task_energy_material_identity"]
            ]
            service = context.service_materials[
                binding["service_material_identity"]
            ]
            if resume or validate_only:
                store.validate_binding(certificate, task_energy)
            elif record.execution_id not in completed:
                store.put(certificate, task_energy)
            row = completed.get(record.execution_id)
            if row is not None:
                expected_retry = self._retry_identity(
                    self.prepared, self.authorization, record, certificate,
                    binding, service,
                )
                for key, expected in (
                    ("taskset_source_sha256", certificate.taskset_source_sha256),
                    ("taskset_identity", certificate.taskset_id),
                    ("task_energy_material_identity", binding[
                        "task_energy_material_identity"
                    ]),
                    ("service_material_identity", binding[
                        "service_material_identity"
                    ]),
                    ("beta_material_identity", service.beta_material_identity),
                    ("retry_resume_identity", expected_retry),
                ):
                    if row[key] != expected:
                        raise RTA4ExecutionError(f"V2 resume {key} drift")
        pending = [
            record for record in records if record.execution_id not in completed
        ]
        if max_records is not None:
            pending = pending[:max_records]
        if validate_only:
            writer.write_checkpoint(completed)
            return ExecutionSummaryV2(
                self.prepared["core"], "TEST_ONLY", self.authorization[
                    "authorization_id"
                ], manifest["manifest_id"], 0, len(records) - len(completed),
                len(completed) == len(records),
                Path(operation["output_root"]) / RTA4_CHECKPOINT_V2,
            )
        rta = None
        simulation = None
        if any(record.kind != "simulation" for record in pending):
            rta = ProductionRTAExecutorV2(
                self.config, run_context=context,
                timeout_contract=self.prepared["timeout_contract"],
            )
        if any(record.kind == "simulation" for record in pending):
            simulation = ProductionSimulationExecutorV2(
                self.config, run_context=context,
                production_manifest=manifest,
                system_config_path=operation["system_config_path"],
                energy_support_path=operation["energy_support_path"],
                output_root=operation["output_root"],
                simulation_timeout_seconds=operation[
                    "simulation_timeout_seconds"
                ],
            )
        processed = 0
        workers = operation["worker_count"]
        max_in_flight = operation["max_in_flight"]
        for start in range(0, len(pending), max_in_flight):
            batch = pending[start:start + max_in_flight]
            futures: list[Future[Any]] = []
            with ThreadPoolExecutor(max_workers=min(workers, len(batch))) as pool:
                for record in batch:
                    certificate = certificates[record.record_id]
                    if record.kind == "simulation":
                        assert simulation is not None
                        futures.append(pool.submit(
                            _timed_simulation, simulation, record, certificate,
                        ))
                    else:
                        assert rta is not None
                        futures.append(pool.submit(rta, record, certificate))
                results = [future.result() for future in futures]
            for record, result in zip(batch, results):
                certificate = certificates[record.record_id]
                binding = context.binding_for(record.record_id)
                service = context.service_materials[
                    binding["service_material_identity"]
                ]
                row = self._row(
                    writer=writer, record=record, certificate=certificate,
                    binding=binding, service=service, result=result,
                )
                writer.write_result(row)
                completed[str(record.execution_id)] = row
                writer.write_checkpoint(completed)
                processed += 1
        checkpoint = writer.write_checkpoint(completed)
        return ExecutionSummaryV2(
            self.prepared["core"], "TEST_ONLY",
            self.authorization["authorization_id"], manifest["manifest_id"],
            processed, len(records) - len(completed), bool(checkpoint["complete"]),
            Path(operation["output_root"]) / RTA4_CHECKPOINT_V2,
        )


__all__ = ["AuthorizedRTA4RunnerV2", "ExecutionSummaryV2"]
