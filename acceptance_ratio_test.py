#!/usr/bin/env python3
"""
接受率分析完整脚本：实验执行 + 数据分析 + 图表生成
- 生成不同利用率的任务集
- 运行仿真获取追踪文件
- 分析追踪文件提取接受率数据（二元可调度性）
- 生成IEEE Transaction风格的接受率图表

修复说明：
1. 实现二元可调度性（Binary Schedulability）：任务集要么完全成功(1.0)，要么失败(0.0)
2. 修复浮点精度问题
3. 确保文件I/O安全性
"""

import json
import hashlib
import math
import re
import subprocess
import time
import yaml
import os
import sys
import argparse
import uuid
import warnings
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from multiprocessing import cpu_count
from pathlib import Path
import numpy as np
import pandas as pd
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams

# ============================================
# Matplotlib 配置（IEEE Transaction 风格）
# ============================================
rcParams['font.family'] = 'serif'
rcParams['font.size'] = 11
rcParams['axes.labelsize'] = 12
rcParams['axes.titlesize'] = 12
rcParams['xtick.labelsize'] = 10
rcParams['ytick.labelsize'] = 10
rcParams['legend.fontsize'] = 10
rcParams['figure.figsize'] = (8, 6)

# ============================================
# 实验配置
# ============================================
CONFIG_TEMPLATE = 'system_config_unified_template.yml'
TASK_GENERATOR = './global_task_generator.py'
SIMULATOR = os.environ.get('PARTSIM_RTSIM_BIN', './build/rtsim/rtsim')
PROJECT_ROOT = Path(__file__).resolve().parent
RTA_TOOL = str(Path(__file__).resolve().parent / 'asap_block_rta.py')
ASAP_BLOCK_ALGORITHM = 'gpfp_asap_block'
RTA_VERSION = 'v20.4'
RTA_INACTIVE_VERSION = 'not_used'
RESULT_SCHEMA_VERSION = 4
TRACE_SCHEMA_VERSION = 2

PER_TASKSET_RESULT_FIELDS = [
    'experiment_id',
    'run_id',
    'source_run_id',
    'output_dir',
    'config_id',
    'config_group_id',
    'seed_base',
    'taskset_seed',
    'normalized_utilization',
    'target_normalized_utilization',
    'target_total_utilization',
    'actual_total_utilization',
    'actual_normalized_utilization',
    'utilization_error_total',
    'utilization_error_normalized',
    'task_util_min',
    'task_util_max',
    'wcet_rounding',
    'deadline_mode',
    'actual_utilization_tolerance_total',
    'task_idx',
    'task_index',
    'taskset_id',
    'taskset_hash',
    'taskset_semantic_hash',
    'taskset_raw_file_hash',
    'seed',
    'algorithm',
    'scheduler',
    'algorithm_display_name',
    'num_tasks',
    'num_cores',
    'battery',
    'initial_energy',
    'initial_energy_ratio',
    'solar_time_ms',
    'harvesting_profile',
    'harvesting_scale',
    'solar_profile_sha256',
    'solar_profile_path_normalized',
    'solar_profile_present',
    'solar_profile_size',
    'solar_source_path',
    'solar_source_sha256',
    'solar_snapshot_relative_path',
    'solar_snapshot_time',
    'actual_simulator_solar_path',
    'simulation_horizon_ms',
    'observed_trace_horizon_ms',
    'trace_schema_version',
    'simulation_completed',
    'simulation_completion_reason',
    'accepted',
    'rejected',
    'timeout',
    'error',
    'status',
    'reason',
    'trace_path',
    'result_schema_version',
    'expected_configured_scheduler',
    'expected_scheduler_display_name',
    'expected_scheduler_implementation',
    'observed_configured_scheduler',
    'observed_scheduler_display_name',
    'observed_scheduler_implementation',
    'observed_scheduler_rtti_name',
    'configured_scheduler',
    'scheduler_display_name',
    'scheduler_implementation',
    'rta_enabled',
    'rta_version',
    'rta_code_fingerprint',
    'rta_code_snapshot_path',
    'rta_code_snapshot_sha256',
    'rta_code_snapshot_size',
    'rta_code_source_path',
    'rta_code_source_sha256',
    'rta_status',
    'rta_attempted',
    'rta_runtime_sec',
    'rta_runtime_source',
    'rta_timed_out',
    'rta_timeout_sec',
    'rta_profile_enabled',
    'rta_profile_task_time_sum_sec',
    'rta_profile_task_count',
    'rta_proven',
    'rta_schedulable',
    'sim_schedulable',
    'soundness_violation',
    'soundness_valid',
    'soundness_excluded_reason',
    'rta_error',
    'rta_reason',
    'rta_response_time_bound',
    'rta_response_bound',
    'simulated_response_time',
    'observed_max_response_time',
    'first_missed_job_release',
    'first_missed_deadline',
    'tightness',
]


class DuplicateResultError(ValueError):
    """Raised when formal result identity is duplicated or ambiguous."""


def _canonicalize_config_value(value):
    """Return a JSON-safe, stable representation for provenance hashing."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, dict):
        return {
            str(key): _canonicalize_config_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if isinstance(value, set):
            items = sorted(items, key=str)
        return [_canonicalize_config_value(item) for item in items]
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating, Decimal)):
        try:
            decimal = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError('non-canonical numeric config value') from exc
        if not decimal.is_finite():
            raise ValueError('config values must be finite')
        if decimal == 0:
            return '0'
        normalized = format(decimal.normalize(), 'f')
        if '.' in normalized:
            normalized = normalized.rstrip('0').rstrip('.')
        return normalized
    raise TypeError('unsupported config value type: {}'.format(type(value)))


def canonical_config_json(config):
    return json.dumps(
        _canonicalize_config_value(config),
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    )


def stable_config_id(config):
    return hashlib.sha256(
        canonical_config_json(config).encode('utf-8')
    ).hexdigest()


def taskset_file_hash(task_file):
    digest = hashlib.sha256()
    with open(task_file, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_semantic_integer(value, field):
    if isinstance(value, bool) or value is None:
        raise ValueError('{} must be an integer'.format(field))
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r'[+-]?\d+', value.strip()):
        return int(value.strip(), 10)
    raise ValueError('{} must be an integer'.format(field))


def taskset_semantic_object(task_file):
    """Return the canonical task behavior consumed by rtsim.

    Generator comments and metadata are deliberately excluded.  Recognized
    defaults mirror rtsim/main.cpp and the explicit experiment task schema;
    unregistered task fields fail closed so a future behavior-affecting field
    cannot silently collide or import path-like provenance into the identity.
    """
    with open(task_file, 'r', encoding='utf-8') as handle:
        document = yaml.safe_load(handle) or {}
    taskset = document.get('taskset')
    if not isinstance(taskset, list):
        raise ValueError('taskset must be a list')

    canonical_tasks = []
    recognized = {
        'name', 'iat', 'deadline', 'runtime', 'startcpu', 'cbs_runtime',
        'cbs_period', 'cbs_deadline', 'ph', 'qs', 'params', 'code',
        'priority', 'energy', 'task_type', 'type', 'resources',
    }
    for position, original in enumerate(taskset):
        if not isinstance(original, dict):
            raise ValueError('taskset entry {} must be an object'.format(position))
        name = original.get('name')
        if not isinstance(name, str) or not name:
            raise ValueError('task name must be a non-empty string')
        iat = _strict_semantic_integer(original.get('iat'), name + '.iat')
        deadline = _strict_semantic_integer(
            original.get('deadline', iat), name + '.deadline'
        )
        params = original.get('params', '')
        if params is None:
            params = ''
        if not isinstance(params, str):
            raise ValueError(name + '.params must be a string')
        phase = _strict_semantic_integer(original.get('ph', 0), name + '.ph')
        match = re.search(r'(?:^|,)\s*arrival_offset=([^,]+)', params)
        if match:
            phase = _strict_semantic_integer(
                match.group(1).strip(), name + '.params.arrival_offset'
            )
        code = original.get('code')
        if not isinstance(code, list):
            raise ValueError(name + '.code must be a list')
        instructions = []
        for instruction in code:
            if not isinstance(instruction, str):
                raise ValueError(name + '.code entries must be strings')
            normalized = instruction.strip()
            if normalized and not normalized.endswith(';'):
                normalized += ';'
            instructions.append(normalized)

        task = {
            'load_index': position,
            'name': name,
            'iat': iat,
            'deadline': deadline,
            'runtime': (
                None if 'runtime' not in original else
                _strict_semantic_integer(original['runtime'], name + '.runtime')
            ),
            'startcpu': _strict_semantic_integer(
                original.get('startcpu', 0), name + '.startcpu'
            ),
            'cbs_runtime': _strict_semantic_integer(
                original.get('cbs_runtime', 0), name + '.cbs_runtime'
            ),
            'cbs_period': _strict_semantic_integer(
                original.get('cbs_period', 0), name + '.cbs_period'
            ),
            'cbs_deadline': _strict_semantic_integer(
                original.get('cbs_deadline', 0), name + '.cbs_deadline'
            ),
            'phase': phase,
            'qs': _strict_semantic_integer(original.get('qs', 100), name + '.qs'),
            'params': params,
            'instructions': instructions,
            'priority': _canonicalize_config_value(original.get('priority')),
            'energy': _canonicalize_config_value(original.get('energy')),
            'task_type': _canonicalize_config_value(
                original.get('task_type', original.get('type'))
            ),
            'task_resources': _canonicalize_config_value(
                original.get('resources')
            ),
        }
        unknown = sorted(set(original) - recognized)
        if unknown:
            raise ValueError('{} unknown_task_field {}'.format(
                name, ','.join(map(str, unknown))
            ))
        canonical_tasks.append(task)
    resources = document.get('resources', [])
    if resources is None:
        resources = []
    if not isinstance(resources, list):
        raise ValueError('resources must be a list')
    # Resource construction is also ordered: declaration position can affect
    # resource/entity IDs and therefore must not be normalized away.
    canonical_resources = [
        {
            'load_index': position,
            'resource': _canonicalize_config_value(resource),
        }
        for position, resource in enumerate(resources)
    ]
    return {'tasks': canonical_tasks, 'resources': canonical_resources}


def taskset_semantic_hash(task_file):
    payload = json.dumps(
        taskset_semantic_object(task_file),
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def rta_code_fingerprint(enabled, entrypoint=None):
    if not enabled:
        return {
            'mode': 'not_used', 'entrypoint': '', 'entrypoint_size': 0,
            'entrypoint_sha256': 'not_used', 'dependency_mode': 'not_used',
            'local_dependency_files': [], 'combined_sha256': 'not_used',
        }
    path = Path(entrypoint or RTA_TOOL).resolve(strict=False)
    if not path.is_file():
        return {
            'mode': 'missing', 'entrypoint': str(path), 'entrypoint_size': 0,
            'entrypoint_sha256': 'missing', 'dependency_mode': 'single_file',
            'local_dependency_files': [], 'combined_sha256': 'missing',
        }
    digest = taskset_file_hash(path)
    combined = hashlib.sha256(
        ('asap_block_rta.py\0' + digest).encode('utf-8')
    ).hexdigest()
    return {
        'mode': 'snapshot_source',
        'entrypoint': str(path),
        'entrypoint_size': path.stat().st_size,
        'entrypoint_sha256': digest,
        'dependency_mode': 'single_file',
        'local_dependency_files': [],
        'combined_sha256': combined,
    }


def _atomic_write_bytes(path, payload, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.partial.' + uuid.uuid4().hex)
    try:
        with temporary.open('wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_dataframe_to_csv(frame, path):
    payload = frame.to_csv(index=False).encode('utf-8')
    _atomic_write_bytes(path, payload)


def solar_profile_provenance(config_path, use_real_solar_data):
    """Describe the exact real-solar bytes a simulator run can consume."""
    provenance = {
        'enabled': bool(use_real_solar_data),
        'mode': 'real_solar_csv' if use_real_solar_data else 'not_used',
        'path_normalized': '',
        'present': False,
        'size': 0,
        'sha256': 'not_used' if not use_real_solar_data else 'missing',
        'pv_efficiency': None,
        'pv_area_m2': None,
        'periodic_collection_interval_ms': None,
    }
    try:
        with open(config_path, 'r', encoding='utf-8') as handle:
            document = yaml.safe_load(handle) or {}
        energy = document.get('energy_management') or {}
        raw_path = energy.get('solar_data_file')
        provenance.update({
            'pv_efficiency': energy.get('pv_efficiency'),
            'pv_area_m2': energy.get('pv_area_m2'),
            'periodic_collection_interval_ms': energy.get(
                'periodic_collection_interval_ms'
            ),
        })
        if raw_path is None or isinstance(raw_path, bool):
            if use_real_solar_data:
                provenance['sha256'] = 'missing_path'
            return provenance
        profile_path = Path(str(raw_path)).expanduser()
        if not profile_path.is_absolute():
            # Formal runners execute rtsim in the project root, so relative
            # paths must be normalized against that same directory.
            profile_path = PROJECT_ROOT / profile_path
        profile_path = profile_path.resolve(strict=False)
        provenance['path_normalized'] = str(profile_path)
        provenance['present'] = profile_path.is_file()
        if profile_path.is_file():
            provenance['size'] = profile_path.stat().st_size
            if use_real_solar_data:
                provenance['sha256'] = taskset_file_hash(profile_path)
        elif use_real_solar_data:
            provenance['sha256'] = 'missing'
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        if use_real_solar_data:
            provenance['sha256'] = 'config_unreadable'
    return provenance


def create_solar_snapshot(config_path, output_dir, use_real_solar_data):
    """Copy the simulator's solar source into a verified run-local input."""
    source = solar_profile_provenance(config_path, use_real_solar_data)
    if not use_real_solar_data:
        return {
            **source, 'source_original_path': '', 'source_sha256': 'not_used',
            'snapshot_relative_path': '', 'snapshot_path': '',
            'snapshot_sha256': 'not_used', 'snapshot_size': 0,
            'actual_simulator_solar_path': '',
        }
    if source.get('sha256') in {
            'missing', 'missing_path', 'config_unreadable'}:
        raise ValueError('real solar source is unavailable: {}'.format(
            source.get('sha256')
        ))
    source_path = Path(source['path_normalized'])
    payload = source_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != source['sha256']:
        raise ValueError('solar source changed while creating snapshot')
    relative = Path('inputs') / 'solar' / ('solar_profile_' + digest + '.csv')
    snapshot_path = Path(output_dir) / relative
    _atomic_write_bytes(snapshot_path, payload, mode=0o444)
    snapshot_hash = taskset_file_hash(snapshot_path)
    if snapshot_hash != digest or snapshot_path.stat().st_size != len(payload):
        raise ValueError('solar snapshot verification failed')
    return {
        **source,
        'source_original_path': str(source_path),
        'source_snapshot_time': datetime.utcnow().isoformat() + 'Z',
        'source_sha256': digest,
        'snapshot_relative_path': relative.as_posix(),
        'snapshot_path': str(snapshot_path.resolve()),
        'snapshot_sha256': snapshot_hash,
        'snapshot_size': len(payload),
        'actual_simulator_solar_path': str(snapshot_path.resolve()),
        'path_normalized': str(snapshot_path.resolve()),
        'sha256': snapshot_hash,
        'size': len(payload),
        'present': True,
    }


def create_rta_code_snapshot(output_dir, enabled, entrypoint=None):
    source = rta_code_fingerprint(enabled, entrypoint)
    if not enabled:
        return {**source, 'snapshot_relative_path': '', 'snapshot_path': ''}
    if source['mode'] == 'missing':
        raise ValueError('RTA entrypoint is unavailable: {}'.format(
            source['entrypoint']
        ))
    payload = Path(source['entrypoint']).read_bytes()
    relative = Path('inputs') / 'rta' / (
        'asap_block_rta_' + source['entrypoint_sha256'] + '.py'
    )
    snapshot_path = Path(output_dir) / relative
    _atomic_write_bytes(snapshot_path, payload, mode=0o444)
    snapshot_hash = taskset_file_hash(snapshot_path)
    if snapshot_hash != source['entrypoint_sha256']:
        raise ValueError('RTA code snapshot verification failed')
    return {
        **source, 'mode': 'immutable_snapshot',
        'snapshot_relative_path': relative.as_posix(),
        'snapshot_path': str(snapshot_path.resolve()),
        'snapshot_sha256': snapshot_hash,
        'snapshot_size': snapshot_path.stat().st_size,
    }


def verify_snapshot(path, expected_sha256, expected_size):
    snapshot = Path(path)
    return (
        snapshot.is_file()
        and snapshot.stat().st_size == int(expected_size)
        and taskset_file_hash(snapshot) == expected_sha256
    )


def get_system_cores(config_path):
    """从配置文件中读取系统核心数"""
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f.read())
        return int(config['cpu_islands'][0]['numcpus'])


