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
import subprocess
import time
import yaml
import os
import sys
import argparse
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
SIMULATOR = './build/rtsim/rtsim'
RTA_TOOL = str(Path(__file__).resolve().parent / 'asap_block_rta.py')
ASAP_BLOCK_ALGORITHM = 'gpfp_asap_block'
RTA_VERSION = 'v20.4'

PER_TASKSET_RESULT_FIELDS = [
    'experiment_id',
    'run_id',
    'output_dir',
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
    'taskset_id',
    'algorithm',
    'algorithm_display_name',
    'num_tasks',
    'num_cores',
    'battery',
    'initial_energy',
    'initial_energy_ratio',
    'solar_time_ms',
    'harvesting_profile',
    'harvesting_scale',
    'simulation_horizon_ms',
    'accepted',
    'rejected',
    'timeout',
    'error',
    'status',
    'reason',
    'trace_path',
    'rta_enabled',
    'rta_version',
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
    'config_id',
    'tightness',
]


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
        'rta_version': RTA_VERSION,
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
                       initial_energy=0.0, profile_rta=False):
    """Run the offline checker only for ASAP-BLOCK."""
    if algorithm != ASAP_BLOCK_ALGORITHM:
        return _base_rta_result(status='not_applicable')

    result = _base_rta_result(status='rta_error')
    result.update({
        'rta_enabled': True,
        'rta_horizon_ms': horizon_ms,
        'rta_initial_energy': float(initial_energy),
        'rta_system_config': str(Path(system_config).resolve()),
    })

    try:
        result['rta_system_config_hash'] = hash_file(system_config)
        if horizon_ms is None or int(horizon_ms) <= 0:
            raise ValueError('RTA horizon must be explicitly positive')

        cmd = [
            'python3',
            RTA_TOOL,
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

    env = os.environ.copy()
    lib_path = os.path.abspath('./build/librtsim')
    env['LD_LIBRARY_PATH'] = lib_path + ':' + env.get('LD_LIBRARY_PATH', '')

    cmd = [
        SIMULATOR, config_file, task_file,
        str(simulation_time), '-t', str(trace_file)
    ]

    def cleanup_trace():
        """清理追踪文件"""
        if keep_traces:
            return
        try:
            if trace_file.exists():
                os.remove(str(trace_file))
        except Exception:
            pass

    acceptance_ratio = 0.0
    simulation_status = 'simulation_error'
    simulation_error = None
    max_response_by_task = {}
    first_miss = {
        'first_missed_job_release': '',
        'first_missed_deadline': '',
    }

    try:
        subprocess.run(cmd, check=True, capture_output=True, env=env, text=True, timeout=120)
        trace_parser = TraceParser(str(trace_file))
        acceptance_ratio = trace_parser.get_acceptance_ratio()
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
        simulation_status = (
            'accepted' if acceptance_ratio == 1.0 else 'rejected'
        )
    except subprocess.TimeoutExpired:
        simulation_status = 'simulation_timeout'
        simulation_error = (
            f"⏱️ 仿真超时: {algorithm}, U={utilization:.2f}, idx={task_idx}"
        )
    except subprocess.CalledProcessError as e:
        error_output = (e.stderr or e.stdout or '').strip()
        simulation_error = (
            f"❌ 仿真失败: {algorithm}, U={utilization:.2f}, idx={task_idx}"
        )
        if error_output:
            simulation_error = f"{simulation_error}\n{error_output}"
    except Exception as e:
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
        )
    elif rta_options.get('enable_rta', False):
        rta_result = _base_rta_result(status='not_applicable')
    else:
        rta_result = _base_rta_result(status='disabled')

    run_result = {
        'algorithm': algorithm,
        'utilization': float(utilization),
        'task_idx': int(task_idx),
        'task_file': str(Path(task_file).resolve()),
        'taskset_id': rta_options.get(
            'taskset_id',
            'u{:.2f}-{:03d}'.format(utilization, task_idx),
        ),
        'seed_base': rta_options.get('seed_base'),
        'taskset_seed': rta_options.get('taskset_seed'),
        'seed': rta_options.get('taskset_seed'),
        'simulation_acceptance': float(acceptance_ratio),
        'acceptance_ratio': float(acceptance_ratio),
        'simulation_status': simulation_status,
        'simulation_error': simulation_error,
        'trace_path': (
            str(trace_file.resolve())
            if keep_traces and trace_file.exists()
            else ''
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

    # 实验参数
    parser.add_argument('--output-dir', type=str,
                       default=build_default_output_dir(),
                       help='输出目录（默认自动生成唯一run目录）')
    parser.add_argument('--overwrite', action='store_true',
                       help='允许覆盖已有输出目录中的正式实验结果')
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
class TraceParser:
    """解析仿真追踪文件，提取性能指标（二元可调度性）"""

    def __init__(self, trace_file: str):
        self.trace_file = trace_file
        self.events = []
        self._load_data()

    def _load_data(self):
        """加载JSON追踪文件"""
        try:
            with open(self.trace_file, 'r') as f:
                data = json.load(f)
                self.events = data.get('events', [])
        except Exception as e:
            print(f"⚠️ 加载追踪文件失败 {self.trace_file}: {e}")
            self.events = []

    def get_acceptance_ratio(self, expected_sim_time=30000):
        """
        计算二元可调度性，采用"没有消息就是好消息"原则

        逻辑：
        - 如果发生任何异常（如文件损坏）-> 返回 0.0（失败）
        - 如果追踪文件为空或无效 -> 返回 0.0（失败）
        - 如果不存在任何 'arrival' -> 返回 0.0（无效测试）
        - 如果仿真实际运行时间 < 预期时长的 95%，判定为引擎崩溃 -> 返回 0.0
        - 只要出现一次 'dline_miss'，立刻返回 0.0（一票否决）
        - 遍历全程没有 'dline_miss'，且存在至少一次 'arrival'，且引擎未崩溃 -> 返回 1.0

        参数：
            expected_sim_time: 预期仿真总时长（毫秒），默认 30000ms

        说明：
            - 二元判定，只返回 0.0 或 1.0
            - 不再维护 open_jobs 集合，避免长周期任务的边界伪下降
        """
        CRASH_THRESHOLD_RATIO = 0.95  # 崩溃检测阈值

        try:
            if not self.events:
                return 0.0

            crash_threshold = expected_sim_time * CRASH_THRESHOLD_RATIO

            # 获取实际仿真结束时间
            last_time = max(float(e.get('time', 0)) for e in self.events)

            # 引擎崩溃检测
            if last_time < crash_threshold:
                return 0.0

            has_arrivals = False

            for event in self.events:
                event_type = event.get('event_type', '')

                if event_type == 'arrival':
                    has_arrivals = True
                elif event_type == 'dline_miss':
                    # 一票否决：任何 deadline miss 都判定为失败
                    return 0.0

            if not has_arrivals:
                return 0.0

            # 无 dline_miss、有 arrival、引擎未崩溃 -> 成功
            return 1.0

        except Exception as e:
            # 任何异常（文件损坏、解析错误等）都当作失败
            print(f"⚠️ 解析追踪文件异常 {self.trace_file}: {e}")
            return 0.0

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
                 keep_traces=False):
        self.output_dir = Path(output_dir)
        self.trace_dir = self.output_dir / 'traces'
        self.task_dir = self.output_dir / 'tasks'

        # 创建目录
        for p in [self.output_dir, self.trace_dir, self.task_dir]:
            p.mkdir(parents=True, exist_ok=True)

        # 实验参数
        self.utilization_points = utilization_points
        self.num_tasksets = num_tasksets
        self.task_n = task_n
        self.task_p_min = task_p_min
        self.task_p_max = task_p_max
        self.simulation_time = simulation_time
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

            updated_lines.append(line)

        temp_config = self.output_dir / f'config_{algorithm}.yml'
        with open(temp_config, 'w', encoding='utf-8') as f:
            f.writelines(updated_lines)
        return str(temp_config)

    def run_simulation(self, algorithm, config_file, task_file, utilization, task_idx):
        """运行单次仿真"""
        trace_file = self.trace_dir / f'trace_{algorithm}_u{utilization:.2f}_{task_idx:03d}.json'

        env = os.environ.copy()
        lib_path = os.path.abspath('./build/librtsim')
        env['LD_LIBRARY_PATH'] = lib_path + ':' + env.get('LD_LIBRARY_PATH', '')

        cmd = [
            SIMULATOR, config_file, task_file,
            str(self.simulation_time), '-t', str(trace_file)
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, env=env, text=True, timeout=120)
            return str(trace_file)
        except subprocess.TimeoutExpired:
            print(f"⏱️ 仿真超时: {algorithm}, U={utilization:.2f}, idx={task_idx}")
            return None
        except subprocess.CalledProcessError as e:
            error_output = (e.stderr or e.stdout or '').strip()
            print(f"❌ 仿真失败: {algorithm}, U={utilization:.2f}, idx={task_idx}")
            if error_output:
                print(error_output)
            return None

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
                            'task_file': '',
                            'taskset_id': self.taskset_id(
                                utilization, task_idx
                            ),
                            'seed_base': self.seed_base,
                            'taskset_seed': seed,
                            'seed': seed,
                            **planned_metadata,
                            'simulation_acceptance': 0.0,
                            'acceptance_ratio': 0.0,
                            'simulation_status': 'yaml_generation_failed',
                            'simulation_error': 'taskset generation failed',
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
                            'taskset_metadata': taskset_metadata,
                            'keep_traces': self.keep_traces,
                        },
                    ))

        count = 0
        rta_output = None
        try:
            if self.enable_rta:
                rta_output = open(
                    self.rta_results_file, 'w', encoding='utf-8'
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
                rta_output.close()

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
        simulation_reason = result.get('simulation_error') or ''
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
        config_id = '{}|M={}|n={}|util={:.2f}|E0={}|seed={}'.format(
            self.experiment_id,
            self.system_cores,
            self.task_n,
            float(utilization),
            result.get('rta_initial_energy', self.rta_initial_energy),
            taskset_seed,
        )

        return {
            'experiment_id': self.experiment_id,
            'run_id': self.experiment_id,
            'output_dir': str(self.output_dir),
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
            'taskset_id': result.get('taskset_id', ''),
            'algorithm': algorithm,
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
            'simulation_horizon_ms': self.simulation_time,
            'accepted': int(status == 'accepted'),
            'rejected': int(status == 'rejected'),
            'timeout': int(status == 'timeout'),
            'error': int(status == 'error'),
            'status': status,
            'reason': simulation_reason,
            'trace_path': result.get('trace_path', ''),
            'rta_enabled': bool(result.get('rta_enabled', False)),
            'rta_version': result.get('rta_version', RTA_VERSION),
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
            'config_id': config_id,
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
        return rows

    def write_per_taskset_results(self, results):
        """Write one row per scheduler/taskset simulation outcome."""
        rows = self.per_taskset_result_rows(results)
        frame = pd.DataFrame(rows, columns=PER_TASKSET_RESULT_FIELDS)
        frame.to_csv(self.per_taskset_results_file, index=False)
        print(
            'Per-taskset results saved: {} ({} rows)'.format(
                self.per_taskset_results_file, len(frame)
            )
        )
        return frame

    def aggregate_results(self, results):
        """
        聚合结果：计算每个利用率点的平均接受率

        注意：这里的平均是对二元值（0.0或1.0）求平均
        例如：[1, 1, 0, 1, 0] 的平均值是 0.6，表示60%的任务集可调度
        """
        data = []
        for algo in ALGORITHMS:
            for utilization in self.utilization_points:
                run_results = results[algo][utilization]
                if run_results:
                    acceptance_ratios = [
                        (
                            result.get('acceptance_ratio', 0.0)
                            if isinstance(result, dict)
                            else result
                        )
                        for result in run_results
                    ]
                    # 计算平均接受率（即可调度任务集的比例）
                    avg_acceptance = np.mean(acceptance_ratios)
                    status_buckets = [
                        classify_simulation_status(result)
                        for result in run_results
                    ]
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
                        'algorithm': algo,
                        'algorithm_display_name': ALGO_DISPLAY_NAMES.get(
                            algo, algo
                        ),
                        'normalized_utilization': utilization,
                        'acceptance_ratio': avg_acceptance,
                        'num_samples': len(acceptance_ratios),
                        'num_successful': int(sum(acceptance_ratios)),
                        'seed_base': self.seed_base,
                        'taskset_count': self.num_tasksets,
                        'core_count': self.system_cores,
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
                        'harvesting_profile': self.harvesting_profile(),
                        'harvesting_scale': self.harvesting_scale,
                        'rta_version': RTA_VERSION,
                        'simulation_num_accepted': status_buckets.count(
                            'accepted'
                        ),
                        'simulation_num_rejected': status_buckets.count(
                            'rejected'
                        ),
                        'simulation_num_timeout': status_buckets.count(
                            'timeout'
                        ),
                        'simulation_num_error': status_buckets.count(
                            'error'
                        ),
                    }

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

# ============================================
# 图表生成器
# ============================================
class FigureGenerator:
    """生成IEEE Transaction风格的接受率图表"""

    @staticmethod
    def load_data_from_csv(csv_path):
        """从CSV文件加载数据"""
        df = pd.read_csv(csv_path)

        results = {}
        for internal_name, display_name in ALGO_DISPLAY_NAMES.items():
            algo_data = df[df['algorithm'] == internal_name]

            if not algo_data.empty:
                algo_data = algo_data.sort_values('normalized_utilization')
                x = algo_data['normalized_utilization'].values
                y = algo_data['acceptance_ratio'].values
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
        """
    )

    add_experiment_cli_args(parser)

    args = parser.parse_args()
    validate_rta_cli_args(parser, args)
    if args.run_experiment:
        validate_output_dir_args(parser, args)

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
            simulation_time=DEFAULT_SIMULATION_TIME,
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
        )

        results = runner.run_experiments()
        df = runner.aggregate_results(results)

        if df.empty:
            print("\n❌ 没有产生有效数据")
            sys.exit(1)

        # 保存数据
        csv_file = Path(args.output_dir) / 'acceptance_ratio_data.csv'
        df.to_csv(csv_file, index=False)
        print(f"\n💾 数据已保存: {csv_file}")
        print(f"\n{df.to_string(index=False)}")

        # 设置图表输出路径
        if args.figure_output:
            figure_path = args.figure_output
        else:
            figure_path = Path(args.output_dir) / 'acceptance_ratio_figure.png'

        # 从CSV加载数据用于绘图
        plot_data = FigureGenerator.load_data_from_csv(csv_file)

    elif args.csv:
        # 从CSV加载数据
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

    print(f"\n✅ 完成！")

if __name__ == '__main__':
    main()