def _finite_float_or_blank(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ''
    return number if math.isfinite(number) else ''


def load_taskset_utilization_metadata(
    task_file,
    target_normalized_utilization=None,
    target_total_utilization=None,
    num_cores=None,
    task_util_min=0.01,
    task_util_max=0.8,
    wcet_rounding='floor',
    deadline_mode='implicit',
    actual_utilization_tolerance_total='',
):
    """Read generator utilization metadata with a taskset-based fallback."""
    metadata = {}
    tasks = []
    try:
        with open(task_file, 'r', encoding='utf-8') as handle:
            document = yaml.safe_load(handle) or {}
        if isinstance(document, dict):
            metadata = document.get('metadata') or {}
            tasks = document.get('taskset') or []
    except Exception:
        metadata = {}
        tasks = []

    actual_total = metadata.get('actual_total_utilization')
    if actual_total is None:
        actual_total = sum(
            float(task.get('runtime', 0)) / float(task.get('iat', 1))
            for task in tasks
            if isinstance(task, dict)
            and str(task.get('name', '')).startswith('task_')
            and float(task.get('iat', 0) or 0) > 0
        )
    actual_total = _finite_float_or_blank(actual_total)

    cores = metadata.get('M', metadata.get('num_cores', num_cores))
    try:
        cores_float = float(cores)
    except (TypeError, ValueError):
        cores_float = float(num_cores or 0)

    actual_normalized = metadata.get('actual_normalized_utilization')
    if actual_normalized is None and actual_total != '' and cores_float > 0:
        actual_normalized = float(actual_total) / cores_float
    actual_normalized = _finite_float_or_blank(actual_normalized)

    target_total = metadata.get(
        'target_total_utilization',
        target_total_utilization,
    )
    target_normalized = metadata.get(
        'target_normalized_utilization',
        target_normalized_utilization,
    )
    target_total = _finite_float_or_blank(target_total)
    target_normalized = _finite_float_or_blank(target_normalized)

    utilization_error_total = ''
    if actual_total != '' and target_total != '':
        utilization_error_total = float(actual_total) - float(target_total)

    utilization_error_normalized = ''
    if actual_normalized != '' and target_normalized != '':
        utilization_error_normalized = (
            float(actual_normalized) - float(target_normalized)
        )

    return {
        'target_normalized_utilization': target_normalized,
        'target_total_utilization': target_total,
        'actual_total_utilization': actual_total,
        'actual_normalized_utilization': actual_normalized,
        'utilization_error_total': utilization_error_total,
        'utilization_error_normalized': utilization_error_normalized,
        'task_util_min': metadata.get('task_util_min', task_util_min),
        'task_util_max': metadata.get('task_util_max', task_util_max),
        'wcet_rounding': metadata.get('wcet_rounding', wcet_rounding),
        'deadline_mode': metadata.get('deadline_mode', deadline_mode),
        'actual_utilization_tolerance_total': metadata.get(
            'actual_utilization_tolerance_total',
            actual_utilization_tolerance_total,
        ),
    }


def hash_file(path):
    """Return the SHA-256 digest of a file used by an experiment run."""
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _base_rta_result(status='disabled'):
    return {
        'rta_enabled': False,
        'rta_version': RTA_INACTIVE_VERSION,
        'rta_status': status,
        'rta_proven_under_assumptions': False,
        'rta_conditional': True,
        'rta_assumptions': [],
        'rta_horizon_ms': None,
        'rta_initial_energy': None,
        'rta_attempted': False,
        'rta_runtime_sec': None,
        'rta_runtime_source': '',
        'rta_timed_out': False,
        'rta_timeout_sec': None,
        'rta_profile_enabled': False,
        'rta_profile_task_time_sum_sec': None,
        'rta_profile_task_count': 0,
        'rta_unproven_tasks': [],
        'rta_failure_reasons': {},
        'rta_error': None,
        'rta_system_config': None,
        'rta_system_config_hash': None,
        'rta_report': None,
    }


def aggregate_rta_profile_task_times(report):
    """Return the sum and count of valid internal per-task profile times."""
    if not isinstance(report, dict):
        return None, 0
    tasks = report.get('tasks')
    if not isinstance(tasks, list):
        return None, 0

    values = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        profile = task.get('rta_profile')
        if not isinstance(profile, dict):
            continue
        value = profile.get('total_time_sec')
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        ):
            values.append(float(value))
    return (sum(values), len(values)) if values else (None, 0)


def parse_rta_json(payload, assume_no_overflow):
    """Convert the RTA JSON contract into an observational run result."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError('RTA output must be a JSON object')
    if 'proven_under_assumptions' not in payload:
        raise ValueError('RTA JSON is missing proven_under_assumptions')
    if payload.get('rta_version') != RTA_VERSION:
        raise ValueError(
            'RTA JSON version must be {}, got {!r}'.format(
                RTA_VERSION, payload.get('rta_version')
            )
        )

    tasks = payload.get('tasks', [])
    if not isinstance(tasks, list):
        raise ValueError('RTA JSON tasks must be a list')

    unproven_tasks = []
    failure_reasons = {}
    for task_result in tasks:
        if not isinstance(task_result, dict):
            raise ValueError('RTA JSON task entries must be objects')
        task_name = str(task_result.get('task_name', '<unknown>'))
        if not bool(task_result.get('proven_under_assumptions', False)):
            unproven_tasks.append(task_name)
            reason = task_result.get('failure_reason')
            if reason:
                failure_reasons[task_name] = str(reason)

    reported_proven = payload['proven_under_assumptions']
    if not isinstance(reported_proven, bool):
        raise ValueError('RTA proven_under_assumptions must be boolean')
    proven = reported_proven and bool(assume_no_overflow)
    if not assume_no_overflow:
        failure_reasons.setdefault(
            '_analysis',
            'no-overflow assumption was not explicitly acknowledged',
        )

    finite_bounds = [
        _extract_number(task.get('response_time_bound'))
        for task in tasks
        if isinstance(task, dict)
        and bool(task.get('proven_under_assumptions', False))
    ]
    finite_bounds = [bound for bound in finite_bounds if bound is not None]

    return {
        'rta_version': RTA_VERSION,
        'rta_status': (
            'proven_under_assumptions' if proven else 'rta_unproven'
        ),
        'rta_proven_under_assumptions': proven,
        'rta_conditional': bool(payload.get('conditional', True)),
        'rta_assumptions': list(payload.get('assumptions', [])),
        'rta_unproven_tasks': unproven_tasks,
        'rta_failure_reasons': failure_reasons,
        'rta_error': None,
        'rta_report': payload,
        'rta_bound': max(finite_bounds) if finite_bounds else None,
    }


def run_asap_block_rta(algorithm, system_config, task_file, horizon_ms,
                       assume_no_overflow=False, timeout=300,
                       initial_energy=0.0, profile_rta=False,
                       rta_tool=None, rta_snapshot=None):
    """Run the offline checker only for ASAP-BLOCK."""
    if algorithm != ASAP_BLOCK_ALGORITHM:
        return _base_rta_result(status='not_applicable')

    result = _base_rta_result(status='rta_error')
    result.update({
        'rta_enabled': True,
        'rta_version': RTA_VERSION,
        'rta_horizon_ms': horizon_ms,
        'rta_initial_energy': float(initial_energy),
        'rta_system_config': str(Path(system_config).resolve()),
    })

    try:
        result['rta_system_config_hash'] = hash_file(system_config)
        if horizon_ms is None or int(horizon_ms) <= 0:
            raise ValueError('RTA horizon must be explicitly positive')

        tool = Path(rta_tool or RTA_TOOL)
        if rta_snapshot and not verify_snapshot(
                tool, rta_snapshot['snapshot_sha256'],
                rta_snapshot['snapshot_size']):
            raise ValueError('RTA code snapshot changed before execution')

        cmd = [
            'python3',
            str(tool),
            '--system', str(system_config),
            '--tasks', str(task_file),
            '--horizon-ms', str(horizon_ms),
            '--rta-initial-energy', str(initial_energy),
        ]
        if assume_no_overflow:
            cmd.append('--assume-no-overflow')
        if profile_rta:
            cmd.append('--profile-rta')
        cmd.append('--json')

        result.update({
            'rta_attempted': True,
            'rta_runtime_source': 'subprocess_wall_clock_perf_counter',
            'rta_timeout_sec': timeout,
            'rta_profile_enabled': bool(profile_rta),
        })
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        finally:
            # End-to-end child-process wall time. Internal per-task profile
            # time is recorded separately and is not equivalent to this value.
            result['rta_runtime_sec'] = time.perf_counter() - started
        if rta_snapshot and not verify_snapshot(
                tool, rta_snapshot['snapshot_sha256'],
                rta_snapshot['snapshot_size']):
            raise ValueError('RTA code snapshot changed during execution')
        if completed.returncode != 0:
            error_output = (completed.stderr or completed.stdout or '').strip()
            raise RuntimeError(
                'RTA exited with code {}{}'.format(
                    completed.returncode,
                    ': {}'.format(error_output) if error_output else '',
                )
            )

        result.update(parse_rta_json(completed.stdout, assume_no_overflow))
        if result['rta_profile_enabled']:
            profile_sum, profile_count = aggregate_rta_profile_task_times(
                result.get('rta_report')
            )
            result['rta_profile_task_time_sum_sec'] = profile_sum
            result['rta_profile_task_count'] = profile_count
        return result
    except subprocess.TimeoutExpired:
        result['rta_timed_out'] = True
        result['rta_error'] = 'RTA timed out after {} seconds'.format(timeout)
    except (OSError, ValueError, RuntimeError) as exc:
        result['rta_error'] = str(exc)
    return result


def validate_rta_cli_args(parser, args):
    """Reject incomplete opt-in RTA configurations before experiments start."""
    try:
        _finite_horizon(
            getattr(args, 'simulation_time', DEFAULT_SIMULATION_TIME),
            positive=True,
        )
    except InvalidHorizonMetadata:
        parser.error('--simulation-time must be positive')
    if args.enable_rta and args.rta_horizon_ms is None:
        parser.error('--rta-horizon-ms is required when --enable-rta is used')
    if args.rta_horizon_ms is not None and args.rta_horizon_ms <= 0:
        parser.error('--rta-horizon-ms must be positive')
    if args.rta_timeout <= 0:
        parser.error('--rta-timeout must be positive')
    if not math.isfinite(args.rta_initial_energy):
        parser.error('--rta-initial-energy must be finite')
    if args.rta_initial_energy < 0:
        parser.error('--rta-initial-energy must be non-negative')
    harvesting_scale = getattr(args, 'harvesting_scale', 1.0)
    if not math.isfinite(harvesting_scale) or harvesting_scale < 0:
        parser.error('--harvesting-scale must be finite and non-negative')
    soundness_mode = getattr(args, 'rta_soundness_mode', 'fail_fast')
    if soundness_mode not in {'fail_fast', 'audit'}:
        parser.error('--rta-soundness-mode must be fail_fast or audit')
    if getattr(args, 'M', None) is not None and args.M <= 0:
        parser.error('--M must be positive')
    fixed_utilization = getattr(args, 'fixed_utilization', None)
    if fixed_utilization is not None and (
        not math.isfinite(fixed_utilization)
        or fixed_utilization <= 0
        or fixed_utilization > 1
    ):
        parser.error('--fixed-utilization must satisfy 0 < U <= 1')
    min_task_util = getattr(args, 'min_task_util', 0.01)
    max_task_util = getattr(args, 'max_task_util', 0.8)
    if min_task_util < 0 or max_task_util <= 0:
        parser.error('--min-task-util/--max-task-util must be positive bounds')
    if min_task_util > max_task_util:
        parser.error('--min-task-util must be <= --max-task-util')
    if max_task_util > 1.0:
        parser.error('--max-task-util must be <= 1.0 for sequential tasks')
    actual_tolerance = getattr(
        args, 'actual_utilization_tolerance_total', None
    )
    if actual_tolerance is not None and (
        not math.isfinite(actual_tolerance) or actual_tolerance < 0
    ):
        parser.error(
            '--actual-utilization-tolerance-total must be finite and non-negative'
        )


SIMULATION_TIMEOUT_STATUSES = {
    'simulation_timeout',
    'timeout',
}

SCHEDULABILITY_FAILURE_STATUSES = {
    'rejected',
    'simulation_rejected',
    'deadline_miss',
    'deadline_missed',
    'dline_miss',
}

INFRASTRUCTURE_FAILURE_STATUSES = {
    'simulation_error',
    'build_error',
    'config_error',
    'trace_parse_error',
    'missing_binary',
    'unknown_error',
    'exception',
    'yaml_generation_failed',
    'rta_error',
}


def _normalise_simulation_status(status):
    return str(status or '').strip().lower()


def _is_simulation_timeout_status(status):
    return _normalise_simulation_status(status) in SIMULATION_TIMEOUT_STATUSES


def _is_infrastructure_failure_status(status):
    status = _normalise_simulation_status(status)
    if not status:
        return False
    if status in INFRASTRUCTURE_FAILURE_STATUSES:
        return True
    normalized = status.replace('-', '_').replace(' ', '_')
    return (
        normalized.endswith('_error')
        or 'error' in normalized
        or 'exception' in normalized
        or normalized in {'failed', 'failure', 'simulation_failed'}
    )


def _is_schedulability_failure_status(status):
    status = _normalise_simulation_status(status)
    if not status:
        return False
    normalized = status.replace('-', '_').replace(' ', '_')
    return (
        normalized in SCHEDULABILITY_FAILURE_STATUSES
        or 'dline_miss' in normalized
        or ('deadline' in normalized and 'miss' in normalized)
    )


def classify_soundness_observation(rta_schedulable, sim_schedulable,
                                   simulation_status):
    """Classify whether a simulation result is valid for RTA soundness.

    Only completed schedulability observations can produce a soundness
    violation. Timeouts and infrastructure failures are retained in CSV rows
    but explicitly excluded from the E1 violation predicate.
    """
    status = _normalise_simulation_status(simulation_status)
    if bool(sim_schedulable) or status == 'accepted':
        return {
            'soundness_valid': True,
            'soundness_excluded_reason': '',
            'soundness_violation': False,
        }
    if _is_simulation_timeout_status(status):
        return {
            'soundness_valid': False,
            'soundness_excluded_reason': 'timeout',
            'soundness_violation': False,
        }
    if _is_infrastructure_failure_status(status):
        return {
            'soundness_valid': False,
            'soundness_excluded_reason': status,
            'soundness_violation': False,
        }
    if _is_schedulability_failure_status(status):
        return {
            'soundness_valid': True,
            'soundness_excluded_reason': '',
            'soundness_violation': bool(rta_schedulable),
        }
    return {
        'soundness_valid': False,
        'soundness_excluded_reason': status or 'unknown_status',
        'soundness_violation': False,
    }


def classify_simulation_status(result):
    """Return the aggregate status bucket for a per-run result."""
    if not isinstance(result, dict):
        return 'accepted' if float(result) == 1.0 else 'rejected'

    status = _normalise_simulation_status(result.get('simulation_status'))
    if not status:
        return (
            'accepted'
            if float(result.get('acceptance_ratio', 0.0)) == 1.0
            else 'rejected'
        )

    if status == 'accepted':
        return 'accepted'
    if _is_simulation_timeout_status(status):
        return 'timeout'
    if _is_infrastructure_failure_status(status):
        return 'error'
    if _is_schedulability_failure_status(status):
        return 'rejected'
    return 'error'


def validate_formal_result_identities(records, context):
    """Fail on missing/ambiguous current provenance or duplicate rows."""
    seen = {}
    hashes_by_display_id = defaultdict(set)
    for position, original in enumerate(records):
        if not isinstance(original, dict):
            raise ValueError(
                'missing_formal_provenance: {} row {}'.format(
                    context, position
                )
            )
        row = original
        scheduler = row.get('algorithm') or row.get('scheduler')
        config_id = row.get('config_id')
        taskset_hash = row.get('taskset_hash')
        taskset_id = row.get('taskset_id')
        raw_status = _normalise_simulation_status(
            row.get('simulation_status', row.get('status'))
        )
        generation_failure = raw_status in {
            'generation_error', 'yaml_generation_failed'
        } or 'taskset generation failed' in str(row.get('reason', '')).lower()
        if not config_id or not scheduler:
            raise ValueError(
                'missing_formal_provenance: {} row {} requires '
                'config_id and scheduler'.format(context, position)
            )
        if not taskset_hash and not generation_failure:
            raise ValueError(
                'missing_formal_provenance: {} row {} requires '
                'taskset_hash'.format(context, position)
            )

        if taskset_id not in {None, ''} and taskset_hash:
            hashes_by_display_id[(str(config_id), str(taskset_id))].add(
                str(taskset_hash)
            )

        identity = str(taskset_hash) if taskset_hash else (
            'generation_error:' + str(taskset_id)
        )
        key = (str(config_id), identity, str(scheduler))
        if key not in seen:
            seen[key] = (position, row)
            continue

        prior_position, prior = seen[key]
        def formal_status(value):
            raw = _normalise_simulation_status(
                value.get('simulation_status', value.get('status'))
            )
            if raw in {'accepted', 'rejected'}:
                return raw
            if raw in SIMULATION_TIMEOUT_STATUSES:
                return 'timeout'
            if raw in {'generation_error', 'yaml_generation_failed'}:
                return 'generation_error'
            return 'error'

        prior_status = formal_status(prior)
        status = formal_status(row)
        if prior_status != status:
            kind = 'duplicate_conflicting_status'
        elif prior == row:
            kind = 'duplicate_identical_result'
        else:
            kind = 'duplicate_conflicting_metadata'
        raise DuplicateResultError(
            '{}: {} key={} rows={},{}'.format(
                kind, context, key, prior_position, position
            )
        )

    conflicting = [
        (key, sorted(hashes))
        for key, hashes in hashes_by_display_id.items()
        if len(hashes) > 1
    ]
    if conflicting:
        raise DuplicateResultError(
            'duplicate_conflicting_metadata: {} same config_id/taskset_id '
            'has multiple taskset_hash values: {}'.format(
                context, conflicting
            )
        )


def _extract_number(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return number


def _is_rta_proven(result):
    if not isinstance(result, dict):
        return False
    return result.get('rta_status') in {
        'proven_under_assumptions',
        'rta_proven',
    }


def _first_deadline_miss_details(events):
    """Return first dline_miss metadata without assuming all trace fields exist."""
    first = None
    first_time = None
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get('event_type') != 'dline_miss':
            continue
        time_value = _extract_number(event.get('time'))
        sort_time = time_value if time_value is not None else math.inf
        if first is None or sort_time < first_time:
            first = event
            first_time = sort_time
    if first is None:
        return {
            'first_missed_job_release': '',
            'first_missed_deadline': '',
        }
    return {
        'first_missed_job_release': first.get('arrival_time', ''),
        'first_missed_deadline': first.get('deadline', ''),
    }


def compute_e1_soundness_violation(rta_schedulable, sim_schedulable,
                                   simulation_status):
    """E1 violation for a valid completed schedulability failure only."""
    return bool(
        classify_soundness_observation(
            rta_schedulable, sim_schedulable, simulation_status
        )['soundness_violation']
    )


def extract_rta_bounds_by_task(rta_result):
    """Return valid proven per-task response-time bounds from an RTA result."""
    if not _is_rta_proven(rta_result):
        return {}

    report = rta_result.get('rta_report')
    if not isinstance(report, dict):
        return {}
    tasks = report.get('tasks')
    if not isinstance(tasks, list):
        return {}

    bounds = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_proven = task.get(
            'proven', task.get('proven_under_assumptions', False)
        )
        task_name = task.get('task_name')
        bound = _extract_number(task.get('response_time_bound'))
        if not task_proven or not task_name or bound is None or bound <= 0:
            continue
        bounds[str(task_name)] = bound
    return bounds


def compute_task_tightness_samples(algorithm, rta_result,
                                   max_response_by_task):
    """Pair proven per-task RTA bounds with observed maximum responses."""
    if algorithm != ASAP_BLOCK_ALGORITHM or not _is_rta_proven(rta_result):
        return []
    if not isinstance(max_response_by_task, dict):
        return []

    samples = []
    for task_name, bound in extract_rta_bounds_by_task(rta_result).items():
        response = _extract_number(max_response_by_task.get(task_name))
        if response is None or response <= 0:
            continue
        samples.append(bound / response)
    return samples


def validate_rta_soundness(algorithm, rta_result, simulation_status,
                           max_response_by_task):
    """Fail fast when a v20.4 sufficient bound contradicts simulation."""
    if algorithm != ASAP_BLOCK_ALGORITHM or not _is_rta_proven(rta_result):
        return
    classification = classify_soundness_observation(
        True,
        _normalise_simulation_status(simulation_status) == 'accepted',
        simulation_status,
    )
    if classification['soundness_violation']:
        status = _normalise_simulation_status(simulation_status)
        if status == 'rejected':
            raise RuntimeError(
                'SEVERE RTA SOUNDNESS ERROR: {} proved a taskset rejected by '
                'ASAP-BLOCK simulation'.format(RTA_VERSION)
            )
        raise RuntimeError(
            'SEVERE RTA SOUNDNESS ERROR: {} proved a taskset with '
            'ASAP-BLOCK simulation status {}'.format(
                RTA_VERSION, simulation_status
            )
        )
    if not classification['soundness_valid']:
        return
    if not isinstance(max_response_by_task, dict):
        return
    for task_name, bound in extract_rta_bounds_by_task(rta_result).items():
        observed = _extract_number(max_response_by_task.get(task_name))
        if observed is not None and observed > bound:
            raise RuntimeError(
                'SEVERE RTA SOUNDNESS ERROR: task {} observed response {} '
                'exceeds {} bound {}'.format(
                    task_name, observed, RTA_VERSION, bound
                )
            )


def tightness_for_result(algorithm, result):
    """Return legacy scalar tightness for valid ASAP-BLOCK samples only."""
    if algorithm != ASAP_BLOCK_ALGORITHM or not isinstance(result, dict):
        return None
    if not _is_rta_proven(result):
        return None

    rta_bound = _extract_number(result.get('rta_bound'))
    simulated_response = _extract_number(
        result.get('simulated_response_time')
    )
    if rta_bound is None or simulated_response is None:
        return None
    if simulated_response <= 0:
        return None
    return rta_bound / simulated_response


def tightness_values_for_result(algorithm, result):
    """Return task-level tightness samples, with legacy scalar fallback."""
    if algorithm != ASAP_BLOCK_ALGORITHM or not isinstance(result, dict):
        return []
    if not _is_rta_proven(result):
        return []

    stored_values = result.get('tightness_values')
    if isinstance(stored_values, list):
        values = []
        for value in stored_values:
            number = _extract_number(value)
            if number is not None:
                values.append(number)
        return values

    values = compute_task_tightness_samples(
        algorithm,
        result,
        result.get('max_observed_response_times'),
    )
    if values:
        return values

    legacy_value = tightness_for_result(algorithm, result)
    return [] if legacy_value is None else [legacy_value]


def run_single_simulation_worker(task):
    """执行单次仿真，并独立记录可选的ASAP-BLOCK RTA结果。"""
    (algorithm, config_file, task_file, task_idx, utilization,
     simulation_time, trace_dir) = task[:7]
    rta_options = task[7] if len(task) > 7 else {}
    keep_traces = bool(rta_options.get('keep_traces', False))
    trace_file = Path(trace_dir) / f'trace_{algorithm}_u{utilization:.2f}_{task_idx:03d}.json'

    simulator = str(
        rta_options.get('simulator_bin')
        or os.environ.get('PARTSIM_RTSIM_BIN')
        or SIMULATOR
    )
    env = os.environ.copy()
    simulator_path = Path(simulator).resolve(strict=False)
    lib_path = str(simulator_path.parent.parent / 'librtsim')
    env['LD_LIBRARY_PATH'] = lib_path + ':' + env.get('LD_LIBRARY_PATH', '')

    cmd = [
        simulator, config_file, task_file,
        str(simulation_time), '-t', str(trace_file)
    ]
    run_id = str(rta_options.get('run_id', ''))
    if run_id:
        cmd.extend(['--run-id', run_id])
    if rta_options.get('semantic_traces', False):
        cmd.append('--semantic-traces')
    taskset_identity = str(
        rta_options.get('taskset_semantic_hash', '')
    ).strip()
    if taskset_identity:
        cmd.extend(['--taskset-semantic-hash', taskset_identity])

    def cleanup_trace():
        """清理追踪文件"""
        if keep_traces:
            return
        try:
            if trace_file.exists():
                os.remove(str(trace_file))
        except Exception:
            pass

    acceptance_ratio = math.nan
    simulation_status = 'error'
    simulation_reason = 'simulation_error'
    simulation_error = None
    observed_trace_horizon = None
    expected_trace_horizon = None
    simulation_completed = None
    simulation_completion_reason = ''
    trace_schema_version = None
    trace_metadata = {}
    max_response_by_task = {}
    first_miss = {
        'first_missed_job_release': '',
        'first_missed_deadline': '',
    }
    current_trace_valid = False

    try:
        if re.fullmatch(r'[0-9a-f]{64}', taskset_identity) is None:
            raise ValueError('missing/invalid taskset semantic hash')
        expected_trace_horizon = _finite_horizon(
            simulation_time, positive=True
        )
        solar_snapshot = rta_options.get('solar_profile_provenance') or {}
        if solar_snapshot.get('snapshot_sha256') not in {None, 'not_used'}:
            if not verify_snapshot(
                    solar_snapshot.get('snapshot_path', ''),
                    solar_snapshot['snapshot_sha256'],
                    solar_snapshot['snapshot_size']):
                raise ValueError('solar snapshot changed before simulation')
        subprocess.run(cmd, check=True, capture_output=True, env=env, text=True, timeout=120)
        if solar_snapshot.get('snapshot_sha256') not in {None, 'not_used'}:
            if not verify_snapshot(
                    solar_snapshot.get('snapshot_path', ''),
                    solar_snapshot['snapshot_sha256'],
                    solar_snapshot['snapshot_size']):
                raise ValueError('solar snapshot changed during simulation')
        trace_parser = TraceParser(str(trace_file))
        trace_evaluation = trace_parser.evaluate(
            expected_sim_time=simulation_time,
            expected_algorithm=algorithm,
            expected_taskset_semantic_hash=taskset_identity,
        )
        acceptance_ratio = trace_evaluation.acceptance_ratio
        simulation_status = trace_evaluation.status
        simulation_reason = trace_evaluation.reason
        observed_trace_horizon = trace_evaluation.observed_horizon_ms
        simulation_completed = trace_evaluation.simulation_completed
        simulation_completion_reason = trace_evaluation.completion_reason
        trace_schema_version = trace_evaluation.trace_schema_version
        trace_metadata = trace_parser.metadata
        if run_id and trace_metadata.get('run_id') != run_id:
            acceptance_ratio = math.nan
            simulation_status = 'error'
            simulation_reason = 'run_id_mismatch'
            simulation_error = 'trace run_id does not match current run'
        if simulation_status in {'accepted', 'rejected'}:
            mismatch = scheduler_identity_mismatch(
                algorithm, trace_metadata
            )
            if mismatch:
                acceptance_ratio = math.nan
                simulation_status = 'error'
                simulation_reason = 'scheduler_identity_mismatch'
                simulation_error = (
                    'scheduler_identity_mismatch: {}'.format(mismatch)
                )
        current_trace_valid = simulation_status in {'accepted', 'rejected'}
        first_miss = _first_deadline_miss_details(trace_parser.events)
        if (
            algorithm == ASAP_BLOCK_ALGORITHM
            and rta_options.get('enable_rta', False)
            and trace_file.exists()
        ):
            try:
                max_response_by_task = (
                    trace_parser.get_max_response_times_by_task()
                )
            except Exception:
                max_response_by_task = {}
    except InvalidHorizonMetadata:
        simulation_status = 'error'
        simulation_reason = 'invalid_horizon_metadata'
        simulation_error = 'invalid simulation horizon metadata'
    except subprocess.TimeoutExpired:
        simulation_status = 'timeout'
        simulation_reason = 'simulation_timeout'
        simulation_error = (
            f"⏱️ 仿真超时: {algorithm}, U={utilization:.2f}, idx={task_idx}"
        )
    except subprocess.CalledProcessError as e:
        simulation_status = 'error'
        error_output = (e.stderr or e.stdout or '').strip()
        simulation_reason = (
            'invalid_task_model'
            if 'invalid_task_model' in error_output
            else 'simulator_nonzero_exit'
        )
        simulation_error = (
            f"❌ 仿真失败: {algorithm}, U={utilization:.2f}, idx={task_idx}"
        )
        if error_output:
            simulation_error = f"{simulation_error}\n{error_output}"
    except Exception as e:
        simulation_status = 'error'
        simulation_reason = 'simulation_exception'
        simulation_error = (
            f"❌ 仿真异常: {algorithm}, U={utilization:.2f}, "
            f"idx={task_idx}: {e}"
        )
    finally:
        cleanup_trace()

    if (
        rta_options.get('enable_rta', False)
        and algorithm == ASAP_BLOCK_ALGORITHM
    ):
        rta_result = run_asap_block_rta(
            algorithm=algorithm,
            system_config=config_file,
            task_file=task_file,
            horizon_ms=rta_options.get('horizon_ms'),
            assume_no_overflow=rta_options.get(
                'assume_no_overflow', False
            ),
            timeout=rta_options.get('timeout', 300),
            initial_energy=rta_options.get('initial_energy', 0.0),
            profile_rta=rta_options.get('profile_rta', False),
            rta_tool=rta_options.get('rta_tool_snapshot'),
            rta_snapshot=rta_options.get('rta_code_snapshot'),
        )
    elif rta_options.get('enable_rta', False):
        rta_result = _base_rta_result(status='not_applicable')
    else:
        rta_result = _base_rta_result(status='disabled')

    run_result = {
        'algorithm': algorithm,
        'scheduler': algorithm,
        'utilization': float(utilization),
        'task_idx': int(task_idx),
        'task_index': int(task_idx),
        'task_file': str(Path(task_file).resolve()),
        'taskset_id': rta_options.get(
            'taskset_id',
            'u{:.2f}-{:03d}'.format(utilization, task_idx),
        ),
        'seed_base': rta_options.get('seed_base'),
        'taskset_seed': rta_options.get('taskset_seed'),
        'seed': rta_options.get('taskset_seed'),
        'source_run_id': rta_options.get('source_run_id', ''),
        'run_id': run_id,
        'config_id': rta_options.get('config_id', ''),
        'config_group_id': rta_options.get('config_group_id', ''),
        'taskset_hash': rta_options.get('taskset_hash', ''),
        'taskset_semantic_hash': rta_options.get(
            'taskset_semantic_hash', rta_options.get('taskset_hash', '')
        ),
        'taskset_raw_file_hash': rta_options.get(
            'taskset_raw_file_hash', ''
        ),
        'solar_profile_sha256': (
            rta_options.get('solar_profile_provenance') or {}
        ).get('sha256', ''),
        'solar_profile_path_normalized': (
            rta_options.get('solar_profile_provenance') or {}
        ).get('path_normalized', ''),
        'solar_profile_present': (
            rta_options.get('solar_profile_provenance') or {}
        ).get('present', False),
        'solar_profile_size': (
            rta_options.get('solar_profile_provenance') or {}
        ).get('size', 0),
        'solar_source_path': (
            rta_options.get('solar_profile_provenance') or {}
        ).get('source_original_path', ''),
        'solar_source_sha256': (
            rta_options.get('solar_profile_provenance') or {}
        ).get('source_sha256', 'not_used'),
        'solar_snapshot_relative_path': (
            rta_options.get('solar_profile_provenance') or {}
        ).get('snapshot_relative_path', ''),
        'solar_snapshot_time': (
            rta_options.get('solar_profile_provenance') or {}
        ).get('source_snapshot_time', ''),
        'actual_simulator_solar_path': (
            rta_options.get('solar_profile_provenance') or {}
        ).get('actual_simulator_solar_path', ''),
        'simulation_acceptance': float(acceptance_ratio),
        'acceptance_ratio': float(acceptance_ratio),
        'simulation_status': simulation_status,
        'accepted': simulation_status == 'accepted',
        'rejected': simulation_status == 'rejected',
        'error': simulation_status == 'error',
        'timeout': simulation_status == 'timeout',
        'reason': simulation_reason,
        'simulation_error': simulation_error,
        'trace_path': (
            str(trace_file.resolve())
            if keep_traces and current_trace_valid and trace_file.exists()
            else ''
        ),
        'trace_retained': bool(
            keep_traces and current_trace_valid and trace_file.exists()
        ),
        'expected_simulation_horizon_ms': (
            expected_trace_horizon
            if expected_trace_horizon is not None else math.nan
        ),
        'observed_trace_horizon_ms': observed_trace_horizon,
        'trace_schema_version': trace_schema_version,
        'simulation_completed': simulation_completed,
        'simulation_completion_reason': simulation_completion_reason,
        'result_schema_version': RESULT_SCHEMA_VERSION,
        'rta_code_fingerprint': (
            rta_options.get('rta_code_snapshot') or {}
        ).get('combined_sha256', 'not_used'),
        'rta_code_snapshot_path': rta_options.get(
            'rta_tool_snapshot', ''
        ),
        'rta_code_snapshot_sha256': (
            rta_options.get('rta_code_snapshot') or {}
        ).get('snapshot_sha256', 'not_used'),
        'rta_code_snapshot_size': (
            rta_options.get('rta_code_snapshot') or {}
        ).get('snapshot_size', 0),
        'rta_code_source_path': (
            rta_options.get('rta_code_snapshot') or {}
        ).get('entrypoint', ''),
        'rta_code_source_sha256': (
            rta_options.get('rta_code_snapshot') or {}
        ).get('entrypoint_sha256', 'not_used'),
        'expected_configured_scheduler': algorithm,
        'expected_scheduler_display_name': ALGO_DISPLAY_NAMES.get(
            algorithm, algorithm
        ),
        'expected_scheduler_implementation': SCHEDULER_IMPLEMENTATIONS.get(
            algorithm, algorithm
        ),
        'observed_configured_scheduler': trace_metadata.get(
            'configured_scheduler', ''
        ),
        'observed_scheduler_display_name': trace_metadata.get(
            'scheduler_display_name', ''
        ),
        'observed_scheduler_implementation': trace_metadata.get(
            'scheduler_implementation', ''
        ),
        'observed_scheduler_rtti_name': trace_metadata.get(
            'scheduler_rtti_name', ''
        ),
        'configured_scheduler': trace_metadata.get(
            'configured_scheduler', ''
        ),
        'scheduler_display_name': trace_metadata.get(
            'scheduler_display_name', ''
        ),
        'scheduler_implementation': trace_metadata.get(
            'scheduler_implementation', ''
        ),
    }
    run_result.update(rta_options.get('taskset_metadata', {}))
    run_result.update(rta_result)
    run_result['simulated_response_time'] = (
        max(max_response_by_task.values()) if max_response_by_task else None
    )
    run_result.update(first_miss)
    rta_schedulable = (
        algorithm == ASAP_BLOCK_ALGORITHM and _is_rta_proven(run_result)
    )
    sim_schedulable = simulation_status == 'accepted'
    soundness = classify_soundness_observation(
        rta_schedulable,
        sim_schedulable,
        simulation_status,
    )
    run_result.update(
        {
            'rta_schedulable': rta_schedulable,
            'sim_schedulable': sim_schedulable,
            'soundness_violation': soundness['soundness_violation'],
            'soundness_valid': soundness['soundness_valid'],
            'soundness_excluded_reason': (
                soundness['soundness_excluded_reason']
            ),
        }
    )
    if rta_options.get('soundness_mode', 'fail_fast') == 'fail_fast':
        validate_rta_soundness(
            algorithm, run_result, simulation_status, max_response_by_task
        )
    tightness_values = compute_task_tightness_samples(
        algorithm, run_result, max_response_by_task
    )
    run_result.update({
        'max_observed_response_times': max_response_by_task,
        'tightness_values': tightness_values,
        'tightness_num_samples': len(tightness_values),
        'avg_tightness': (
            float(np.mean(tightness_values)) if tightness_values else None
        ),
    })
    return run_result

# 算法配置 - 9种调度器
# ASAP系列（贪婪策略）
# ALAP系列（最晚策略）
# ST系列（标准策略）
ALGORITHMS = [
    'gpfp_asap_block', 'gpfp_asap_nonblock', 'gpfp_asap_sync',
    'gpfp_alap_block', 'gpfp_alap_nonblock', 'gpfp_alap_sync',
    'gpfp_st_block', 'gpfp_st_nonblock', 'gpfp_st_sync'
]

# 算法显示名称映射
ALGO_DISPLAY_NAMES = {
    'gpfp_asap_block': 'ASAP-Block',
    'gpfp_asap_nonblock': 'ASAP-NonBlock',
    'gpfp_asap_sync': 'ASAP-Sync',
    'gpfp_alap_block': 'ALAP-Block',
    'gpfp_alap_nonblock': 'ALAP-NonBlock',
    'gpfp_alap_sync': 'ALAP-Sync',
    'gpfp_st_block': 'ST-Block',
    'gpfp_st_nonblock': 'ST-NonBlock',
    'gpfp_st_sync': 'ST-Sync'
}

# Stable run-level trace identities. These must match the checked C++
# factory mapping in librtsim/system.cpp.
SCHEDULER_IMPLEMENTATIONS = {
    'gpfp_asap_block': 'GPFPASAPBlockScheduler',
    'gpfp_asap_nonblock': 'GPFPASAPNonBlockScheduler',
    'gpfp_asap_sync': 'GPFPASAPSyncScheduler',
    'gpfp_alap_block': 'GPFPALAPBlockScheduler',
    'gpfp_alap_nonblock': 'GPFPALAPNonBlockScheduler',
    'gpfp_alap_sync': 'GPFPALAPSyncScheduler',
    'gpfp_st_block': 'GPFPSTBlockScheduler',
    'gpfp_st_nonblock': 'GPFPSTNonBlockScheduler',
    'gpfp_st_sync': 'GPFPSTSyncScheduler',
}


def _wilson_interval(successes, trials, z=1.959963984540054):
    """Taskset-level Wilson interval used by formal common-complete rows."""
    successes = int(successes)
    trials = int(trials)
    if trials == 0:
        return np.nan, np.nan
    p_hat = successes / trials
    z2 = z * z
    denominator = 1.0 + z2 / trials
    center = (p_hat + z2 / (2.0 * trials)) / denominator
    half = z / denominator * math.sqrt(
        p_hat * (1.0 - p_hat) / trials
        + z2 / (4.0 * trials * trials)
    )
    return center - half, center + half


def scheduler_identity_mismatch(algorithm, metadata):
    """Return mismatch detail, or an empty string for a valid trace."""
    expected = {
        'configured_scheduler': algorithm,
        'scheduler_display_name': ALGO_DISPLAY_NAMES.get(algorithm, algorithm),
        'scheduler_implementation': SCHEDULER_IMPLEMENTATIONS.get(
            algorithm, algorithm
        ),
    }
    metadata = metadata if isinstance(metadata, dict) else {}
    mismatches = [
        '{}={!r} expected {!r}'.format(
            key, metadata.get(key), expected_value
        )
        for key, expected_value in expected.items()
        if metadata.get(key) != expected_value
    ]
    return '; '.join(mismatches)

# 算法分类（用于图表分组）
ALGO_GROUPS = {
    'asap': ['gpfp_asap_block', 'gpfp_asap_nonblock', 'gpfp_asap_sync'],
    'alap': ['gpfp_alap_block', 'gpfp_alap_nonblock', 'gpfp_alap_sync'],
    'st': ['gpfp_st_block', 'gpfp_st_nonblock', 'gpfp_st_sync'],
    'block': ['gpfp_asap_block', 'gpfp_alap_block', 'gpfp_st_block'],
    'nonblock': ['gpfp_asap_nonblock', 'gpfp_alap_nonblock', 'gpfp_st_nonblock'],
    'sync': ['gpfp_asap_sync', 'gpfp_alap_sync', 'gpfp_st_sync']
}

# 图表显示名称
GROUP_DISPLAY_NAMES = {
    'asap': 'ASAP系列',
    'alap': 'ALAP系列',
    'st': 'ST系列',
    'block': 'Block系列',
    'nonblock': 'NonBlock系列',
    'sync': 'Sync系列'
}

# 算法样式配置（颜色和标记）
ALGO_STYLES = {
    # ASAP系列 - 蓝色系
    'gpfp_asap_block': {'color': '#1f77b4', 'marker': 'o', 'linestyle': '-'},
    'gpfp_asap_nonblock': {'color': '#1f77b4', 'marker': 's', 'linestyle': '--'},
    'gpfp_asap_sync': {'color': '#1f77b4', 'marker': '^', 'linestyle': ':'},
    # ALAP系列 - 绿色系
    'gpfp_alap_block': {'color': '#2ca02c', 'marker': 'o', 'linestyle': '-'},
    'gpfp_alap_nonblock': {'color': '#2ca02c', 'marker': 's', 'linestyle': '--'},
    'gpfp_alap_sync': {'color': '#2ca02c', 'marker': '^', 'linestyle': ':'},
    # ST系列 - 红色系
    'gpfp_st_block': {'color': '#d62728', 'marker': 'o', 'linestyle': '-'},
    'gpfp_st_nonblock': {'color': '#d62728', 'marker': 's', 'linestyle': '--'},
    'gpfp_st_sync': {'color': '#d62728', 'marker': '^', 'linestyle': ':'}
}

# 实验常数（可通过命令行修改）
DEFAULT_UTILIZATION_POINTS = np.around(np.linspace(0.1, 1.0, 10), 2)  # 四舍五入避免浮点精度问题
DEFAULT_NUM_TASKSETS = 50  # 每个利用率点50个任务集
DEFAULT_TASK_N = 10  # 每个任务集10个任务
DEFAULT_TASK_P_MIN = 40  # 周期范围：最小40ms
DEFAULT_TASK_P_MAX = 400  # 周期范围：最大400ms（增加多样性）
DEFAULT_SIMULATION_TIME = 30000  # 30秒仿真
DEFAULT_BATTERY_CAPACITY = 20.0  # 20J电池
DEFAULT_INITIAL_ENERGY_RATIO = 1.0  # 100%初始能量（满电）
DEFAULT_SOLAR_START_TIME_MS = 21975000  # 太阳能起始时间（毫秒）
DEFAULT_USE_REAL_SOLAR_DATA = False  # 使用分段函数模拟，不使用真实太阳能数据
DEFAULT_HARVESTING_SCALE = 1.0  # synthetic_piecewise 供能强度倍率
DEFAULT_MAX_WORKERS = max(1, min(12, cpu_count() - 2))
DEFAULT_SEED_BASE = 2000


def get_git_short_commit():
    """Return the current short commit for reproducible output naming."""
    try:
        completed = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def build_default_output_dir():
    """Build a run-specific output directory to avoid silent overwrites."""
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    commit = get_git_short_commit()
    return f'acceptance_ratio_runs/run-{timestamp}-{commit}'


def add_experiment_cli_args(parser):
    """Register experiment CLI flags in one testable place."""
    # 实验控制
    parser.add_argument('--run-experiment', action='store_true',
                       help='运行实验生成新数据')
    parser.add_argument('--csv', type=str, default=None,
                       help='从CSV文件加载数据（不运行实验）')
    parser.add_argument(
        '--allow-unattested-diagnostic-input', action='store_true',
        help='仅诊断：读取未认证CSV并隔离所有输出',
    )

    # 实验参数
    parser.add_argument('--output-dir', type=str,
                       default=build_default_output_dir(),
                       help='输出目录（默认自动生成唯一run目录）')
    parser.add_argument('--overwrite', action='store_true',
                       help='允许覆盖已有输出目录中的正式实验结果')
    parser.add_argument(
        '--require-common-complete',
        action='store_true',
        help=(
            '正式模式：任一实验点不是九算法共同完整样本集时，'
            '保留诊断输出但以非零状态结束'
        ),
    )
    parser.add_argument('--seed-base', type=int, default=DEFAULT_SEED_BASE,
                       help=f'任务集随机种子基数 (默认: {DEFAULT_SEED_BASE})')
    parser.add_argument('--num-points', type=int, default=10,
                       help='利用率采样点数 (默认: 10)')
    parser.add_argument(
        '--fixed-utilization',
        type=float,
        default=None,
        help='只运行一个指定的normalized utilization点，范围为0 < U <= 1',
    )
    parser.add_argument('--num-tasksets', type=int, default=DEFAULT_NUM_TASKSETS,
                       help=f'每个利用率点的任务集数量 (默认: {DEFAULT_NUM_TASKSETS})')
    parser.add_argument('--task-n', type=int, default=DEFAULT_TASK_N,
                       help=f'每个任务集的任务数 (默认: {DEFAULT_TASK_N})')
    parser.add_argument(
        '--simulation-time', type=int, default=DEFAULT_SIMULATION_TIME,
        help=(
            '每次仿真的真实时域，单位ms '
            f'(默认: {DEFAULT_SIMULATION_TIME})'
        ),
    )
    parser.add_argument(
        '--M', type=int, default=None,
        help=(
            '处理器核心数；默认读取 system_config_unified_template.yml，'
            '显式传入时同时覆盖生成器-c和临时系统配置numcpus'
        ),
    )
    parser.add_argument('--min-task-util', type=float, default=0.01,
                       help='UUniFast-Discard单任务最小利用率')
    parser.add_argument('--max-task-util', type=float, default=0.8,
                       help='UUniFast-Discard单任务最大利用率')
    parser.add_argument(
        '--wcet-rounding',
        choices=('floor', 'round', 'ceil', 'compensated'),
        default='floor',
        help='由u_i*T_i整数化生成runtime时使用的取整方式',
    )
    parser.add_argument(
        '--actual-utilization-tolerance-total',
        type=float,
        default=None,
        help=(
            '生成后允许的总利用率绝对误差；显式设置时超出门限则丢弃'
            '整组任务并重试'
        ),
    )
    parser.add_argument(
        '--constrained-deadlines',
        action='store_true',
        help='生成约束截止时间 C_i<=D_i<=T_i；默认隐式截止时间 D_i=T_i',
    )
    parser.add_argument('--battery', type=float, default=DEFAULT_BATTERY_CAPACITY,
                       help=f'电池容量 (Joules) (默认: {DEFAULT_BATTERY_CAPACITY})')
    parser.add_argument('--initial-energy', type=float, default=DEFAULT_INITIAL_ENERGY_RATIO,
                       help=f'初始能量比例 (0.0-1.0) (默认: {DEFAULT_INITIAL_ENERGY_RATIO})')
    parser.add_argument('--solar-time-ms', type=int, default=DEFAULT_SOLAR_START_TIME_MS,
                       help=f'太阳能收集开始时间（毫秒）(默认: {DEFAULT_SOLAR_START_TIME_MS})')
    parser.add_argument(
        '--harvesting-scale', type=float, default=DEFAULT_HARVESTING_SCALE,
        help=(
            'synthetic_piecewise 收集率/供能倍率，默认1.0；不改变电池容量、'
            '初始能量、任务能耗、任务时序或调度语义'
        ),
    )
    parser.add_argument('--max-workers', type=int, default=DEFAULT_MAX_WORKERS,
                       help=f'并发线程数 (默认: {DEFAULT_MAX_WORKERS})')
    parser.add_argument(
        '--keep-traces',
        action='store_true',
        default=False,
        help='保留每次仿真生成的JSON trace文件；默认解析后删除worker trace',
    )
    parser.add_argument(
        '--semantic-traces',
        action='store_true',
        default=False,
        help=(
            '在JSON trace中记录只读scheduler decision语义事件；默认关闭，'
            '不改变调度选择'
        ),
    )
    parser.add_argument('--enable-rta', action='store_true',
                       help='仅为 ASAP-BLOCK 启用离线RTA观察指标')
    parser.add_argument('--rta-horizon-ms', type=int, default=None,
                       help='RTA harvesting服务曲线分析时域（启用RTA时必填）')
    parser.add_argument('--rta-assume-no-overflow', action='store_true',
                       help='显式确认RTA的电池不溢出条件假设')
    parser.add_argument('--rta-timeout', type=int, default=300,
                       help='单次RTA超时时间（秒，默认: 300）')
    parser.add_argument(
        '--rta-initial-energy', type=float, default=0.0,
        help=(
            '每个RTA分析窗口起点（目标作业释放时刻）可保证的绝对能量'
            '下界E0，单位J，默认0.0；不是电池比例，也不继承仿真的'
            '--initial-energy。仿真--initial-energy 1.0表示满电比例，'
            'RTA --rta-initial-energy 1.0表示1J。只有能证明每次目标作业'
            '释放时均有该能量时，非零E0才支持正式理论保证；否则仅是'
            '诊断或特定实验假设'
        ),
    )
    parser.add_argument(
        '--profile-rta', action='store_true',
        help='在ASAP-BLOCK RTA JSON中记录性能计数（默认关闭）',
    )
    parser.add_argument(
        '--rta-soundness-mode',
        choices=('fail_fast', 'audit'),
        default='fail_fast',
        help=(
            'RTA soundness冲突处理：fail_fast保持默认立即失败；'
            'audit保留CSV行并记录soundness_violation'
        ),
    )

    # 图表参数
    parser.add_argument('--figure-output', type=str, default=None,
                       help='综合图表输出文件名（可选，默认生成6张分组图表）')
    parser.add_argument('--x-label', type=str, default=None,
                       help='自定义X轴标签')
    parser.add_argument('--no-group-figures', action='store_true',
                       help='不生成分组图表，只生成综合图表')


def validate_output_dir_args(parser, args):
    """Reject accidental reuse of a populated output directory."""
    output_dir = Path(args.output_dir)
    if getattr(args, 'overwrite', False):
        return
    if output_dir.exists() and any(output_dir.iterdir()):
        parser.error(
            'output directory already exists and is not empty; '
            'choose a new --output-dir or pass --overwrite'
        )

# ============================================
# 追踪文件解析器
# ============================================
@dataclass(frozen=True)
class TraceEvaluation:
    status: str
    reason: str
    acceptance_ratio: float
    expected_horizon_ms: float
    observed_horizon_ms: object
    simulation_completed: object = None
    completion_reason: str = ''
    trace_schema_version: object = None


class InvalidHorizonMetadata(ValueError):
    pass


def _finite_horizon(value, *, positive):
    if isinstance(value, (bool, np.bool_)) or value is None:
        raise InvalidHorizonMetadata('horizon must be a real number')
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidHorizonMetadata('horizon must be numeric') from exc
    if not math.isfinite(number):
        raise InvalidHorizonMetadata('horizon must be finite')
    if positive and number <= 0:
        raise InvalidHorizonMetadata('expected horizon must be positive')
    if not positive and number < 0:
        raise InvalidHorizonMetadata('observed horizon must be non-negative')
    return number


class TraceParser:
    """解析仿真追踪文件，提取性能指标（二元可调度性）"""

    def __init__(self, trace_file: str, allow_legacy=False):
        self.trace_file = trace_file
        self.allow_legacy = bool(allow_legacy)
        self.data = {}
        self.events = []
        self.metadata = {}
        self.load_error = None
        self._load_data()

    def _load_data(self):
        """加载JSON追踪文件"""
        def reject_duplicate_keys(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError('duplicate JSON key: {}'.format(key))
                value[key] = item
            return value

        try:
            with open(self.trace_file, 'r', encoding='utf-8') as f:
                data = json.load(f, object_pairs_hook=reject_duplicate_keys)
        except FileNotFoundError:
            self.load_error = 'missing_trace'
            return
        except (json.JSONDecodeError, ValueError):
            self.load_error = 'malformed_trace'
            return
        except Exception:
            self.load_error = 'trace_load_error'
            return

        if not isinstance(data, dict):
            self.load_error = 'malformed_trace'
            return
        events = data.get('events')
        if not isinstance(events, list):
            self.load_error = 'malformed_trace'
            return

        self.data = data
        self.events = events
        self.metadata = {
            key: data.get(key, '')
            for key in (
                'configured_scheduler',
                'scheduler_display_name',
                'scheduler_implementation',
                'scheduler_rtti_name',
                'run_id',
                'taskset_semantic_hash',
                'trace_schema_version',
                'run_count',
                'target_run_generation',
                'expected_simulation_horizon_ms',
                'observed_simulation_end_ms',
                'simulation_completed',
                'simulation_completion_reason',
            )
        }

    def evaluate(self, expected_sim_time=30000, expected_algorithm=None,
                 expected_taskset_semantic_hash=None):
        """
        Return a structured schedulability observation.

        Only accepted/rejected observations are valid acceptance samples.
        Missing, malformed, empty, or truncated traces are infrastructure
        errors and must not enter the conditional acceptance denominator.
        """
        try:
            expected = _finite_horizon(expected_sim_time, positive=True)
        except InvalidHorizonMetadata:
            return TraceEvaluation(
                'error', 'invalid_horizon_metadata', math.nan, math.nan, None
            )
        if self.load_error:
            return TraceEvaluation(
                'error', self.load_error, math.nan, expected, None
            )
        if not self.events:
            return TraceEvaluation(
                'error', 'empty_trace', math.nan, expected, None
            )

        schema_version = self.data.get('trace_schema_version')
        if schema_version != TRACE_SCHEMA_VERSION:
            if not self.allow_legacy:
                return TraceEvaluation(
                    'error', 'unsupported_trace_schema', math.nan,
                    expected, None, trace_schema_version=schema_version
                )
            warnings.warn(
                'legacy trace schema accepted explicitly; completion metadata '
                'cannot be trusted for formal results',
                RuntimeWarning,
                stacklevel=2,
            )
        elif expected_algorithm:
            mismatch = scheduler_identity_mismatch(
                expected_algorithm, self.metadata
            )
            if mismatch:
                return TraceEvaluation(
                    'error', 'scheduler_identity_mismatch', math.nan,
                    expected, None, trace_schema_version=schema_version
                )

        if schema_version == TRACE_SCHEMA_VERSION:
            run_id = self.data.get('run_id')
            if not isinstance(run_id, str) or not run_id:
                return TraceEvaluation(
                    'error', 'missing_run_id', math.nan,
                    expected, None, trace_schema_version=schema_version
                )
            semantic_hash = self.data.get('taskset_semantic_hash')
            if (not isinstance(semantic_hash, str)
                    or re.fullmatch(r'[0-9a-f]{64}', semantic_hash) is None
                    or (expected_taskset_semantic_hash is not None
                        and semantic_hash != expected_taskset_semantic_hash)):
                return TraceEvaluation(
                    'error', 'taskset_semantic_hash_mismatch', math.nan,
                    expected, None, trace_schema_version=schema_version
                )
            generations = set()
            for event in self.events:
                if not isinstance(event, dict):
                    return TraceEvaluation(
                        'error', 'malformed_event_time', math.nan,
                        expected, None, trace_schema_version=schema_version
                    )
                if 'run_generation' not in event:
                    return TraceEvaluation(
                        'error', 'missing_run_generation', math.nan,
                        expected, None, trace_schema_version=schema_version
                    )
                generation = event.get('run_generation')
                if type(generation) is not int or generation <= 0:
                    return TraceEvaluation(
                        'error', 'invalid_run_generation', math.nan,
                        expected, None, trace_schema_version=schema_version
                    )
                generations.add(generation)

            run_count = self.data.get('run_count')
            target_generation = self.data.get('target_run_generation')
            top_level_generation = self.data.get('run_generation')
            if type(run_count) is not int or run_count <= 0:
                return TraceEvaluation(
                    'error', 'invalid_run_count', math.nan,
                    expected, None, trace_schema_version=schema_version
                )
            if run_count > 1:
                return TraceEvaluation(
                    'error', 'multiple_simulation_runs_not_supported',
                    math.nan, expected, None,
                    trace_schema_version=schema_version
                )
            if len(generations) != 1:
                return TraceEvaluation(
                    'error', 'multiple_simulation_runs_not_supported',
                    math.nan, expected, None,
                    trace_schema_version=schema_version
                )
            if type(target_generation) is not int or target_generation <= 0 \
                    or target_generation not in generations:
                return TraceEvaluation(
                    'error', 'run_generation_mismatch', math.nan,
                    expected, None, trace_schema_version=schema_version
                )
            if type(top_level_generation) is not int or (
                    top_level_generation != target_generation):
                return TraceEvaluation(
                    'error', 'run_generation_mismatch', math.nan,
                    expected, None, trace_schema_version=schema_version
                )

        try:
            event_times = []
            for event in self.events:
                if not isinstance(event, dict):
                    raise TypeError('event is not an object')
                event_times.append(
                    _finite_horizon(event.get('time'), positive=False)
                )
        except (InvalidHorizonMetadata, TypeError, ValueError):
            return TraceEvaluation(
                'error', 'malformed_event_time', math.nan, expected, None
            )

        if schema_version != TRACE_SCHEMA_VERSION:
            observed = max(event_times)
            trace_end = self.data.get('observed_simulation_end_ms')
            if trace_end is not None:
                try:
                    observed = _finite_horizon(trace_end, positive=False)
                except InvalidHorizonMetadata:
                    return TraceEvaluation(
                        'error', 'invalid_horizon_metadata', math.nan,
                        expected, None, trace_schema_version=schema_version
                    )
            has_arrivals = any(
                event.get('event_type') == 'arrival' for event in self.events
            )
            if not has_arrivals:
                return TraceEvaluation(
                    'error', 'no_arrivals', math.nan, expected, observed,
                    trace_schema_version=schema_version
                )
            if any(
                event.get('event_type') == 'dline_miss'
                for event in self.events
            ):
                return TraceEvaluation(
                    'rejected', 'deadline_miss', 0.0, expected, observed,
                    trace_schema_version=schema_version
                )
            if observed < expected * 0.95:
                return TraceEvaluation(
                    'error', 'incomplete_trace', math.nan, expected, observed,
                    trace_schema_version=schema_version
                )
            return TraceEvaluation(
                'accepted', 'accepted', 1.0, expected, observed,
                trace_schema_version=schema_version
            )

        try:
            metadata_expected = _finite_horizon(
                self.data.get('expected_simulation_horizon_ms'), positive=True
            )
            observed = _finite_horizon(
                self.data.get('observed_simulation_end_ms'), positive=False
            )
        except InvalidHorizonMetadata:
            return TraceEvaluation(
                'error', 'invalid_horizon_metadata', math.nan,
                expected, None, trace_schema_version=schema_version
            )

        if not math.isclose(metadata_expected, expected, rel_tol=0.0, abs_tol=1e-9):
            return TraceEvaluation(
                'error', 'expected_horizon_mismatch', math.nan,
                expected, observed, trace_schema_version=schema_version
            )

        completed = self.data.get('simulation_completed')
        completion_reason = self.data.get('simulation_completion_reason')
        if type(completed) is not bool or not isinstance(completion_reason, str) \
                or not completion_reason:
            return TraceEvaluation(
                'error', 'invalid_completion_metadata', math.nan,
                expected, observed, trace_schema_version=schema_version
            )

        if observed + 1e-9 < max(event_times):
            return TraceEvaluation(
                'error', 'invalid_completion_metadata', math.nan,
                expected, observed, completed, completion_reason,
                schema_version
            )

        # Schema-v2 formal simulations are complete-horizon observations.
        # Validate the entire completion tuple before inspecting deadline
        # misses so a damaged or contradictory trace can never enter the
        # accepted/rejected denominator.
        if not completed or completion_reason != 'reached_horizon':
            return TraceEvaluation(
                'error', 'invalid_completion_metadata', math.nan,
                expected, observed, completed, completion_reason,
                schema_version
            )
        if not math.isclose(observed, expected, rel_tol=0.0, abs_tol=1e-9):
            return TraceEvaluation(
                'error', 'invalid_completion_metadata', math.nan,
                expected, observed, completed, completion_reason,
                schema_version
            )

        has_arrivals = any(
            event.get('event_type') == 'arrival'
            for event in self.events
        )
        if not has_arrivals:
            return TraceEvaluation(
                'error', 'no_arrivals', math.nan, expected, observed
            )
        deadline_misses = [
            event for event in self.events
            if event.get('event_type') == 'dline_miss'
        ]
        seen_jobs = set()
        for event in deadline_misses:
            job_id = event.get('job_id')
            try:
                release = _finite_horizon(
                    event.get('arrival_time'), positive=False
                )
                deadline = _finite_horizon(
                    event.get('deadline'), positive=False
                )
                event_time = _finite_horizon(
                    event.get('time'), positive=False
                )
                remaining = _finite_horizon(
                    event.get('remaining_execution_ms'), positive=False
                )
            except InvalidHorizonMetadata:
                return TraceEvaluation(
                    'error', 'malformed_deadline_miss', math.nan,
                    expected, observed, completed, completion_reason,
                    schema_version
                )
            if (
                not isinstance(job_id, str) or not job_id
                or not isinstance(event.get('task_name'), str)
                or not event.get('task_name')
                or job_id in seen_jobs
                or release > deadline
                or event_time < deadline
                or remaining <= 0
            ):
                return TraceEvaluation(
                    'error', 'malformed_deadline_miss', math.nan,
                    expected, observed, completed, completion_reason,
                    schema_version
                )
            seen_jobs.add(job_id)

        if deadline_misses:
            return TraceEvaluation(
                'rejected', 'deadline_miss', 0.0, expected, observed,
                completed, completion_reason, schema_version
            )
        return TraceEvaluation(
            'accepted', 'accepted', 1.0, expected, observed,
            completed, completion_reason, schema_version
        )

    def get_acceptance_ratio(self, expected_sim_time=30000):
        """Backward-compatible scalar view; invalid traces return NaN."""
        return self.evaluate(expected_sim_time).acceptance_ratio

    def get_max_response_times_by_task(self):
        """Return the maximum completed-job response time for each task."""
        max_response_by_task = {}
        for event in self.events:
            if not isinstance(event, dict):
                continue
            if event.get('event_type') not in {
                'end_instance', 'completion'
            }:
                continue

            task_name = event.get('task_name')
            finish_time = _extract_number(event.get('time'))
            arrival_time = _extract_number(event.get('arrival_time'))
            if not task_name or finish_time is None or arrival_time is None:
                continue

            response_time = finish_time - arrival_time
            if response_time < 0:
                continue
            task_name = str(task_name)
            previous = max_response_by_task.get(task_name)
            if previous is None or response_time > previous:
                max_response_by_task[task_name] = response_time
        return max_response_by_task

# ============================================
# 实验执行器
# ============================================
class ExperimentRunner:
    """运行接受率实验"""

    def __init__(self, output_dir, utilization_points, num_tasksets,
                 task_n, task_p_min, task_p_max, simulation_time,
                 battery_capacity, initial_energy_ratio, solar_start_time_ms,
                 use_real_solar_data=True, system_cores=None,
                 max_workers=DEFAULT_MAX_WORKERS, enable_rta=False,
                 rta_horizon_ms=None, rta_assume_no_overflow=False,
                 rta_timeout=300, seed_base=DEFAULT_SEED_BASE,
                 rta_initial_energy=0.0, profile_rta=False,
                 harvesting_scale=DEFAULT_HARVESTING_SCALE,
                 rta_soundness_mode='fail_fast',
                 task_util_min=0.01, task_util_max=0.8,
                 wcet_rounding='floor', constrained_deadlines=False,
                 actual_utilization_tolerance_total=None,
                 keep_traces=False, semantic_traces=False,
                 require_common_complete=False):
        self.output_dir = Path(output_dir)
        self.trace_dir = self.output_dir / 'traces'
        self.task_dir = self.output_dir / 'tasks'

        # 创建目录
        for p in [self.output_dir, self.trace_dir, self.task_dir]:
            p.mkdir(parents=True, exist_ok=True)
        self.run_id = str(uuid.uuid4())

        # 实验参数
        self.utilization_points = utilization_points
        self.num_tasksets = num_tasksets
        self.task_n = task_n
        self.task_p_min = task_p_min
        self.task_p_max = task_p_max
        self.simulation_time = _finite_horizon(
            simulation_time, positive=True
        )
        self.battery_capacity = battery_capacity
        self.initial_energy_ratio = initial_energy_ratio
        self.solar_start_time_ms = solar_start_time_ms
        self.use_real_solar_data = use_real_solar_data
        self.harvesting_scale = float(harvesting_scale)
        if (
            not math.isfinite(self.harvesting_scale)
            or self.harvesting_scale < 0
        ):
            raise ValueError('harvesting_scale must be finite and non-negative')
        self.system_cores = system_cores if system_cores is not None else get_system_cores(CONFIG_TEMPLATE)
        self.max_workers = max(1, max_workers)
        self.enable_rta = bool(enable_rta)
        self.rta_horizon_ms = rta_horizon_ms
        self.rta_assume_no_overflow = bool(rta_assume_no_overflow)
        self.rta_timeout = max(1, int(rta_timeout))
        self.rta_initial_energy = float(rta_initial_energy)
        self.profile_rta = bool(profile_rta)
        self.keep_traces = bool(keep_traces)
        self.semantic_traces = bool(semantic_traces)
        self.require_common_complete = bool(require_common_complete)
        self.task_util_min = float(task_util_min)
        self.task_util_max = float(task_util_max)
        if wcet_rounding not in {'floor', 'round', 'ceil', 'compensated'}:
            raise ValueError('wcet_rounding must be floor, round, ceil, or compensated')
        self.wcet_rounding = wcet_rounding
        self.constrained_deadlines = bool(constrained_deadlines)
        if actual_utilization_tolerance_total is None:
            self.actual_utilization_tolerance_total = None
        else:
            self.actual_utilization_tolerance_total = float(
                actual_utilization_tolerance_total
            )
            if (
                not math.isfinite(self.actual_utilization_tolerance_total)
                or self.actual_utilization_tolerance_total < 0
            ):
                raise ValueError(
                    'actual_utilization_tolerance_total must be finite and non-negative'
                )
        if rta_soundness_mode not in {'fail_fast', 'audit'}:
            raise ValueError('rta_soundness_mode must be fail_fast or audit')
        self.rta_soundness_mode = rta_soundness_mode
        self.seed_base = int(seed_base)
        self.solar_snapshot = create_solar_snapshot(
            CONFIG_TEMPLATE, self.output_dir, self.use_real_solar_data
        )
        self.rta_code_snapshot = create_rta_code_snapshot(
            self.output_dir, self.enable_rta, RTA_TOOL
        )
        self.rta_results_file = self.output_dir / 'rta_results.jsonl'
        self.per_taskset_results_file = (
            self.output_dir / 'per_taskset_results.csv'
        )
        self.experiment_id = self.output_dir.name

        print(f"🖥️  系统核心数: {self.system_cores}")
        print(
            "🎲 任务生成: min_u={}, max_u={}, wcet_rounding={}, "
            "deadline_mode={}, actual_utilization_tolerance_total={}".format(
                self.task_util_min,
                self.task_util_max,
                self.wcet_rounding,
                'constrained' if self.constrained_deadlines else 'implicit',
                (
                    ''
                    if self.actual_utilization_tolerance_total is None
                    else self.actual_utilization_tolerance_total
                ),
            )
        )
        print(f"📁 输出目录: {self.output_dir}")
        print(f"⚙️  并发进程数: {self.max_workers}")
        if self.enable_rta:
            print(
                "🔎 ASAP-BLOCK RTA: enabled, horizon={}ms, "
                "assume_no_overflow={}, timeout={}s, E0={}J, "
                "profile={}, soundness_mode={}".format(
                    self.rta_horizon_ms,
                    self.rta_assume_no_overflow,
                    self.rta_timeout,
                    self.rta_initial_energy,
                    self.profile_rta,
                    self.rta_soundness_mode,
                )
            )

    def taskset_seed(self, utilization, task_idx):
        return (
            self.seed_base
            + int(round(utilization * 100)) * 100
            + int(task_idx)
        )

    def taskset_id(self, utilization, task_idx):
        return 'u{:.2f}-{:03d}'.format(utilization, int(task_idx))

    def harvesting_profile(self):
        return (
            'real_solar'
            if self.use_real_solar_data
            else 'synthetic_piecewise'
        )

    def canonical_experiment_config(self, utilization, *, include_seed=True):
        """Return every task-generation/simulation input affecting a point."""
        solar = {
            key: self.solar_snapshot.get(key)
            for key in (
                'enabled', 'mode', 'source_sha256', 'snapshot_sha256',
                'snapshot_size', 'pv_efficiency', 'pv_area_m2',
                'periodic_collection_interval_ms',
            )
        }
        config = {
            'experiment_name': 'partsim_acceptance',
            'experiment_version': RESULT_SCHEMA_VERSION,
            'trace_schema_version': TRACE_SCHEMA_VERSION,
            'num_cores': self.system_cores,
            'num_tasks': self.task_n,
            'num_tasksets': self.num_tasksets,
            'normalized_utilization': float(utilization),
            'battery_capacity': self.battery_capacity,
            'initial_energy': (
                self.battery_capacity * self.initial_energy_ratio
            ),
            'initial_energy_ratio': self.initial_energy_ratio,
            'harvesting_profile': self.harvesting_profile(),
            'harvesting_scale': self.harvesting_scale,
            'solar_start_time_ms': self.solar_start_time_ms,
            'simulation_horizon_ms': self.simulation_time,
            'deadline_mode': (
                'constrained' if self.constrained_deadlines else 'implicit'
            ),
            'wcet_rounding': self.wcet_rounding,
            'period_min_ms': self.task_p_min,
            'period_max_ms': self.task_p_max,
            'task_util_min': self.task_util_min,
            'task_util_max': self.task_util_max,
            'actual_utilization_tolerance_total': (
                self.actual_utilization_tolerance_total
            ),
            'use_real_solar_data': self.use_real_solar_data,
            'solar_profile': solar,
            'scheduler_family': sorted(ALGORITHMS),
            'semantic_traces': self.semantic_traces,
            'system_template_sha256': (
                taskset_file_hash(CONFIG_TEMPLATE)
                if Path(CONFIG_TEMPLATE).is_file() else 'missing'
            ),
            'task_generator_sha256': (
                taskset_file_hash(TASK_GENERATOR)
                if Path(TASK_GENERATOR).is_file() else 'missing'
            ),
            'rta': {
                'enabled': self.enable_rta,
                'version': RTA_VERSION,
                'horizon_ms': self.rta_horizon_ms,
                'assume_no_overflow': self.rta_assume_no_overflow,
                'initial_energy': self.rta_initial_energy,
                'timeout_seconds': self.rta_timeout,
                'profile_enabled': self.profile_rta,
                'soundness_mode': self.rta_soundness_mode,
                'code_fingerprint': (
                    {
                        key: self.rta_code_snapshot.get(key)
                        for key in (
                            'mode', 'entrypoint', 'entrypoint_size',
                            'entrypoint_sha256', 'dependency_mode',
                            'local_dependency_files', 'combined_sha256',
                        )
                    }
                    if self.enable_rta else rta_code_fingerprint(False)
                ),
            },
        }
        if include_seed:
            config['seed_base'] = self.seed_base
        return config

    def config_id(self, utilization):
        return stable_config_id(
            self.canonical_experiment_config(utilization, include_seed=True)
        )

    def config_group_id(self, utilization):
        """Identity for compatible multi-seed pooling (seed excluded)."""
        config = self.canonical_experiment_config(
            utilization, include_seed=False
        )
        # Sample-count design changes weighting/requested totals, not the
        # underlying workload/simulation point. Counts remain explicit and
        # may therefore be pooled even when seed batches have unequal sizes.
        config.pop('num_tasksets', None)
        return stable_config_id(config)

    def generate_taskset(self, utilization, task_idx, seed=None,
                         system_config_file=None):
        """生成指定利用率的任务集"""
        task_file = self.task_dir / f'taskset_u{utilization:.2f}_{task_idx:03d}.yml'
        if system_config_file is None:
            system_config_file = CONFIG_TEMPLATE
        if seed is None:
            seed = self.taskset_seed(utilization, task_idx)

        # 计算总利用率（归一化利用率 × 核心数）
        total_utilization = utilization * self.system_cores

        # 修复：格式化为4位小数，防止浮点精度问题
        utilization_str = f"{total_utilization:.4f}"

        cmd = [
            'python3', TASK_GENERATOR,
            '-n', str(self.task_n),
            '-u', utilization_str,  # 使用格式化后的字符串
            '-p', str(self.task_p_min),
            '-P', str(self.task_p_max),
            '-c', str(self.system_cores),
            '--seed', str(seed),
            '-s', str(system_config_file),
            '-o', str(task_file),
            '--min-task-util', str(self.task_util_min),
            '--max-task-util', str(self.task_util_max),
            '--wcet-rounding', self.wcet_rounding,
        ]
        if self.actual_utilization_tolerance_total is not None:
            cmd.extend([
                '--actual-utilization-tolerance-total',
                str(self.actual_utilization_tolerance_total),
            ])
        if self.constrained_deadlines:
            cmd.append('--constrained-deadlines')

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30, text=True)
            return str(task_file)
        except subprocess.CalledProcessError as e:
            error_output = (e.stderr or e.stdout or '').strip()
            print(f"❌ 生成任务集失败 (U={utilization:.2f}, idx={task_idx}, seed={seed}): {error_output or e}")
            return None
        except Exception as e:
            print(f"❌ 生成任务集失败 (U={utilization:.2f}, idx={task_idx}, seed={seed}): {e}")
            return None

    def modify_config(self, algorithm: str):
        """修改系统配置文件，同时保持原始YAML格式风格以兼容rtsim解析器"""
        with open(CONFIG_TEMPLATE, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        initial_energy = self.battery_capacity * self.initial_energy_ratio
        use_real_solar_data = 'true' if self.use_real_solar_data else 'false'

        updated_lines = []
        in_energy_management = False
        in_cpu_islands = False
        in_kernel = False

        for line in lines:
            stripped = line.strip()

            if stripped == 'cpu_islands:':
                in_cpu_islands = True
                in_energy_management = False
            elif stripped == 'energy_management:':
                in_energy_management = True
                in_cpu_islands = False
                in_kernel = False
            elif stripped.endswith(':') and stripped not in {'cpu_islands:', 'energy_management:', 'kernel:'}:
                if not line.startswith(' '):
                    in_energy_management = False
                    in_cpu_islands = False
                    in_kernel = False

            if in_cpu_islands and stripped == 'kernel:':
                in_kernel = True
            elif in_cpu_islands and in_kernel and not line.startswith('      '):
                in_kernel = False

            if in_cpu_islands and in_kernel and stripped.startswith('scheduler:'):
                indent = line[:len(line) - len(line.lstrip())]
                updated_lines.append(f'{indent}scheduler: {algorithm}\n')
                continue

            if in_cpu_islands and stripped.startswith('numcpus:'):
                indent = line[:len(line) - len(line.lstrip())]
                updated_lines.append(
                    f'{indent}numcpus: {int(self.system_cores)}\n'
                )
                continue

            if in_energy_management and stripped.startswith('initial_energy_ratio:'):
                indent = line[:len(line) - len(line.lstrip())]
                updated_lines.append(f'{indent}initial_energy_ratio: {self.initial_energy_ratio}\n')
                continue

            if in_energy_management and stripped.startswith('initial_energy:'):
                indent = line[:len(line) - len(line.lstrip())]
                comment = ''
                if '#' in line:
                    comment = '  #' + line.split('#', 1)[1].rstrip('\n')
                updated_lines.append(f'{indent}initial_energy: {initial_energy}{comment}\n')
                continue

            if in_energy_management and stripped.startswith('max_energy:'):
                indent = line[:len(line) - len(line.lstrip())]
                comment = ''
                if '#' in line:
                    comment = '  #' + line.split('#', 1)[1].rstrip('\n')
                updated_lines.append(f'{indent}max_energy: {self.battery_capacity}{comment}\n')
                continue

            if in_energy_management and stripped.startswith('time_of_day_ms:'):
                indent = line[:len(line) - len(line.lstrip())]
                comment = ''
                if '#' in line:
                    comment = '  #' + line.split('#', 1)[1].rstrip('\n')
                updated_lines.append(f'{indent}time_of_day_ms: {self.solar_start_time_ms}{comment}\n')
                continue

            if (
                in_energy_management
                and stripped.startswith('base_harvesting_rate:')
            ):
                if self.use_real_solar_data:
                    updated_lines.append(line)
                    continue
                indent = line[:len(line) - len(line.lstrip())]
                base_rate = float(
                    line.split(':', 1)[1].split('#', 1)[0].strip()
                )
                comment = ''
                if '#' in line:
                    comment = '  #' + line.split('#', 1)[1].rstrip('\n')
                effective_rate = base_rate * self.harvesting_scale
                updated_lines.append(
                    f'{indent}base_harvesting_rate: '
                    f'{effective_rate}{comment}\n'
                )
                continue

            if in_energy_management and stripped.startswith('harvesting_scale:'):
                indent = line[:len(line) - len(line.lstrip())]
                updated_lines.append(
                    f'{indent}harvesting_scale: {self.harvesting_scale}\n'
                )
                continue

            if in_energy_management and stripped.startswith('day_of_year:'):
                indent = line[:len(line) - len(line.lstrip())]
                updated_lines.append(f'{indent}day_of_year: 187\n')
                continue

            if in_energy_management and stripped.startswith('use_real_solar_data:'):
                indent = line[:len(line) - len(line.lstrip())]
                comment = ''
                if '#' in line:
                    comment = '  #' + line.split('#', 1)[1].rstrip('\n')
                updated_lines.append(f'{indent}use_real_solar_data: {use_real_solar_data}{comment}\n')
                continue

            if in_energy_management and stripped.startswith('solar_data_file:'):
                if self.use_real_solar_data:
                    indent = line[:len(line) - len(line.lstrip())]
                    updated_lines.append(
                        '{}solar_data_file: {}\n'.format(
                            indent,
                            self.solar_snapshot['actual_simulator_solar_path'],
                        )
                    )
                else:
                    updated_lines.append(line)
                continue

            updated_lines.append(line)

        temp_config = self.output_dir / f'config_{algorithm}.yml'
        with open(temp_config, 'w', encoding='utf-8') as f:
            f.writelines(updated_lines)
        return str(temp_config)

    def run_experiments(self):
        """运行所有实验"""
        results = defaultdict(lambda: defaultdict(list))

        total_runs = len(self.utilization_points) * self.num_tasksets * len(ALGORITHMS)
        print(f"\n{'='*60}")
        print(f"接受率实验：归一化处理器利用率 vs 可调度性（二元）")
        print(f"{'='*60}")
        print(f"🚀 开始实验...")
        print(f"   利用率点数: {len(self.utilization_points)}")
        print(f"   每点任务集数: {self.num_tasksets}")
        print(f"   算法数: {len(ALGORITHMS)}")
        print(f"   总仿真数: {total_runs}")
        print(f"   评估方法: 二元可调度性（0=失败, 1=成功）")
        print(f"   并发线程数: {self.max_workers}")

        config_files = {}
        for algo in ALGORITHMS:
            config_files[algo] = self.modify_config(algo)
        task_generation_config = config_files[ASAP_BLOCK_ALGORITHM]

        tasks = []
        for u_idx, utilization in enumerate(self.utilization_points):
            print(f"\n📊 处理利用率点 {u_idx+1}/{len(self.utilization_points)}: U_norm={utilization:.2f}")
            point_config_id = self.config_id(utilization)
            point_config_group_id = self.config_group_id(utilization)
            point_solar_provenance = dict(self.solar_snapshot)

            task_files = []
            for task_idx in range(self.num_tasksets):
                seed = self.taskset_seed(utilization, task_idx)
                task_file = self.generate_taskset(
                    utilization,
                    task_idx,
                    seed,
                    system_config_file=task_generation_config,
                )
                target_total_utilization = utilization * self.system_cores
                planned_metadata = {
                    'source_run_id': self.run_id,
                    'run_id': self.run_id,
                    'config_id': point_config_id,
                    'config_group_id': point_config_group_id,
                    'taskset_hash': '',
                    'taskset_semantic_hash': '',
                    'taskset_raw_file_hash': '',
                    'solar_profile_sha256': point_solar_provenance['sha256'],
                    'solar_profile_path_normalized': (
                        point_solar_provenance['path_normalized']
                    ),
                    'solar_profile_present': point_solar_provenance['present'],
                    'solar_profile_size': point_solar_provenance['size'],
                    'target_normalized_utilization': float(utilization),
                    'target_total_utilization': target_total_utilization,
                    'actual_total_utilization': '',
                    'actual_normalized_utilization': '',
                    'utilization_error_total': '',
                    'utilization_error_normalized': '',
                    'task_util_min': self.task_util_min,
                    'task_util_max': self.task_util_max,
                    'wcet_rounding': self.wcet_rounding,
                    'deadline_mode': (
                        'constrained'
                        if self.constrained_deadlines else 'implicit'
                    ),
                    'actual_utilization_tolerance_total': (
                        ''
                        if self.actual_utilization_tolerance_total is None
                        else self.actual_utilization_tolerance_total
                    ),
                }
                if task_file:
                    taskset_metadata = load_taskset_utilization_metadata(
                        task_file,
                        target_normalized_utilization=float(utilization),
                        target_total_utilization=target_total_utilization,
                        num_cores=self.system_cores,
                        task_util_min=self.task_util_min,
                        task_util_max=self.task_util_max,
                        wcet_rounding=self.wcet_rounding,
                        deadline_mode=planned_metadata['deadline_mode'],
                        actual_utilization_tolerance_total=(
                            planned_metadata[
                                'actual_utilization_tolerance_total'
                            ]
                        ),
                    )
                    taskset_metadata.update({
                        'source_run_id': self.run_id,
                        'run_id': self.run_id,
                        'config_id': point_config_id,
                        'config_group_id': point_config_group_id,
                        'taskset_semantic_hash': taskset_semantic_hash(
                            task_file
                        ),
                        'taskset_raw_file_hash': taskset_file_hash(task_file),
                    })
                    # Compatibility field has one unambiguous meaning:
                    # logical sample identity, never raw-file integrity.
                    taskset_metadata['taskset_hash'] = (
                        taskset_metadata['taskset_semantic_hash']
                    )
                    task_files.append(
                        (task_idx, task_file, seed, taskset_metadata)
                    )
                else:
                    for algo in ALGORITHMS:
                        rta_enabled = (
                            self.enable_rta
                            and algo == ASAP_BLOCK_ALGORITHM
                        )
                        results[algo][utilization].append({
                            'algorithm': algo,
                            'utilization': float(utilization),
                            'task_idx': int(task_idx),
                            'task_index': int(task_idx),
                            'task_file': '',
                            'taskset_id': self.taskset_id(
                                utilization, task_idx
                            ),
                            'seed_base': self.seed_base,
                            'taskset_seed': seed,
                            'seed': seed,
                            **planned_metadata,
                            'simulation_acceptance': math.nan,
                            'acceptance_ratio': math.nan,
                            'simulation_status': 'generation_error',
                            'accepted': False,
                            'rejected': False,
                            'error': True,
                            'timeout': False,
                            'reason': 'taskset generation failed',
                            'simulation_error': 'taskset generation failed',
                            'trace_path': '',
                            'expected_simulation_horizon_ms': float(
                                self.simulation_time
                            ),
                            'observed_trace_horizon_ms': None,
                            'trace_schema_version': None,
                            'simulation_completed': None,
                            'simulation_completion_reason': '',
                            'result_schema_version': RESULT_SCHEMA_VERSION,
                            'scheduler': algo,
                            'expected_configured_scheduler': algo,
                            'expected_scheduler_display_name': (
                                ALGO_DISPLAY_NAMES.get(algo, algo)
                            ),
                            'expected_scheduler_implementation': (
                                SCHEDULER_IMPLEMENTATIONS.get(algo, algo)
                            ),
                            'observed_configured_scheduler': '',
                            'observed_scheduler_display_name': '',
                            'observed_scheduler_implementation': '',
                            'observed_scheduler_rtti_name': '',
                            'rta_enabled': rta_enabled,
                            'rta_status': (
                                'rta_error' if rta_enabled
                                else 'not_applicable' if self.enable_rta
                                else 'disabled'
                            ),
                            'rta_error': (
                                'taskset generation failed'
                                if rta_enabled else None
                            ),
                        })

            if not task_files:
                print(f"⚠️ 没有成功生成任务集，跳过 U={utilization:.2f}")
                continue

            for task_idx, task_file, seed, taskset_metadata in task_files:
                for algo in ALGORITHMS:
                    tasks.append((
                        algo,
                        config_files[algo],
                        task_file,
                        task_idx,
                        utilization,
                        self.simulation_time,
                        str(self.trace_dir),
                        {
                            'enable_rta': self.enable_rta,
                            'horizon_ms': self.rta_horizon_ms,
                            'assume_no_overflow': (
                                self.rta_assume_no_overflow
                            ),
                            'timeout': self.rta_timeout,
                            'initial_energy': self.rta_initial_energy,
                            'profile_rta': self.profile_rta,
                            'soundness_mode': self.rta_soundness_mode,
                            'seed_base': self.seed_base,
                            'taskset_seed': seed,
                            'taskset_id': self.taskset_id(
                                utilization, task_idx
                            ),
                            'source_run_id': self.run_id,
                            'run_id': self.run_id,
                            'config_id': point_config_id,
                            'config_group_id': point_config_group_id,
                            'taskset_hash': taskset_metadata['taskset_hash'],
                            'taskset_semantic_hash': taskset_metadata[
                                'taskset_semantic_hash'
                            ],
                            'taskset_raw_file_hash': taskset_metadata[
                                'taskset_raw_file_hash'
                            ],
                            'solar_profile_provenance': (
                                point_solar_provenance
                            ),
                            'taskset_metadata': taskset_metadata,
                            'keep_traces': self.keep_traces,
                            'semantic_traces': self.semantic_traces,
                            'rta_tool_snapshot': self.rta_code_snapshot.get(
                                'snapshot_path', ''
                            ),
                            'rta_code_snapshot': self.rta_code_snapshot,
                        },
                    ))

        count = 0
        rta_output = None
        rta_temporary = self.rta_results_file.with_suffix('.jsonl.partial')
        try:
            if self.enable_rta:
                rta_output = open(
                    rta_temporary, 'w', encoding='utf-8'
                )

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(run_single_simulation_worker, task): task
                    for task in tasks
                }

                for future in as_completed(futures):
                    run_result = future.result()
                    algorithm = run_result['algorithm']
                    utilization = run_result['utilization']
                    if run_result['simulation_error']:
                        print(run_result['simulation_error'])
                    if run_result['rta_error']:
                        print(
                            "⚠️ RTA error: {}, U={:.2f}, idx={}: {}".format(
                                algorithm,
                                utilization,
                                run_result['task_idx'],
                                run_result['rta_error'],
                            )
                        )
                    results[algorithm][utilization].append(run_result)

                    if rta_output is not None and run_result['rta_enabled']:
                        rta_output.write(
                            json.dumps(run_result, ensure_ascii=False) + '\n'
                        )
                        rta_output.flush()

                    count += 1
                    if count % 10 == 0 or count == total_runs:
                        print(
                            f"   进度: {count}/{total_runs} "
                            f"({(count/total_runs)*100:.1f}%)"
                        )
        finally:
            if rta_output is not None:
                rta_output.flush()
                os.fsync(rta_output.fileno())
                rta_output.close()
        if self.enable_rta:
            os.replace(rta_temporary, self.rta_results_file)

        for config_file in config_files.values():
            if os.path.exists(config_file):
                os.remove(config_file)

        if self.enable_rta:
            violation_count = sum(
                1
                for algorithm_results in results.values()
                for run_results in algorithm_results.values()
                for result in run_results
                if isinstance(result, dict)
                and result.get('soundness_violation', False)
            )
            print(
                'RTA soundness violations recorded: {}'.format(
                    violation_count
                )
            )

        self.write_per_taskset_results(results)
        return results

    def _per_taskset_result_row(self, algorithm, utilization, result):
        """Normalize one scheduler/taskset outcome for paired analysis."""
        result = result if isinstance(result, dict) else {
            'acceptance_ratio': float(result),
        }
        status = classify_simulation_status(result)
        rta_enabled = bool(result.get('rta_enabled', False))
        rta_failure_reasons = result.get('rta_failure_reasons') or {}
        if isinstance(rta_failure_reasons, dict):
            rta_reason = json.dumps(
                rta_failure_reasons, ensure_ascii=False, sort_keys=True
            ) if rta_failure_reasons else ''
        else:
            rta_reason = str(rta_failure_reasons)
        if result.get('rta_error'):
            rta_reason = str(result['rta_error'])

        # The raw taskset row keeps its scalar contract explicit:
        # tightness = rta_response_time_bound / simulated_response_time.
        # Aggregate CSV tightness remains the mean of correctly paired
        # per-task samples collected by tightness_values_for_result().
        scalar_tightness = tightness_for_result(algorithm, result)
        tightness = scalar_tightness if scalar_tightness is not None else ''
        simulation_reason = (
            result.get('reason') or result.get('simulation_error') or ''
        )
        if status == 'rejected' and not simulation_reason:
            simulation_reason = 'rejected by simulation trace checks'
        rta_proven = bool(_is_rta_proven(result))
        rta_schedulable = bool(
            result.get(
                'rta_schedulable',
                algorithm == ASAP_BLOCK_ALGORITHM and rta_proven,
            )
        )
        sim_schedulable = bool(
            result.get('sim_schedulable', status == 'accepted')
        )
        soundness_status = result.get('simulation_status') or status
        soundness = classify_soundness_observation(
            rta_schedulable, sim_schedulable, soundness_status
        )
        soundness_violation = bool(soundness['soundness_violation'])
        soundness_valid = bool(
            result.get('soundness_valid', soundness['soundness_valid'])
        )
        soundness_excluded_reason = result.get(
            'soundness_excluded_reason',
            soundness['soundness_excluded_reason'],
        )
        rta_bound = result.get('rta_bound', '')
        observed_response = result.get('simulated_response_time', '')
        taskset_seed = result.get('taskset_seed', result.get('seed', ''))
        config_id = result.get('config_id') or self.config_id(utilization)
        config_group_id = (
            result.get('config_group_id')
            or self.config_group_id(utilization)
        )

        return {
            'experiment_id': self.experiment_id,
            'run_id': self.run_id,
            'source_run_id': result.get(
                'source_run_id', self.run_id
            ),
            'output_dir': str(self.output_dir),
            'config_id': config_id,
            'config_group_id': config_group_id,
            'seed_base': result.get('seed_base', self.seed_base),
            'taskset_seed': taskset_seed,
            'normalized_utilization': float(utilization),
            'target_normalized_utilization': result.get(
                'target_normalized_utilization',
                float(utilization),
            ),
            'target_total_utilization': result.get(
                'target_total_utilization',
                float(utilization) * self.system_cores,
            ),
            'actual_total_utilization': result.get(
                'actual_total_utilization',
                '',
            ),
            'actual_normalized_utilization': result.get(
                'actual_normalized_utilization',
                '',
            ),
            'utilization_error_total': result.get(
                'utilization_error_total',
                '',
            ),
            'utilization_error_normalized': result.get(
                'utilization_error_normalized',
                '',
            ),
            'task_util_min': result.get(
                'task_util_min',
                self.task_util_min,
            ),
            'task_util_max': result.get(
                'task_util_max',
                self.task_util_max,
            ),
            'wcet_rounding': result.get(
                'wcet_rounding',
                self.wcet_rounding,
            ),
            'deadline_mode': result.get(
                'deadline_mode',
                'constrained' if self.constrained_deadlines else 'implicit',
            ),
            'actual_utilization_tolerance_total': result.get(
                'actual_utilization_tolerance_total',
                (
                    ''
                    if self.actual_utilization_tolerance_total is None
                    else self.actual_utilization_tolerance_total
                ),
            ),
            'task_idx': result.get('task_idx', ''),
            'task_index': result.get(
                'task_index', result.get('task_idx', '')
            ),
            'taskset_id': result.get('taskset_id', ''),
            'taskset_hash': result.get('taskset_hash', ''),
            'taskset_semantic_hash': result.get(
                'taskset_semantic_hash', result.get('taskset_hash', '')
            ),
            'taskset_raw_file_hash': result.get(
                'taskset_raw_file_hash', ''
            ),
            'seed': taskset_seed,
            'algorithm': algorithm,
            'scheduler': algorithm,
            'algorithm_display_name': ALGO_DISPLAY_NAMES.get(
                algorithm, algorithm
            ),
            'num_tasks': self.task_n,
            'num_cores': self.system_cores,
            'battery': self.battery_capacity,
            'initial_energy': (
                self.battery_capacity * self.initial_energy_ratio
            ),
            'initial_energy_ratio': self.initial_energy_ratio,
            'solar_time_ms': self.solar_start_time_ms,
            'harvesting_profile': self.harvesting_profile(),
            'harvesting_scale': self.harvesting_scale,
            'solar_profile_sha256': result.get(
                'solar_profile_sha256', ''
            ),
            'solar_profile_path_normalized': result.get(
                'solar_profile_path_normalized', ''
            ),
            'solar_profile_present': result.get(
                'solar_profile_present', False
            ),
            'solar_profile_size': result.get('solar_profile_size', 0),
            'solar_source_path': result.get('solar_source_path', ''),
            'solar_source_sha256': result.get(
                'solar_source_sha256', 'not_used'
            ),
            'solar_snapshot_relative_path': result.get(
                'solar_snapshot_relative_path', ''
            ),
            'solar_snapshot_time': result.get('solar_snapshot_time', ''),
            'actual_simulator_solar_path': result.get(
                'actual_simulator_solar_path', ''
            ),
            'simulation_horizon_ms': self.simulation_time,
            'observed_trace_horizon_ms': result.get(
                'observed_trace_horizon_ms', ''
            ),
            'trace_schema_version': result.get('trace_schema_version', ''),
            'simulation_completed': result.get('simulation_completed', ''),
            'simulation_completion_reason': result.get(
                'simulation_completion_reason', ''
            ),
            'accepted': int(status == 'accepted'),
            'rejected': int(status == 'rejected'),
            'timeout': int(status == 'timeout'),
            'error': int(status == 'error'),
            'status': status,
            'reason': simulation_reason,
            'trace_path': result.get('trace_path', ''),
            'result_schema_version': result.get(
                'result_schema_version', RESULT_SCHEMA_VERSION
            ),
            'expected_configured_scheduler': result.get(
                'expected_configured_scheduler', algorithm
            ),
            'expected_scheduler_display_name': result.get(
                'expected_scheduler_display_name',
                ALGO_DISPLAY_NAMES.get(algorithm, algorithm),
            ),
            'expected_scheduler_implementation': result.get(
                'expected_scheduler_implementation',
                SCHEDULER_IMPLEMENTATIONS.get(algorithm, algorithm),
            ),
            'observed_configured_scheduler': result.get(
                'observed_configured_scheduler',
                result.get('configured_scheduler', ''),
            ),
            'observed_scheduler_display_name': result.get(
                'observed_scheduler_display_name',
                result.get('scheduler_display_name', ''),
            ),
            'observed_scheduler_implementation': result.get(
                'observed_scheduler_implementation',
                result.get('scheduler_implementation', ''),
            ),
            'observed_scheduler_rtti_name': result.get(
                'observed_scheduler_rtti_name',
                result.get('scheduler_rtti_name', ''),
            ),
            'configured_scheduler': result.get(
                'configured_scheduler', ''
            ),
            'scheduler_display_name': result.get(
                'scheduler_display_name', ''
            ),
            'scheduler_implementation': result.get(
                'scheduler_implementation', ''
            ),
            'rta_enabled': rta_enabled,
            'rta_version': (
                result.get('rta_version', RTA_VERSION)
                if rta_enabled
                else RTA_INACTIVE_VERSION
            ),
            'rta_code_fingerprint': (
                result.get(
                    'rta_code_fingerprint',
                    self.rta_code_snapshot.get('combined_sha256', 'not_used'),
                ) if rta_enabled else 'not_used'
            ),
            'rta_code_snapshot_path': (
                result.get(
                    'rta_code_snapshot_path',
                    self.rta_code_snapshot.get('snapshot_path', ''),
                ) if rta_enabled else ''
            ),
            'rta_code_snapshot_sha256': (
                result.get(
                    'rta_code_snapshot_sha256',
                    self.rta_code_snapshot.get('snapshot_sha256', 'not_used'),
                ) if rta_enabled else 'not_used'
            ),
            'rta_code_snapshot_size': (
                result.get(
                    'rta_code_snapshot_size',
                    self.rta_code_snapshot.get('snapshot_size', 0),
                ) if rta_enabled else 0
            ),
            'rta_code_source_path': (
                result.get(
                    'rta_code_source_path',
                    self.rta_code_snapshot.get('entrypoint', ''),
                ) if rta_enabled else ''
            ),
            'rta_code_source_sha256': (
                result.get(
                    'rta_code_source_sha256',
                    self.rta_code_snapshot.get('entrypoint_sha256', 'not_used'),
                ) if rta_enabled else 'not_used'
            ),
            'rta_status': result.get('rta_status', 'disabled'),
            'rta_attempted': bool(result.get('rta_attempted', False)),
            'rta_runtime_sec': (
                ''
                if result.get('rta_runtime_sec') is None
                else result.get('rta_runtime_sec')
            ),
            'rta_runtime_source': result.get('rta_runtime_source', ''),
            'rta_timed_out': bool(result.get('rta_timed_out', False)),
            'rta_timeout_sec': (
                ''
                if result.get('rta_timeout_sec') is None
                else result.get('rta_timeout_sec')
            ),
            'rta_profile_enabled': bool(
                result.get('rta_profile_enabled', False)
            ),
            'rta_profile_task_time_sum_sec': (
                ''
                if result.get('rta_profile_task_time_sum_sec') is None
                else result.get('rta_profile_task_time_sum_sec')
            ),
            'rta_profile_task_count': int(
                result.get('rta_profile_task_count', 0)
            ),
            'rta_proven': rta_proven,
            'rta_schedulable': rta_schedulable,
            'sim_schedulable': sim_schedulable,
            'soundness_violation': soundness_violation,
            'soundness_valid': soundness_valid,
            'soundness_excluded_reason': soundness_excluded_reason,
            'rta_error': result.get('rta_error') or '',
            'rta_reason': rta_reason,
            'rta_response_time_bound': rta_bound,
            'rta_response_bound': rta_bound,
            'simulated_response_time': observed_response,
            'observed_max_response_time': observed_response,
            'first_missed_job_release': result.get(
                'first_missed_job_release', ''
            ),
            'first_missed_deadline': result.get(
                'first_missed_deadline', ''
            ),
            'tightness': tightness,
        }

    def per_taskset_result_rows(self, results):
        """Flatten nested experiment results into deterministic raw rows."""
        rows = []
        for algorithm in ALGORITHMS:
            for utilization in self.utilization_points:
                for result in results[algorithm][utilization]:
                    rows.append(self._per_taskset_result_row(
                        algorithm, utilization, result
                    ))
        validate_formal_result_identities(rows, 'per_taskset result write')
        return rows

    def write_per_taskset_results(self, results):
        """Write one row per scheduler/taskset simulation outcome."""
        rows = self.per_taskset_result_rows(results)
        frame = pd.DataFrame(rows, columns=PER_TASKSET_RESULT_FIELDS)
        _atomic_dataframe_to_csv(frame, self.per_taskset_results_file)
        print(
            'Per-taskset results saved: {} ({} rows)'.format(
                self.per_taskset_results_file, len(frame)
            )
        )
        return frame

    def aggregate_results(self, results):
        """
        Aggregate conditional simulation acceptance.

        acceptance_ratio = accepted / (accepted + rejected). Infrastructure
        errors and timeouts remain visible but do not enter that denominator.
        num_samples retains its historical requested-count meaning; the
        explicit num_valid_samples and num_requested_samples columns remove
        the previous ambiguity.
        """
        common_summaries = self.common_complete_summaries(results)
        data = []
        for algo in ALGORITHMS:
            for utilization in self.utilization_points:
                run_results = results[algo][utilization]
                if run_results:
                    status_buckets = [
                        classify_simulation_status(result)
                        for result in run_results
                    ]
                    requested_count = len(status_buckets)
                    accepted_count = status_buckets.count('accepted')
                    rejected_count = status_buckets.count('rejected')
                    error_count = status_buckets.count('error')
                    timeout_count = status_buckets.count('timeout')
                    generation_error_count = sum(
                        _normalise_simulation_status(
                            result.get('simulation_status')
                        ) in {'generation_error', 'yaml_generation_failed'}
                        for result in run_results
                        if isinstance(result, dict)
                    )
                    valid_count = accepted_count + rejected_count
                    avg_acceptance = (
                        accepted_count / valid_count
                        if valid_count else np.nan
                    )
                    unconditional_success_rate = (
                        accepted_count / requested_count
                        if requested_count else np.nan
                    )
                    actual_total_values = [
                        float(result['actual_total_utilization'])
                        for result in run_results
                        if isinstance(result, dict)
                        and result.get('actual_total_utilization') not in {
                            None,
                            '',
                        }
                    ]
                    actual_norm_values = [
                        float(result['actual_normalized_utilization'])
                        for result in run_results
                        if isinstance(result, dict)
                        and result.get('actual_normalized_utilization') not in {
                            None,
                            '',
                        }
                    ]
                    util_error_values = [
                        float(result['utilization_error_total'])
                        for result in run_results
                        if isinstance(result, dict)
                        and result.get('utilization_error_total') not in {
                            None,
                            '',
                        }
                    ]
                    row = {
                        'result_schema_version': RESULT_SCHEMA_VERSION,
                        'run_id': self.run_id,
                        'source_run_id': self.run_id,
                        'config_id': self.config_id(utilization),
                        'config_group_id': self.config_group_id(utilization),
                        'algorithm': algo,
                        'algorithm_display_name': ALGO_DISPLAY_NAMES.get(
                            algo, algo
                        ),
                        'expected_configured_scheduler': algo,
                        'expected_scheduler_display_name': (
                            ALGO_DISPLAY_NAMES.get(algo, algo)
                        ),
                        'expected_scheduler_implementation': (
                            SCHEDULER_IMPLEMENTATIONS.get(algo, algo)
                        ),
                        'normalized_utilization': utilization,
                        'acceptance_ratio': avg_acceptance,
                        'unconditional_success_rate': (
                            unconditional_success_rate
                        ),
                        'error_rate': (
                            error_count / requested_count
                            if requested_count else np.nan
                        ),
                        'timeout_rate': (
                            timeout_count / requested_count
                            if requested_count else np.nan
                        ),
                        'num_samples': requested_count,
                        'num_successful': accepted_count,
                        'num_valid_samples': valid_count,
                        'num_requested_samples': requested_count,
                        'no_valid_simulations': valid_count == 0,
                        'seed_base': self.seed_base,
                        'taskset_count': self.num_tasksets,
                        'core_count': self.system_cores,
                        'task_n': self.task_n,
                        'period_min_ms': self.task_p_min,
                        'period_max_ms': self.task_p_max,
                        'task_util_min': self.task_util_min,
                        'task_util_max': self.task_util_max,
                        'wcet_rounding': self.wcet_rounding,
                        'deadline_mode': (
                            'constrained'
                            if self.constrained_deadlines else 'implicit'
                        ),
                        'avg_actual_total_utilization': (
                            float(np.mean(actual_total_values))
                            if actual_total_values else np.nan
                        ),
                        'avg_actual_normalized_utilization': (
                            float(np.mean(actual_norm_values))
                            if actual_norm_values else np.nan
                        ),
                        'avg_utilization_error_total': (
                            float(np.mean(util_error_values))
                            if util_error_values else np.nan
                        ),
                        'battery_capacity': self.battery_capacity,
                        'initial_energy': (
                            self.battery_capacity * self.initial_energy_ratio
                        ),
                        'initial_energy_ratio': self.initial_energy_ratio,
                        'solar_time_ms': self.solar_start_time_ms,
                        'simulation_horizon_ms': self.simulation_time,
                        'harvesting_profile': self.harvesting_profile(),
                        'harvesting_scale': self.harvesting_scale,
                        'solar_profile_sha256': self.solar_snapshot['sha256'],
                        'solar_profile_path_normalized': self.solar_snapshot[
                            'path_normalized'
                        ],
                        'solar_snapshot_relative_path': self.solar_snapshot[
                            'snapshot_relative_path'
                        ],
                        'rta_enabled': bool(
                            self.enable_rta
                            and algo == ASAP_BLOCK_ALGORITHM
                        ),
                        'rta_code_fingerprint': (
                            self.rta_code_snapshot.get(
                                'combined_sha256', 'not_used'
                            )
                            if self.enable_rta
                            and algo == ASAP_BLOCK_ALGORITHM
                            else 'not_used'
                        ),
                        'rta_version': (
                            RTA_VERSION
                            if self.enable_rta
                            and algo == ASAP_BLOCK_ALGORITHM
                            else RTA_INACTIVE_VERSION
                        ),
                        'simulation_num_accepted': accepted_count,
                        'simulation_num_rejected': rejected_count,
                        'simulation_num_timeout': timeout_count,
                        'simulation_num_error': error_count,
                        'simulation_num_generation_error': (
                            generation_error_count
                        ),
                        'simulation_num_valid': valid_count,
                        'simulation_num_requested': requested_count,
                    }
                    common = common_summaries[utilization]
                    common_counts = common['algorithms'][algo]
                    common_wilson_low, common_wilson_high = _wilson_interval(
                        common_counts['accepted'], common['complete']
                    )
                    row.update({
                        'common_complete_num_tasksets': common['complete'],
                        'requested_num_tasksets': common['requested'],
                        'common_complete_ratio': (
                            common['complete'] / common['requested']
                            if common['requested'] else np.nan
                        ),
                        'common_complete_excluded_num': common['excluded'],
                        'common_complete_excluded_error': (
                            common['excluded_error']
                        ),
                        'common_complete_excluded_timeout': (
                            common['excluded_timeout']
                        ),
                        'common_complete_excluded_generation_error': (
                            common['excluded_generation_error']
                        ),
                        'common_complete_excluded_missing': (
                            common['excluded_missing']
                        ),
                        'common_complete_accepted': common_counts['accepted'],
                        'common_complete_rejected': common_counts['rejected'],
                        'common_complete_acceptance_ratio': (
                            common_counts['accepted'] / common['complete']
                            if common['complete'] else np.nan
                        ),
                        'common_complete_unconditional_success_rate': (
                            common_counts['accepted'] / common['requested']
                            if common['requested'] else np.nan
                        ),
                        'common_complete_wilson_ci95_low': common_wilson_low,
                        'common_complete_wilson_ci95_high': common_wilson_high,
                        'common_complete_no_valid_simulations': (
                            common['complete'] == 0
                        ),
                        'official_run_invalid': (
                            common['complete'] != common['requested']
                        ),
                        'official_run_valid': (
                            common['complete'] == common['requested']
                        ),
                        'common_complete_sample_definition': (
                            'Only tasksets with valid accepted/rejected '
                            'outcomes under all nine schedulers are included.'
                        ),
                    })

                    rta_results = [
                        result for result in run_results
                        if isinstance(result, dict)
                        and result.get('rta_enabled', False)
                    ]
                    rta_num_analyzed = len(rta_results)
                    rta_num_proven = sum(
                        result.get('rta_status')
                        == 'proven_under_assumptions'
                        for result in rta_results
                    )
                    rta_num_unproven = sum(
                        result.get('rta_status') == 'rta_unproven'
                        for result in rta_results
                    )
                    rta_num_errors = sum(
                        result.get('rta_status') in {
                            'rta_error',
                            'rta_timeout',
                            'timeout',
                            'failed',
                        }
                        for result in rta_results
                    )
                    rta_soundness_violations = sum(
                        bool(result.get('soundness_violation', False))
                        for result in rta_results
                    )
                    tightness_values = []
                    for result in run_results:
                        tightness_values.extend(
                            tightness_values_for_result(algo, result)
                        )
                    row.update({
                        'rta_num_analyzed': rta_num_analyzed,
                        'rta_num_proven': rta_num_proven,
                        'rta_num_unproven': rta_num_unproven,
                        'rta_num_errors': rta_num_errors,
                        'rta_soundness_violations': (
                            rta_soundness_violations
                        ),
                        'rta_proven_ratio': (
                            rta_num_proven / rta_num_analyzed
                            if rta_num_analyzed else np.nan
                        ),
                        'sim_success_rta_proven': sum(
                            result.get('acceptance_ratio') == 1.0
                            and result.get('rta_status')
                            == 'proven_under_assumptions'
                            for result in rta_results
                        ),
                        'sim_success_rta_unproven': sum(
                            result.get('acceptance_ratio') == 1.0
                            and result.get('rta_status') == 'rta_unproven'
                            for result in rta_results
                        ),
                        'avg_tightness': (
                            float(np.mean(tightness_values))
                            if tightness_values else np.nan
                        ),
                        'tightness_num_samples': len(tightness_values),
                    })
                    data.append(row)

        return pd.DataFrame(data)

    def common_complete_summaries(self, results):
        """Return the common valid taskset set for all nine schedulers."""
        summaries = {}
        for utilization in self.utilization_points:
            by_algorithm = {}
            requested_keys = set()
            formal_records = []
            for algorithm in ALGORITHMS:
                mapped = {}
                for position, original in enumerate(
                        results[algorithm][utilization]):
                    if isinstance(original, dict):
                        result = dict(original)
                    else:
                        ratio = float(original)
                        result = {
                            'acceptance_ratio': ratio,
                            'simulation_status': (
                                'accepted' if ratio == 1.0 else 'rejected'
                            ),
                        }
                    result.setdefault('algorithm', algorithm)
                    # Keep the historical in-memory aggregate API usable for
                    # unit/RTA callers. Formal schema-v4 rows never receive
                    # inferred provenance and remain strictly validated.
                    if result.get('result_schema_version') != RESULT_SCHEMA_VERSION:
                        legacy_index = result.get(
                            'task_idx', result.get('taskset_id', position)
                        )
                        result.setdefault(
                            'config_id', self.config_id(utilization)
                        )
                        result.setdefault(
                            'config_group_id',
                            self.config_group_id(utilization),
                        )
                        result.setdefault(
                            'taskset_id', 'legacy-in-memory-{}'.format(
                                legacy_index
                            )
                        )
                        result.setdefault(
                            'taskset_hash',
                            'legacy-in-memory-u{}-{}'.format(
                                float(utilization), legacy_index
                            ),
                        )
                    formal_records.append(result)
                    config_id = result.get('config_id')
                    taskset_hash = result.get('taskset_hash')
                    raw_status = _normalise_simulation_status(
                        result.get('simulation_status')
                    )
                    if taskset_hash:
                        identity = str(taskset_hash)
                    elif raw_status in {
                        'generation_error', 'yaml_generation_failed'
                    }:
                        identity = 'generation_error:{}'.format(
                            result.get('taskset_id', result.get('task_idx'))
                        )
                    else:
                        identity = ''
                    key = (str(config_id or ''), identity)
                    mapped[key] = result
                    requested_keys.add(key)
                by_algorithm[algorithm] = mapped

            validate_formal_result_identities(
                formal_records,
                'common-complete U={}'.format(float(utilization)),
            )

            complete_keys = []
            excluded = defaultdict(int)
            for key in sorted(requested_keys):
                rows = [by_algorithm[algorithm].get(key) for algorithm in ALGORITHMS]
                if any(row is None for row in rows):
                    excluded['missing'] += 1
                    continue
                raw_statuses = [
                    _normalise_simulation_status(row.get('simulation_status'))
                    for row in rows
                ]
                statuses = [classify_simulation_status(row) for row in rows]
                if all(status in {'accepted', 'rejected'} for status in statuses):
                    complete_keys.append(key)
                    continue
                if any(status == 'generation_error' for status in raw_statuses):
                    excluded['generation_error'] += 1
                elif any(status == 'timeout' for status in statuses):
                    excluded['timeout'] += 1
                else:
                    excluded['error'] += 1

            algorithm_counts = {}
            for algorithm in ALGORITHMS:
                statuses = [
                    classify_simulation_status(by_algorithm[algorithm][key])
                    for key in complete_keys
                ]
                algorithm_counts[algorithm] = {
                    'accepted': statuses.count('accepted'),
                    'rejected': statuses.count('rejected'),
                }

            requested = len(requested_keys)
            complete = len(complete_keys)
            summaries[utilization] = {
                'requested': requested,
                'complete': complete,
                'excluded': requested - complete,
                'excluded_error': excluded['error'],
                'excluded_timeout': excluded['timeout'],
                'excluded_generation_error': excluded['generation_error'],
                'excluded_missing': excluded['missing'],
                'algorithms': algorithm_counts,
            }
        return summaries

# ============================================
# 图表生成器
# ============================================
class FigureGenerator:
    """生成IEEE Transaction风格的接受率图表"""

    @staticmethod
    def load_data_from_csv(csv_path):
        """从CSV文件加载数据"""
        df = pd.read_csv(csv_path)

        value_column = (
            'common_complete_acceptance_ratio'
            if 'common_complete_acceptance_ratio' in df.columns
            else 'acceptance_ratio'
        )
        if value_column == 'acceptance_ratio':
            warnings.warn(
                'CSV has no common-complete acceptance column; using legacy '
                'per-scheduler conditional acceptance',
                RuntimeWarning,
                stacklevel=2,
            )
        results = {}
        for internal_name, display_name in ALGO_DISPLAY_NAMES.items():
            algo_data = df[df['algorithm'] == internal_name]

            if not algo_data.empty:
                algo_data = algo_data.sort_values('normalized_utilization')
                x = algo_data['normalized_utilization'].values
                y = pd.to_numeric(
                    algo_data[value_column], errors='coerce'
                ).values
                if np.isnan(y).any():
                    warnings.warn(
                        '{} contains no-valid/common-incomplete points; they '
                        'remain NaN and will appear as gaps'.format(internal_name),
                        RuntimeWarning,
                        stacklevel=2,
                    )
                results[internal_name] = (x, y)  # 使用内部名称作为key

        return results

    @staticmethod
    def plot_single_group(results, group_name, save_path, x_label=None):
        """
        绘制单个分组的接受率图表

        Args:
            results: 所有算法的数据 {internal_name: (x, y)}
            group_name: 分组名称 ('asap', 'alap', 'st', 'block', 'nonblock', 'sync')
            save_path: 保存路径
            x_label: X轴标签
        """
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        # 创建图表
        fig, ax = plt.subplots(figsize=(8, 6))

        # 获取该分组的算法列表
        algo_list = ALGO_GROUPS.get(group_name, [])
        if not algo_list:
            print(f"⚠️ 未知的分组: {group_name}")
            return None, None

        # 绘制每条曲线
        for algo_internal in algo_list:
            if algo_internal not in results:
                continue
            x, y = results[algo_internal]
            style = ALGO_STYLES.get(algo_internal, {'color': 'black', 'marker': 'o', 'linestyle': '-'})
            display_name = ALGO_DISPLAY_NAMES.get(algo_internal, algo_internal)

            ax.plot(x, y,
                   color=style['color'],
                   marker=style['marker'],
                   markersize=6,
                   linewidth=2,
                   linestyle=style['linestyle'],
                   label=display_name,
                   markerfacecolor='white',
                   markeredgewidth=1.5,
                   markeredgecolor=style['color'])

        # 配置坐标轴
        if x_label:
            ax.set_xlabel(x_label)
        else:
            ax.set_xlabel(r'Normalized Processor Utilization ($\sum U_i / M$)')
        ax.set_ylabel('Acceptance Ratio')
        ax.set_xlim([0, 1.05])
        ax.set_ylim([-0.05, 1.05])

        # 添加网格
        ax.grid(True, linestyle='--', alpha=0.5, color='grey', linewidth=0.5)
        ax.set_axisbelow(True)

        # 设置标题
        ax.set_title(GROUP_DISPLAY_NAMES.get(group_name, group_name), fontsize=14, fontweight='bold')

        # 配置图例
        ax.legend(loc='upper right', frameon=True, fancybox=False,
                 edgecolor='black', framealpha=1.0)

        # 设置白色背景
        ax.set_facecolor('white')
        fig.patch.set_facecolor('white')

        # 紧凑布局
        plt.tight_layout()

        # 保存图表
        plt.savefig(str(save_path), dpi=300, bbox_inches='tight',
                   facecolor='white', edgecolor='none')
        print(f"✅ 图表已保存: {save_path}")

        plt.close(fig)
        return fig, ax

    @staticmethod
    def plot_all_groups(results, output_dir, x_label=None):
        """
        生成所有6张分组图表

        Args:
            results: 所有算法的数据 {internal_name: (x, y)}
            output_dir: 输出目录
            x_label: X轴标签
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 分组顺序
        groups = ['asap', 'alap', 'st', 'block', 'nonblock', 'sync']

        for group_name in groups:
            save_path = output_dir / f'acceptance_ratio_{group_name}.png'
            print(f"\n🎨 正在生成 {GROUP_DISPLAY_NAMES[group_name]} 图表...")
            FigureGenerator.plot_single_group(results, group_name, save_path, x_label)

        print(f"\n✅ 所有图表已保存到: {output_dir}")

    @staticmethod
    def plot_acceptance_ratio(results, save_path, x_label=None):
        """
        绘制所有9种算法的单张综合图表（向后兼容）
        """
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        fig, ax = plt.subplots(figsize=(10, 7))

        # 定义不同系列的颜色
        series_colors = {
            'asap': '#1f77b4',  # 蓝色
            'alap': '#2ca02c',  # 绿色
            'st': '#d62728'     # 红色
        }

        # 绘制所有算法
        for algo_internal, display_name in ALGO_DISPLAY_NAMES.items():
            if algo_internal not in results:
                continue
            x, y = results[algo_internal]
            style = ALGO_STYLES.get(algo_internal, {})

            ax.plot(x, y,
                   color=style.get('color', 'black'),
                   marker=style.get('marker', 'o'),
                   markersize=5,
                   linewidth=1.5,
                   linestyle=style.get('linestyle', '-'),
                   label=display_name,
                   markerfacecolor='white',
                   markeredgewidth=1.2,
                   markeredgecolor=style.get('color', 'black'),
                   alpha=0.8)

        if x_label:
            ax.set_xlabel(x_label)
        else:
            ax.set_xlabel(r'Normalized Processor Utilization ($\sum U_i / M$)')
        ax.set_ylabel('Acceptance Ratio')
        ax.set_xlim([0, 1.05])
        ax.set_ylim([-0.05, 1.05])

        ax.grid(True, linestyle='--', alpha=0.5, color='grey', linewidth=0.5)
        ax.set_axisbelow(True)

        # 图例分两列显示
        ax.legend(loc='upper right', frameon=True, fancybox=False,
                 edgecolor='black', framealpha=1.0, ncol=2, fontsize=9)

        ax.set_facecolor('white')
        fig.patch.set_facecolor('white')

        plt.tight_layout()
        plt.savefig(str(save_path), dpi=300, bbox_inches='tight',
                   facecolor='white', edgecolor='none')
        print(f"✅ 综合图表已保存: {save_path}")

        plt.close(fig)
        return fig, ax

    @staticmethod
    def print_data_summary(results):
        """打印数据摘要"""
        print("\n📊 数据摘要:")
        for algo_internal, display_name in ALGO_DISPLAY_NAMES.items():
            if algo_internal not in results:
                continue
            x, y = results[algo_internal]
            print(f"{display_name}:")
            print(f"  X范围: [{x.min():.3f}, {x.max():.3f}]")
            print(f"  接受率范围: [{y.min():.3f}, {y.max():.3f}]")
            mid_idx = len(x) // 2
            if len(x) > 0:
                print(f"  中点 (X={x[mid_idx]:.3f}): 接受率={y[mid_idx]:.3f}")


def remove_formal_acceptance_figures(output_dir, figure_output=None):
    """Remove only formal figure names owned by this experiment run."""
    output_dir = Path(output_dir)
    candidates = {
        output_dir / 'acceptance_ratio_all.png',
        output_dir / 'acceptance_ratio_all.pdf',
        output_dir / 'acceptance_ratio_figure.png',
        output_dir / 'acceptance_ratio_figure.pdf',
    }
    for group_name in ['asap', 'alap', 'st', 'block', 'nonblock', 'sync']:
        candidates.add(
            output_dir / 'figures' / 'acceptance_ratio_{}.png'.format(
                group_name
            )
        )
        candidates.add(
            output_dir / 'figures' / 'acceptance_ratio_{}.pdf'.format(
                group_name
            )
        )
    if figure_output:
        candidates.add(Path(figure_output))
    for candidate in candidates:
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def suppress_formal_figures_for_incomplete_common_sample(
        frame, require_common_complete, output_dir, figure_output=None):
    invalid = bool(
        require_common_complete
        and not frame.empty
        and (
            ~frame['official_run_valid'].astype(bool)
            if 'official_run_valid' in frame
            else frame['official_run_invalid'].astype(bool)
        ).any()
    )
    if invalid:
        remove_formal_acceptance_figures(output_dir, figure_output)
    return invalid

# ============================================
# 主程序
# ============================================
def main():
    parser = argparse.ArgumentParser(
        description='接受率分析：9种算法实验 + 6张分组图表生成',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 运行完整实验并生成6张分组图表
  python3 acceptance_ratio_test.py --run-experiment

  # 仅从已有数据生成图表
  python3 acceptance_ratio_test.py --csv acceptance_ratio_experiment/acceptance_ratio_data.csv

  # 自定义实验参数
  python3 acceptance_ratio_test.py --run-experiment --num-points 10 --num-tasksets 5

算法说明:
  ASAP系列 (贪婪策略): ASAP-Block, ASAP-NonBlock, ASAP-Sync
  ALAP系列 (最晚策略): ALAP-Block, ALAP-NonBlock, ALAP-Sync
  ST系列  (标准策略): ST-Block, ST-NonBlock, ST-Sync

图表输出:
  1. ASAP系列对比图 (Block/NonBlock/Sync)
  2. ALAP系列对比图 (Block/NonBlock/Sync)
  3. ST系列对比图 (Block/NonBlock/Sync)
  4. Block系列对比图 (ASAP/ALAP/ST)
  5. NonBlock系列对比图 (ASAP/ALAP/ST)
  6. Sync系列对比图 (ASAP/ALAP/ST)

统计定义:
  acceptance_ratio = accepted / (accepted + rejected)
  error 与 timeout 不进入有效仿真分母；unconditional_success_rate 单独输出。
        """
    )

    add_experiment_cli_args(parser)

    args = parser.parse_args()
    diagnostic_csv_mode = False
    validate_rta_cli_args(parser, args)
    if args.run_experiment:
        validate_output_dir_args(parser, args)
        # A failed common-complete rerun must never leave a stale formal plot
        # that appears to describe the newly written diagnostic CSV.
        remove_formal_acceptance_figures(
            args.output_dir, args.figure_output
        )

    # 决定数据来源
    if args.run_experiment:
        # 运行实验
        if args.fixed_utilization is not None:
            utilization_points = np.array([float(args.fixed_utilization)])
        else:
            utilization_points = np.around(
                np.linspace(0.1, 1.0, args.num_points), 2
            )
        system_cores = (
            int(args.M)
            if args.M is not None
            else get_system_cores(CONFIG_TEMPLATE)
        )

        runner = ExperimentRunner(
            output_dir=args.output_dir,
            utilization_points=utilization_points,
            num_tasksets=args.num_tasksets,
            task_n=args.task_n,
            task_p_min=DEFAULT_TASK_P_MIN,
            task_p_max=DEFAULT_TASK_P_MAX,
            simulation_time=args.simulation_time,
            battery_capacity=args.battery,
            initial_energy_ratio=args.initial_energy,
            solar_start_time_ms=args.solar_time_ms,
            use_real_solar_data=DEFAULT_USE_REAL_SOLAR_DATA,
            system_cores=system_cores,
            max_workers=args.max_workers,
            enable_rta=args.enable_rta,
            rta_horizon_ms=args.rta_horizon_ms,
            rta_assume_no_overflow=args.rta_assume_no_overflow,
            rta_timeout=args.rta_timeout,
            seed_base=args.seed_base,
            rta_initial_energy=args.rta_initial_energy,
            profile_rta=args.profile_rta,
            harvesting_scale=args.harvesting_scale,
            rta_soundness_mode=args.rta_soundness_mode,
            task_util_min=args.min_task_util,
            task_util_max=args.max_task_util,
            wcet_rounding=args.wcet_rounding,
            constrained_deadlines=args.constrained_deadlines,
            actual_utilization_tolerance_total=(
                args.actual_utilization_tolerance_total
            ),
            keep_traces=args.keep_traces,
            semantic_traces=args.semantic_traces,
            require_common_complete=args.require_common_complete,
        )

        results = runner.run_experiments()
        df = runner.aggregate_results(results)

        if df.empty:
            print("\n❌ 没有产生有效数据")
            sys.exit(1)

        # 保存数据
        csv_file = Path(args.output_dir) / 'acceptance_ratio_data.csv'
        _atomic_dataframe_to_csv(df, csv_file)
        common_csv = Path(args.output_dir) / 'common_complete_acceptance_data.csv'
        common_columns = [
            column for column in df.columns
            if column in {
                'result_schema_version', 'run_id', 'source_run_id',
                'config_id', 'config_group_id', 'algorithm',
                'algorithm_display_name', 'normalized_utilization',
                'requested_num_tasksets', 'common_complete_num_tasksets',
                'common_complete_ratio', 'common_complete_excluded_num',
                'common_complete_excluded_error',
                'common_complete_excluded_timeout',
                'common_complete_excluded_generation_error',
                'common_complete_excluded_missing',
                'common_complete_accepted', 'common_complete_rejected',
                'common_complete_acceptance_ratio',
                'common_complete_unconditional_success_rate',
                'common_complete_wilson_ci95_low',
                'common_complete_wilson_ci95_high',
                'common_complete_no_valid_simulations',
                'official_run_invalid',
                'official_run_valid',
                'common_complete_sample_definition',
            }
        ]
        _atomic_dataframe_to_csv(df[common_columns], common_csv)
        print(f"\n💾 数据已保存: {csv_file}")
        print(f"💾 共同完整样本数据已保存: {common_csv}")
        print(f"\n{df.to_string(index=False)}")

        # 设置图表输出路径
        if args.figure_output:
            figure_path = args.figure_output
        else:
            figure_path = Path(args.output_dir) / 'acceptance_ratio_figure.png'

        common_complete_failed = (
            suppress_formal_figures_for_incomplete_common_sample(
                df,
                args.require_common_complete,
                args.output_dir,
                args.figure_output,
            )
        )
        if common_complete_failed:
            print(
                '❌ 正式运行无效：存在非共同完整任务集；已保留诊断CSV，'
                '未生成正式图',
                file=sys.stderr,
            )
            sys.exit(2)

        # Only valid formal runs reach the figure-data boundary.
        plot_data = FigureGenerator.load_data_from_csv(csv_file)

    elif args.csv:
        # 从CSV加载数据
        from scripts.experiment_analysis import (
            diagnostic_output_directory, finalize_diagnostic_outputs,
            validate_attested_analyzer_input,
        )
        if args.allow_unattested_diagnostic_input:
            diagnostic_csv_mode = True
            args.output_dir = str(
                diagnostic_output_directory(args.output_dir)
            )
            args.figure_output = None
        else:
            validate_attested_analyzer_input(
                args.csv,
                allow_primary=False,
                require_source_equivalent_derived=True,
            )
        print(f"📂 从CSV文件加载数据: {args.csv}")
        plot_data = FigureGenerator.load_data_from_csv(args.csv)
        print(f"✅ 成功加载 {len(plot_data)} 个算法的数据")

    else:
        print("❌ 错误：必须指定 --run-experiment 或 --csv")
        print("使用 --help 查看帮助信息")
        sys.exit(1)

    # 生成图表
    print("\n🎨 正在生成图表...")

    # 生成分组图表（6张）
    if not args.no_group_figures:
        figures_dir = Path(args.output_dir) / 'figures'
        FigureGenerator.plot_all_groups(plot_data, figures_dir, args.x_label)

    # 生成综合图表
    if args.figure_output:
        figure_path = args.figure_output
    else:
        figure_path = Path(args.output_dir) / 'acceptance_ratio_all.png'

    FigureGenerator.plot_acceptance_ratio(plot_data, figure_path, args.x_label)
    FigureGenerator.print_data_summary(plot_data)

    if diagnostic_csv_mode:
        from scripts.experiment_analysis import finalize_diagnostic_outputs
        finalize_diagnostic_outputs(args.output_dir)

    print(f"\n✅ 完成！")

if __name__ == '__main__':
    main()
