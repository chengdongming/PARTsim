#!/usr/bin/env python3
"""
全局调度任务集生成器 - 能量感知版本
集成系统初始能量和能量收集接口
增加随机到达时间偏移功能
增强版：完全集成系统配置文件中的功耗模型参数
"""

import random
import argparse
import hashlib
import json
import math
import sys
import yaml
import os
from typing import List, Dict, Set, Tuple, Optional, Any, Sequence
from collections import defaultdict
from datetime import datetime

# 统一日志系统
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from utils.unified_logger import get_taskgen_logger
    logger = get_taskgen_logger()
    logger.info("全局任务生成器启动 - 使用统一日志系统")
except ImportError:
    import logging
    logger = logging.getLogger("task_generator")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s] %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.warning("无法导入统一日志系统，使用标准日志")

DEFAULT_POWER_COEFFICIENTS = {
    "bzip2": 1.2,
    "hash": 0.8,
    "encrypt": 1.5,
    "decrypt": 1.5,
    "control": 0.1,
    "idle": 0.1,
}

DEFAULT_FREQUENCY_POWER_RATIOS = {
    7000: 0.85,
    7500: 0.88,
    8000: 0.92,
    8100: 0.93,
    8200: 0.94,
    8300: 0.95,
    8400: 0.96,
    8500: 0.97,
    9000: 1.00,
    9500: 1.05,
    10000: 1.10,
    10500: 1.15,
}

TASK_WORKLOAD_CONTRACT_VERSION = "REAL_TIME_TASK_WORKLOAD_CONTRACT_V2"
TASK_WORKLOAD_CANDIDATE_DOMAIN = (
    "ASAP_BLOCK:V9.3:REAL_TIME_TASK_WORKLOAD_CANDIDATES:v2"
)


def _task_workload_candidate_identity(candidates: Sequence[str]) -> str:
    material = {
        "version": TASK_WORKLOAD_CONTRACT_VERSION,
        "ordered_candidates": list(candidates),
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(
        TASK_WORKLOAD_CANDIDATE_DOMAIN.encode("ascii") + b"\0" + encoded
    ).hexdigest()


def _normalise_energy_model(model: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise model values so canonical and legacy configs can be compared."""
    if not isinstance(model, dict):
        return {}

    frequencies = model.get(
        "frequency_power_ratios",
        model.get("frequency_scaling", {}),
    )
    return {
        "base_power": (
            float(model["base_power"]) if "base_power" in model else None
        ),
        "workload_coefficients": {
            str(key): float(value)
            for key, value in model.get("workload_coefficients", {}).items()
        },
        "frequency_power_ratios": {
            int(key): float(value) for key, value in frequencies.items()
        },
    }


def _resolve_scheduler_energy_model(energy_config: Dict[str, Any]) -> Dict[str, Any]:
    """Select the canonical scheduler model with legacy compatibility."""
    canonical = energy_config.get("scheduler_energy_model", {})
    legacy = energy_config.get("consumption_model", {})

    if canonical and legacy:
        if _normalise_energy_model(canonical) != _normalise_energy_model(legacy):
            logger.warning(
                "scheduler_energy_model与consumption_model配置不同；"
                "使用scheduler_energy_model"
            )
        return canonical
    return canonical or legacy or {}


def _resolve_frequency_ratios(model: Dict[str, Any]) -> Dict[int, float]:
    """Prefer frequency_power_ratios and fall back to frequency_scaling."""
    canonical = model.get("frequency_power_ratios")
    legacy = model.get("frequency_scaling")

    if canonical is not None:
        if legacy is not None:
            canonical_values = {
                int(key): float(value) for key, value in canonical.items()
            }
            legacy_values = {
                int(key): float(value) for key, value in legacy.items()
            }
            if canonical_values != legacy_values:
                logger.warning(
                    "frequency_power_ratios与frequency_scaling配置不同；"
                    "使用frequency_power_ratios"
                )
        return {
            int(key): float(value) for key, value in canonical.items()
        }

    if legacy is not None:
        return {
            int(key): float(value) for key, value in legacy.items()
        }
    return {}


# 导入能量管理器 - 修复导入问题
try:
    from energy_manager import EnergyManager, get_energy_manager
    # 添加一个兼容性包装函数
    def create_energy_manager(system_config: Dict) -> EnergyManager:
        """创建能量管理器的兼容性函数"""
        # 如果system_config是字典，尝试提取配置文件路径
        if isinstance(system_config, dict):
            # 如果没有文件路径，返回默认管理器
            logger.warning("使用默认能量管理器，因为配置是字典而不是文件路径")
            return get_energy_manager()
        else:
            # 假设是文件路径
            return get_energy_manager(system_config)
except ImportError:
    logger.warning("无法导入能量管理器，使用简化版本")
    # 创建简化版本
    class SimpleEnergyManager:
        def __init__(self, system_config=None):
            self.system_config = system_config or {}
            energy_config = self.system_config.get('energy_management', {})
            self.initial_energy = energy_config.get('initial_energy', 200.0)
            self.max_energy = energy_config.get('max_energy', 600.0)
            self.current_energy = self.initial_energy
            self.unit_time = energy_config.get('unit_time', 50)
            
            # 工作负载功率系数
            scheduler_model = _resolve_scheduler_energy_model(energy_config)
            self.base_power = scheduler_model.get('base_power', 0.5)
            self.workload_coefficients = scheduler_model.get(
                'workload_coefficients',
                dict(DEFAULT_POWER_COEFFICIENTS),
            )
            
            logger.info(f"简单能量管理器初始化完成 - 初始能量: {self.initial_energy}J")
        
        def has_sufficient_energy(self, required_energy: float) -> bool:
            """检查是否有足够能量"""
            # 预留10%的能量作为系统开销
            safety_margin = self.current_energy * 0.1
            available_energy = self.current_energy - safety_margin
            return available_energy >= required_energy
        
        def consume_energy(self, energy: float) -> bool:
            """消耗能量"""
            if self.current_energy >= energy:
                self.current_energy -= energy
                return True
            return False
        
        def get_energy_status(self) -> Dict[str, Any]:
            """获取能量状态"""
            return {
                'initial_energy': self.initial_energy,
                'max_energy': self.max_energy,
                'remaining_energy': self.current_energy,
                'energy_utilization': (self.initial_energy - self.current_energy) / self.initial_energy 
                                      if self.initial_energy > 0 else 0
            }
    
    EnergyManager = SimpleEnergyManager
    
    def create_energy_manager(system_config: Dict) -> SimpleEnergyManager:
        """创建简化能量管理器"""
        return SimpleEnergyManager(system_config)


class UUniFastDiscard:
    """UUniFast-Discard算法实现"""
    
    def __init__(self, seed=None):
        if seed is not None:
            random.seed(seed)
    
    def generate(
        self,
        n: int,
        U: float,
        min_task_util: float = 0.01,
        max_task_util: float = 0.8,
        max_trials: int = 1000,
    ) -> List[float]:
        if n <= 0 or U <= 0 or U > n:
            raise ValueError(f"Invalid parameters: n={n}, U={U}")
        if min_task_util < 0 or max_task_util <= 0:
            raise ValueError(
                f"Invalid utilization bounds: min={min_task_util}, "
                f"max={max_task_util}"
            )
        if min_task_util > max_task_util:
            raise ValueError(
                f"Invalid utilization bounds: min={min_task_util}, "
                f"max={max_task_util}"
            )
        if max_task_util > 1.0:
            raise ValueError(
                f"Sequential task utilization bound must be <= 1.0, "
                f"got {max_task_util}"
            )
        if U > n * max_task_util:
            raise ValueError(
                "Infeasible utilization: total utilization {} exceeds "
                "n * max_task_util = {}".format(U, n * max_task_util)
            )
        if U < n * min_task_util:
            raise ValueError(
                "Infeasible utilization: total utilization {} is below "
                "n * min_task_util = {}".format(U, n * min_task_util)
            )
        
        for trial in range(max_trials):
            utilizations = []
            sumU = U
            valid = True
            
            for i in range(n - 1):
                nextSumU = sumU * (random.random() ** (1.0 / (n - i)))
                util_i = sumU - nextSumU
                utilizations.append(util_i)
                sumU = nextSumU
                
                if util_i < min_task_util or util_i > max_task_util:
                    valid = False
                    break
            
            utilizations.append(sumU)
            
            if (
                valid
                and min_task_util <= utilizations[-1] <= max_task_util
            ):
                return utilizations
        
        raise RuntimeError(f"Failed after {max_trials} trials")


class DAGGenerator:
    """DAG生成器"""
    
    def __init__(self, seed=None):
        if seed is not None:
            random.seed(seed)
    
    def generate_layered_dag(self, n: int, edge_prob: float = 0.4, 
                           max_in_degree: int = 3, max_out_degree: int = 3) -> Dict[int, Set[int]]:
        """使用分层方法生成DAG"""
        dag = {}
        
        # 创建分层结构
        layers = []
        remaining = n
        layer_sizes = []
        
        while remaining > 0:
            max_layer_size = min(4, remaining)
            layer_size = random.randint(1, max_layer_size)
            layer_sizes.append(layer_size)
            remaining -= layer_size
        
        # 构建层
        node_idx = 0
        for size in layer_sizes:
            layer = list(range(node_idx, node_idx + size))
            layers.append(layer)
            node_idx += size
        
        # 初始化DAG
        for i in range(n):
            dag[i] = set()
        
        # 在相邻层之间创建边
        for layer_idx in range(len(layers) - 1):
            current_layer = layers[layer_idx]
            next_layer = layers[layer_idx + 1]
            
            for node in current_layer:
                if not dag[node] and random.random() < 0.8:
                    max_possible = min(max_out_degree, len(next_layer))
                    if max_possible > 0:
                        num_edges = random.randint(1, max_possible)
                        successors = random.sample(next_layer, num_edges)
                        dag[node].update(successors)
                elif random.random() < edge_prob:
                    max_possible = min(max_out_degree, len(next_layer))
                    if max_possible > 0:
                        num_edges = random.randint(1, max_possible)
                        successors = random.sample(next_layer, num_edges)
                        dag[node].update(successors)
        
        return dag
    
    def get_predecessors(self, dag: Dict[int, Set[int]]) -> Dict[int, Set[int]]:
        """获取每个节点的前驱节点"""
        predecessors = defaultdict(set)
        for node, successors in dag.items():
            for succ in successors:
                predecessors[succ].add(node)
        return dict(predecessors)
    
    def get_roots(self, predecessors: Dict[int, Set[int]], n: int) -> List[int]:
        """获取根节点"""
        return [i for i in range(n) if i not in predecessors or not predecessors[i]]
    
    def get_leaves(self, dag: Dict[int, Set[int]], n: int) -> List[int]:
        """获取叶节点"""
        return [i for i in range(n) if i not in dag or not dag[i]]


class EnergyAwareTaskGenerator:
    """能量感知任务生成器 - 增强版"""
    
    def __init__(self, seed=None, energy_manager: EnergyManager = None,
                 system_config_path: str = None,
                 task_workload_candidates: Optional[Sequence[str]] = None):
        self.uunifast = UUniFastDiscard(seed)
        self.dag_generator = DAGGenerator(seed)
        self.energy_manager = energy_manager
        self.system_config_path = system_config_path
        
        # 从系统配置加载能量参数
        self.system_config = self._load_system_config(system_config_path)
        self.energy_config = self._extract_energy_config()
        self.scheduler_energy_model = _resolve_scheduler_energy_model(
            self.energy_config
        )
        
        # 从配置中加载功率参数
        self.power_coefficients = self._load_power_coefficients()
        self.workload_types = sorted(self.power_coefficients)
        self.task_workload_candidates = self._resolve_task_workload_candidates(
            task_workload_candidates
        )
        self.base_power = self._load_base_power()
        self.frequency_power_ratios = self._load_frequency_ratios()

        self.base_frequency = self._load_base_frequency()
        
        # 调试模式
        self._debug = False
        
        if seed is not None:
            random.seed(seed)
        
        logger.info(f"能量感知生成器初始化完成")
        logger.info(f"  工作负载类型: {len(self.workload_types)} 种")
        logger.info(f"  基础功耗: {self.base_power} W")
        logger.info(f"  基础频率: {self.base_frequency} MHz")

    def _resolve_task_workload_candidates(
            self, configured: Optional[Sequence[str]]) -> Tuple[str, ...]:
        """Freeze the lexical non-idle pool used for real-time tasks."""

        if configured is None:
            candidates = tuple(sorted(
                name for name in self.workload_types if name != "idle"
            ))
            if not candidates:
                raise ValueError(
                    "configured power model has no non-idle task workloads"
                )
            return candidates
        candidates = tuple(str(value) for value in configured)
        if not candidates:
            raise ValueError("task workload candidate pool must not be empty")
        if any(not value or value.strip() != value for value in candidates):
            raise ValueError("task workload candidates must be non-empty canonical names")
        if len(candidates) != len(set(candidates)):
            raise ValueError("task workload candidates must be unique")
        if "idle" in candidates:
            raise ValueError("idle is a system state, not a task workload candidate")
        if candidates != tuple(sorted(candidates)):
            raise ValueError("task workload candidates must use stable lexical order")
        unknown = sorted(set(candidates) - set(self.workload_types))
        if unknown:
            raise ValueError(
                f"task workload candidates are absent from the configured model: {unknown}"
            )
        return candidates
    
    def _load_system_config(self, config_path: str) -> Dict:
        """加载系统配置文件"""
        if not config_path or not os.path.exists(config_path):
            logger.warning(f"系统配置文件不存在: {config_path}")
            return {}
        
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                logger.info(f"成功加载系统配置文件: {config_path}")
                return config
        except Exception as e:
            logger.error(f"无法加载系统配置文件 {config_path}: {e}")
            return {}
    
    def _extract_energy_config(self) -> Dict:
        """提取能量管理配置"""
        if not self.system_config:
            return {}
        
        return self.system_config.get('energy_management', {})
    
    def _get_workload_types(self) -> List[str]:
        """从系统配置中获取工作负载类型"""
        workload_coeffs = self.scheduler_energy_model.get(
            'workload_coefficients',
            {},
        )
        
        if workload_coeffs:
            return list(workload_coeffs.keys())
        
        # 默认工作负载类型
        return list(DEFAULT_POWER_COEFFICIENTS.keys())
    
    def _load_power_coefficients(self) -> Dict[str, float]:
        """从配置加载功率系数"""
        workload_coeffs = self.scheduler_energy_model.get(
            'workload_coefficients',
            {},
        )
        
        if workload_coeffs:
            coefficients = dict(DEFAULT_POWER_COEFFICIENTS)
            for workload, coeff in workload_coeffs.items():
                coefficients[workload] = float(coeff)
            return coefficients
        
        return dict(DEFAULT_POWER_COEFFICIENTS)
    
    def _load_base_power(self) -> float:
        """从配置加载基础功耗"""
        return float(self.scheduler_energy_model.get('base_power', 0.5))
    
    def _load_frequency_ratios(self) -> Dict[int, float]:
        """从配置加载频率功率比"""
        configured = _resolve_frequency_ratios(self.scheduler_energy_model)
        if configured:
            ratios = dict(DEFAULT_FREQUENCY_POWER_RATIOS)
            ratios.update(configured)
            return ratios
        return dict(DEFAULT_FREQUENCY_POWER_RATIOS)

    def _load_base_frequency(self) -> float:
        """Load the scheduler frequency used by C++ from the first CPU island."""
        cpu_islands = self.system_config.get('cpu_islands', [])
        if cpu_islands:
            return float(cpu_islands[0].get('base_freq', 8100.0))
        return 8100.0
    
    def get_frequency_ratio(self, frequency_mhz: float = 8100.0) -> float:
        """获取频率功率比例系数"""
        if not self.frequency_power_ratios:
            return 1.0
        
        # 找到最接近的频率
        closest_freq = min(self.frequency_power_ratios.keys(), 
                          key=lambda f: abs(f - frequency_mhz))
        return self.frequency_power_ratios.get(closest_freq, 1.0)
    
    def calculate_energy(self, execution_time_ms: float,
                        workload_type: str,
                        frequency_mhz: float = 8100.0) -> float:
        """
        计算能量消耗（焦耳）- 使用系统配置参数
        
        公式：能量(J) = 基础功耗(W) × 工作负载系数 × 频率比例 × 时间(s)
        """
        execution_time_s = execution_time_ms / 1000.0
        
        # 获取工作负载功率系数
        workload_power = self.power_coefficients.get(workload_type, 1.0)
        
        # 获取频率功率比例
        frequency_ratio = self.get_frequency_ratio(frequency_mhz)
        
        # 计算总功率
        total_power_watts = self.base_power * workload_power * frequency_ratio
        
        # 计算能量
        energy_joules = total_power_watts * execution_time_s
        
        # 调试输出
        if self._debug:
            logger.debug(f"能量计算: {execution_time_ms}ms, {workload_type}, "
                         f"功率={total_power_watts:.3f}W, 能量={energy_joules:.6f}J")
        
        return energy_joules

    @staticmethod
    def _round_execution_time(raw_execution_time: float, wcet_rounding: str) -> int:
        if wcet_rounding == "ceil":
            return math.ceil(raw_execution_time)
        if wcet_rounding == "round":
            return round(raw_execution_time)
        return math.floor(raw_execution_time)

    @staticmethod
    def _execution_time_cap(period: int, max_task_util: float) -> int:
        return max(1, min(int(period), int(math.floor(float(period) * max_task_util))))

    @classmethod
    def _compensated_execution_times(
        cls,
        utilizations: List[float],
        periods: List[int],
        max_task_util: float,
        target_total_utilization: float,
    ) -> List[int]:
        execution_times = []
        fractional_remainders = []
        for util, period in zip(utilizations, periods):
            raw_execution_time = float(util) * int(period)
            floor_time = math.floor(raw_execution_time)
            cap = cls._execution_time_cap(period, max_task_util)
            execution_time = min(max(1, int(floor_time)), cap)
            execution_times.append(execution_time)
            fractional_remainders.append(raw_execution_time - floor_time)

        actual_total = sum(
            execution_time / period
            for execution_time, period in zip(execution_times, periods)
        )
        if actual_total >= target_total_utilization:
            return execution_times

        ordered_indices = sorted(
            range(len(execution_times)),
            key=lambda index: (
                fractional_remainders[index],
                1.0 / periods[index],
                -index,
            ),
            reverse=True,
        )

        while actual_total < target_total_utilization:
            current_error = abs(actual_total - target_total_utilization)
            best_index = None
            best_error = current_error
            best_actual = actual_total

            for index in ordered_indices:
                period = periods[index]
                cap = cls._execution_time_cap(period, max_task_util)
                if execution_times[index] >= cap:
                    continue
                candidate_actual = actual_total + (1.0 / period)
                candidate_error = abs(
                    candidate_actual - target_total_utilization
                )
                if candidate_error + 1e-15 < best_error:
                    best_index = index
                    best_error = candidate_error
                    best_actual = candidate_actual

            if best_index is None:
                break
            execution_times[best_index] += 1
            actual_total = best_actual

        return execution_times
    
    def generate_taskset(self, n: int, total_utilization: float, 
                        min_period: int = 1000, max_period: int = 5000,
                        num_cpus: int = 4, implicit_deadline: bool = True,
                        dag_enabled: bool = True, edge_prob: float = 0.4,
                        energy_aware: bool = True,
                        arrival_offset: bool = True, 
                        max_arrival_offset: int = None,
                        min_task_util: float = 0.01,
                        max_task_util: float = 0.8,
                        wcet_rounding: str = "floor",
                        actual_utilization_tolerance_total: float = None
                        ) -> Tuple[List[Dict], List[Dict], Dict, Dict]:
        """
        生成能量感知任务集 - 增强版
        
        关键修改：
        1. 使用系统配置中的能量参数计算能耗
        2. runtime参数作为仿真中的执行时间，不需要再次计算
        3. 计算每个任务的精确能耗
        """
        if wcet_rounding not in {"floor", "round", "ceil", "compensated"}:
            raise ValueError(f"Unsupported wcet_rounding: {wcet_rounding}")
        if actual_utilization_tolerance_total is not None:
            actual_utilization_tolerance_total = float(
                actual_utilization_tolerance_total
            )
            if (
                not math.isfinite(actual_utilization_tolerance_total)
                or actual_utilization_tolerance_total < 0
            ):
                raise ValueError(
                    "actual_utilization_tolerance_total must be finite and non-negative"
                )
        if min_period <= 0 or max_period <= 0 or min_period > max_period:
            raise ValueError(
                f"Invalid period range: min_period={min_period}, max_period={max_period}"
            )
        if math.floor(float(min_period) * max_task_util) < 1:
            raise ValueError(
                "Infeasible integer taskset: max_task_util={} is too small "
                "for runtime>=1 with min_period={}".format(
                    max_task_util, min_period
                )
            )
        integer_min_total = n / float(max_period)
        if total_utilization < integer_min_total:
            raise ValueError(
                "Infeasible integer taskset: target total utilization {} is below "
                "minimum realized utilization {} from runtime>=1 and max_period={}".format(
                    total_utilization, integer_min_total, max_period
                )
            )
        integer_max_total = n * max_task_util
        if total_utilization > integer_max_total:
            raise ValueError(
                "Infeasible integer taskset: target total utilization {} exceeds "
                "n * max_task_util = {}".format(total_utilization, integer_max_total)
            )

        max_generation_trials = 1000 if actual_utilization_tolerance_total is not None else 1
        last_error = None

        for _generation_trial in range(max_generation_trials):
            # 生成利用率
            utilizations = self.uunifast.generate(
                n,
                total_utilization,
                min_task_util=min_task_util,
                max_task_util=max_task_util,
            )

            # 生成DAG结构（如果需要）
            dag = {}
            predecessors = {}
            if dag_enabled:
                dag = self.dag_generator.generate_layered_dag(n, edge_prob)
                predecessors = self.dag_generator.get_predecessors(dag)

            periods = [random.randint(min_period, max_period) for _ in range(n)]
            if wcet_rounding == "compensated":
                execution_times = self._compensated_execution_times(
                    utilizations,
                    periods,
                    max_task_util,
                    total_utilization,
                )
            else:
                execution_times = []
                for util, period in zip(utilizations, periods):
                    raw_execution_time = util * period
                    rounded_execution_time = self._round_execution_time(
                        raw_execution_time,
                        wcet_rounding,
                    )
                    execution_time = max(1, int(rounded_execution_time))
                    execution_time = min(
                        execution_time,
                        self._execution_time_cap(period, max_task_util),
                    )
                    execution_times.append(execution_time)

            # 生成任务
            tasks = []
            total_energy = 0.0
            energy_constrained = False

            for i, util in enumerate(utilizations):
                period = periods[i]
                execution_time = execution_times[i]

                # 生成截止时间
                if implicit_deadline:
                    deadline = period
                else:
                    # 约束截止时间：execution_time <= deadline < period
                    # 在 [execution_time, period-1] 范围内随机生成
                    if execution_time >= period:
                        deadline = period
                    else:
                        deadline = random.randint(execution_time, period - 1)

                # 生成到达时间偏移
                arrival_offset_value = 0
                if arrival_offset:
                    if max_arrival_offset is None:
                        max_arrival_offset_value = int(period * 0.3)
                    else:
                        max_arrival_offset_value = max_arrival_offset

                    if max_arrival_offset_value > 0:
                        arrival_offset_value = random.randint(0, max_arrival_offset_value)

                # 选择工作负载类型 - 均匀分布（所有候选类型概率相同）
                workload = random.choice(self.task_workload_candidates)

                # 计算能耗 - 使用系统配置参数
                energy = self.calculate_energy(execution_time, workload, self.base_frequency)

                # 能量感知检查
                if energy_aware and self.energy_manager:
                    # 检查能量是否足够执行这个任务
                    required_energy_with_margin = energy * 1.1  # 10%安全余量

                    # 检查能量管理器是否有足够能量
                    has_sufficient = False
                    if hasattr(self.energy_manager, 'has_sufficient_energy'):
                        has_sufficient = self.energy_manager.has_sufficient_energy(required_energy_with_margin)
                    elif hasattr(self.energy_manager, 'current_energy'):
                        has_sufficient = self.energy_manager.current_energy >= required_energy_with_margin

                    if not has_sufficient:
                        # 能量不足，调整执行时间
                        energy_constrained = True
                        if hasattr(self.energy_manager, 'current_energy'):
                            max_energy = self.energy_manager.current_energy
                            # 按比例缩减执行时间，保留10%余量
                            scale_factor = max_energy / (energy * 1.1)
                            execution_time = max(50, int(execution_time * scale_factor))
                            execution_time = min(
                                execution_time,
                                self._execution_time_cap(period, max_task_util),
                            )
                            deadline = max(deadline, execution_time)
                            deadline = min(deadline, period)
                            energy = self.calculate_energy(execution_time, workload, self.base_frequency)
                            logger.warning(f"任务 {i} 因能量限制调整执行时间: {execution_time}ms")

                total_energy += energy

                # 构建任务代码
                code = []

                # 如果有前驱，添加lock操作
                if dag_enabled and i in predecessors:
                    for pred in sorted(predecessors[i]):
                        code.append(f"lock(res_{pred}_{i})")

                # 添加执行代码 - runtime作为固定执行时间
                code.append(f"fixed({execution_time}, {workload})")

                # 如果有后继，添加unlock操作
                if dag_enabled and i in dag and dag[i]:
                    for succ in sorted(dag[i]):
                        code.append(f"unlock(res_{i}_{succ})")

                # 构建params字符串 - 完整格式：period=X,wcet=Y,arrival_offset=Z,workload=W
                params_parts = [
                    f"period={period}",
                    f"wcet={execution_time}",
                    f"arrival_offset={arrival_offset_value}",
                    f"workload={workload}"
                ]
                params_str = ",".join(params_parts)

                task = {
                    'name': f'task_{i}',  # 任务名：task_0, task_1, task_2...
                    'iat': period,
                    'runtime': execution_time,  # 关键：runtime作为仿真中的执行时间
                    'deadline': deadline,
                    'code': code,
                    'params': params_str,
                    'utilization': util,
                    'execution_time': execution_time,
                    'workload': workload,
                    'energy': energy,
                    'arrival_offset': arrival_offset_value,
                    'frequency_mhz': self.base_frequency  # 添加频率信息
                }
                tasks.append(task)

            actual_total_utilization = sum(
                task['runtime'] / task['iat'] for task in tasks if task.get('iat', 0) > 0
            )
            last_error = actual_total_utilization - float(total_utilization)
            if (
                actual_utilization_tolerance_total is not None
                and abs(last_error) > actual_utilization_tolerance_total
            ):
                continue

            # 构建完整的任务集和资源
            if dag_enabled:
                all_tasks, resources, dag = self._build_dag_configuration(
                    tasks, dag, n, arrival_offset, max_arrival_offset
                )
                # 为DAG任务添加能耗信息
                for task in all_tasks:
                    if 'energy' not in task:
                        task['energy'] = self.calculate_energy(task.get('execution_time', 10), 'control')
                    if arrival_offset and 'arrival_offset' not in task:
                        task['arrival_offset'] = 0  # DAG开始/结束任务的到达偏移为0
            else:
                all_tasks = tasks
                resources = []

            # 能量信息 - 修复：使用正确的方式获取初始能量
            energy_info = {
                'total_energy': total_energy,
                'energy_constrained': energy_constrained,
                'initial_energy': 0.0,
                'remaining_energy': 0.0,
                'energy_utilization': 0.0
            }

            if self.energy_manager:
                # 获取初始能量 - 检查不同的属性名
                initial_energy = 0.0
                if hasattr(self.energy_manager, 'initial_energy'):
                    initial_energy = self.energy_manager.initial_energy
                elif hasattr(self.energy_manager, 'config') and hasattr(self.energy_manager.config, 'initial_energy'):
                    initial_energy = self.energy_manager.config.initial_energy

                # 获取当前能量
                current_energy = 0.0
                if hasattr(self.energy_manager, 'current_energy'):
                    current_energy = self.energy_manager.current_energy

                energy_info['initial_energy'] = initial_energy
                energy_info['remaining_energy'] = current_energy

                # 计算能量利用率
                if initial_energy > 0:
                    energy_info['energy_utilization'] = total_energy / initial_energy

            return all_tasks, resources, dag, energy_info

        raise RuntimeError(
            "Failed to generate taskset within actual utilization tolerance "
            "{} after {} trials; last error was {}".format(
                actual_utilization_tolerance_total,
                max_generation_trials,
                last_error,
            )
        )
    
    def _build_dag_configuration(self, tasks: List[Dict], dag: Dict[int, Set[int]], n: int,
                               arrival_offset: bool = True, max_arrival_offset: int = None) -> Tuple[List[Dict], List[Dict], Dict]:
        """构建DAG配置"""
        resources = []
        predecessors = self.dag_generator.get_predecessors(dag)
        
        # 获取根节点和叶节点
        roots = self.dag_generator.get_roots(predecessors, n)
        leaves = self.dag_generator.get_leaves(dag, n)
        
        # 使用第一个任务的周期作为基准
        base_period = max(tasks[0]['iat'] if tasks else 1000, 1000)
        
        # 创建DAG开始任务
        dag_begin_code = ["lock(DAG_global)"]
        for root in roots:
            resource_name = f"res_dag_begin_{root}"
            dag_begin_code.append(f"unlock({resource_name})")
            resources.append({
                'name': resource_name,
                'initial_state': 'locked'
            })
        
        # DAG开始任务的参数
        dag_begin_params = f"period={base_period}"
        if arrival_offset:
            dag_begin_params += ",arrival_offset=0"
        
        dag_begin = {
            'name': 'dag_begin',
            'iat': base_period,
            'runtime': 10,
            'deadline': 200,
            'code': dag_begin_code,
            'params': dag_begin_params,
            'execution_time': 10,
            'workload': 'control'
        }
        
        # 创建DAG结束任务
        dag_end_code = []
        for leaf in leaves:
            resource_name = f"res_{leaf}_dag_end"
            dag_end_code.append(f"lock({resource_name})")
            resources.append({
                'name': resource_name,
                'initial_state': 'locked'
            })
        dag_end_code.append("unlock(DAG_global)")
        
        # DAG结束任务的参数
        dag_end_params = f"period={base_period}"
        if arrival_offset:
            dag_end_params += ",arrival_offset=0"
        
        dag_end = {
            'name': 'dag_end',
            'iat': base_period,
            'runtime': 10,
            'deadline': base_period,
            'code': dag_end_code,
            'params': dag_end_params,
            'execution_time': 10,
            'workload': 'control'
        }
        
        # 添加任务间的依赖资源
        for pred, successors in dag.items():
            for succ in successors:
                resource_name = f"res_{pred}_{succ}"
                resources.append({
                    'name': resource_name,
                    'initial_state': 'locked'
                })
        
        # 添加DAG全局资源
        resources.append({
            'name': 'DAG_global',
            'initial_state': 'unlocked'
        })
        
        # 组合所有任务
        all_tasks = [dag_begin] + tasks + [dag_end]
        
        return all_tasks, resources, dag


def create_yaml_content(tasks: List[Dict], resources: List[Dict] = None, 
                       system_config: str = "global_scheduler", total_utilization: float = 0.0,
                       dag_enabled: bool = True, energy_info: Dict = None,
                       arrival_offset_enabled: bool = False,
                       generation_metadata: Dict[str, Any] = None) -> str:
    """
    创建YAML内容 - 增强能量感知版本
    
    增强功能：
    1. 添加详细的能量参数注释
    2. 为每个任务添加能量计算注释
    """
    lines = []
    
    # 添加文件头
    lines.append(f"# PARTSim 能量感知全局调度任务集配置 - 增强版")
    lines.append(f"# 系统配置: {system_config}")
    lines.append(f"# 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"# 任务总数: {len([t for t in tasks if t['name'].startswith('task_')])}")
    lines.append(f"# 总利用率: {total_utilization:.3f}")
    
    if energy_info:
        lines.append(f"# 能量信息:")
        lines.append(f"#   总估算能耗: {energy_info['total_energy']:.3f} J")
        lines.append(f"#   初始系统能量: {energy_info['initial_energy']:.3f} J")
        if 'remaining_energy' in energy_info:
            lines.append(f"#   剩余能量: {energy_info['remaining_energy']:.3f} J")
        if 'energy_utilization' in energy_info:
            lines.append(f"#   能量利用率: {energy_info['energy_utilization']:.1%}")
        lines.append(f"#   能量受限: {'是' if energy_info.get('energy_constrained', False) else '否'}")
        if energy_info.get('energy_constrained', False):
            lines.append(f"#   ⚠️  任务因能量限制已调整")
    
    lines.append(f"# 前驱约束: {'启用' if dag_enabled else '禁用'}")
    lines.append(f"# 到达时间偏移: {'启用' if arrival_offset_enabled else '禁用'}")
    lines.append(f"# 调度策略: 能量感知全局EDF")
    lines.append(f"# 注意: runtime参数作为仿真中的直接执行时间，已考虑能量参数")
    lines.append("")

    if generation_metadata:
        lines.append("metadata:")
        for key, value in generation_metadata.items():
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, str):
                rendered = f'"{value}"'
            else:
                rendered = value
            lines.append(f"  {key}: {rendered}")
        lines.append("")
    
    # 添加能量参数说明（从系统配置中提取）
    try:
        if system_config and os.path.exists(system_config):
            with open(system_config, 'r') as f:
                sys_config = yaml.safe_load(f)
                energy_config = sys_config.get('energy_management', {})
                if energy_config:
                    scheduler_model = _resolve_scheduler_energy_model(energy_config)
                    lines.append("# 能量参数配置:")
                    lines.append(f"#   基础功耗: {scheduler_model.get('base_power', 0.5)} W")
                    
                    workload_coeffs = scheduler_model.get('workload_coefficients', {})
                    if workload_coeffs:
                        lines.append("#   工作负载功率系数:")
                        for workload, coeff in workload_coeffs.items():
                            lines.append(f"#     - {workload}: {coeff}")
                    
                    lines.append("")
    except Exception as e:
        # 忽略错误，继续生成文件
        pass
    
    # 添加任务集
    lines.append("taskset:")
    for task in tasks:
        # 为每个任务添加详细注释（简短任务名：t0, t1, t2...）
        if task['name'][0] == 't' and task['name'][1:].isdigit():
            period = task['iat']
            deadline = task['deadline']
            runtime = task.get('runtime', 0)
            workload = task.get('workload', 'unknown')
            energy = task.get('energy', 0.0)
            arrival_offset = task.get('arrival_offset', 0)
            utilization = runtime / period if period > 0 else 0
            
            lines.append(f"  # {task['name']}:")
            lines.append(f"  #   周期: {period} ms, 截止时间: {deadline} ms")
            lines.append(f"  #   执行时间(runtime): {runtime} ms, 利用率: {utilization:.3f}")
            lines.append(f"  #   工作负载类型: {workload}, 估算能耗: {energy:.3f} J")
            if arrival_offset > 0:
                lines.append(f"  #   到达时间偏移: {arrival_offset} ms")
            if runtime > 0:
                power = energy / (runtime / 1000)  # 转换为瓦特
                lines.append(f"  #   平均功耗: {power:.3f} W (E={energy:.3f}J, t={runtime}ms)")
        
        # 任务定义
        lines.append(f"  - name: {task['name']}")
        lines.append(f"    iat: {task['iat']}")
        lines.append(f"    runtime: {task['runtime']}")  # 关键：runtime作为执行时间
        if 'startcpu' in task:
            lines.append(f"    startcpu: {task['startcpu']}")
        lines.append(f"    deadline: {task['deadline']}")
        if 'params' in task:
            lines.append(f"    params: \"{task['params']}\"")
        lines.append(f"    code:")
        for code_line in task['code']:
            lines.append(f"      - {code_line}")
        lines.append("")  # 任务间空行
    
    # 添加资源
    if resources:
        lines.append("resources:")
        for resource in resources:
            lines.append(f"  - name: {resource['name']}")
            lines.append(f"    initial_state: {resource['initial_state']}")
    
    return '\n'.join(lines)


def load_system_config(system_file: str) -> Dict:
    """加载系统配置文件"""
    try:
        with open(system_file, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.warning(f"无法加载系统配置文件 {system_file}: {e}")
        return {}


def configure_arrival_offset_arguments(parser):
    """Add the backward-compatible, explicitly disableable offset switch."""

    arrival_group = parser.add_mutually_exclusive_group()
    arrival_group.add_argument(
        "--arrival-offset", dest="arrival_offset", action="store_true",
        default=True, help="启用到达时间偏移（兼容默认行为）",
    )
    arrival_group.add_argument(
        "--no-arrival-offset", dest="arrival_offset", action="store_false",
        help="禁用到达时间偏移并生成同步释放任务",
    )
    return parser


def main():
    parser = argparse.ArgumentParser(
        description="能量感知全局调度任务集生成器 - 增强版",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument("-n", "--num-tasks", type=int, default=8,
                       help="任务数量")
    parser.add_argument("-u", "--utilization", type=float, default=2.0,
                       help="总利用率")
    parser.add_argument("-p", "--min-period", type=int, default=1000,
                       help="最小周期(ms)")
    parser.add_argument("-P", "--max-period", type=int, default=5000,
                       help="最大周期(ms)")
    parser.add_argument("-c", "--cpus", type=int, default=4,
                       help="CPU数量")
    parser.add_argument("-cd", "--constrained-deadlines", action="store_true",
                       help="使用约束截止时间，保证 runtime <= deadline <= period；默认使用隐式截止时间 D=T")
    parser.add_argument("--dag", action="store_true", default=False,
                       help="启用前驱约束")
    parser.add_argument("--edge-prob", type=float, default=0.4,
                       help="DAG边概率")
    parser.add_argument("-s", "--system-config", default="./simconf/systems/gpfp_system.yml",
                       help="系统配置文件路径，用于读取能量参数")
    parser.add_argument("--energy-aware", action="store_true", default=True,
                       help="启用能量感知调度")
    configure_arrival_offset_arguments(parser)
    parser.add_argument("--max-arrival-offset", type=int, default=None,
                       help="最大到达时间偏移(ms)，默认为任务周期的30%%")
    parser.add_argument("-o", "--output", default="energy_aware_tasks.yml",
                       help="输出文件")
    parser.add_argument("--seed", type=int, default=None,
                       help="随机种子")
    parser.add_argument(
        "--task-workload-candidate",
        action="append",
        dest="task_workload_candidates",
        help=(
            "实时任务workload候选；可重复，顺序会影响确定性生成。"
            "显式候选池必须按词典序排列且不得包含idle；省略时从已加载"
            "系统功耗模型派生完整词典序非idle集合"
        ),
    )
    parser.add_argument("--min-task-util", type=float, default=0.01,
                       help="UUniFast-Discard单任务最小利用率")
    parser.add_argument("--max-task-util", type=float, default=0.8,
                       help="UUniFast-Discard单任务最大利用率；顺序任务应不超过1.0")
    parser.add_argument("--wcet-rounding", choices=("floor", "round", "ceil", "compensated"),
                       default="floor",
                       help="由u_i*T_i整数化得到runtime时使用的取整方式")
    parser.add_argument(
        "--actual-utilization-tolerance-total",
        type=float,
        default=None,
        help=(
            "生成后允许的总利用率绝对误差；显式设置时若"
            "|actual_total_utilization-target_total_utilization|超过该值，"
            "丢弃整组任务并重新生成"
        ),
    )
    
    args = parser.parse_args()
    
    try:
        logger.info("能量感知全局调度任务集生成器 - 增强版")
        logger.info("=" * 60)
        
        # 加载系统配置
        system_config = load_system_config(args.system_config)
        
        # 创建能量管理器
        energy_manager = None
        
        if args.energy_aware:
            try:
                # 使用文件路径创建能量管理器
                energy_manager = create_energy_manager(args.system_config)
                logger.info("🔋 能量管理已启用")
                
                # 尝试获取初始能量显示
                initial_energy = 0.0
                max_energy = 0.0
                base_harvest_rate = 0.0
                unit_time = 50
                
                # 检查不同的属性访问方式
                if hasattr(energy_manager, 'initial_energy'):
                    initial_energy = energy_manager.initial_energy
                elif hasattr(energy_manager, 'config') and hasattr(energy_manager.config, 'initial_energy'):
                    initial_energy = energy_manager.config.initial_energy
                
                if hasattr(energy_manager, 'max_energy'):
                    max_energy = energy_manager.max_energy
                elif hasattr(energy_manager, 'config') and hasattr(energy_manager.config, 'max_energy'):
                    max_energy = energy_manager.config.max_energy
                
                if hasattr(energy_manager, 'config') and hasattr(energy_manager.config, 'base_harvest_rate_per_ms'):
                    base_harvest_rate = energy_manager.config.base_harvest_rate_per_ms
                
                if hasattr(energy_manager, 'config') and hasattr(energy_manager.config, 'unit_time'):
                    unit_time = energy_manager.config.unit_time
                
                logger.info(f"   初始能量: {initial_energy} J")
                logger.info(f"   最大能量: {max_energy} J")
                logger.info(f"   基础收集率: {base_harvest_rate*1000:.3f} J/s ({base_harvest_rate:.6f} J/ms)")
                logger.info(f"   单位时间: {unit_time} ms")
                
                # 显示功率系数
                if hasattr(energy_manager, 'config') and hasattr(energy_manager.config, 'power_coefficients'):
                    logger.info("   工作负载功率系数:")
                    power_coeffs = energy_manager.config.power_coefficients
                    for workload, coeff in sorted(power_coeffs.items()):
                        logger.info(f"     - {workload}: {coeff} W")
                
            except Exception as e:
                logger.error(f"无法创建能量管理器: {e}")
                import traceback
                traceback.print_exc()
                logger.warning("⚡ 将使用无能量管理版本")
                args.energy_aware = False
                energy_manager = None
        else:
            logger.info("⚡ 能量管理已禁用")
        
        # 创建任务生成器，传入系统配置文件路径
        generator = EnergyAwareTaskGenerator(
            seed=args.seed, 
            energy_manager=energy_manager,
            system_config_path=args.system_config,
            task_workload_candidates=args.task_workload_candidates,
        )
        
        tasks, resources, dag, energy_info = generator.generate_taskset(
            n=args.num_tasks,
            total_utilization=args.utilization,
            min_period=args.min_period,
            max_period=args.max_period,
            num_cpus=args.cpus,
            implicit_deadline=not args.constrained_deadlines,
            dag_enabled=args.dag,
            edge_prob=args.edge_prob,
            energy_aware=args.energy_aware,
            arrival_offset=args.arrival_offset,
            max_arrival_offset=args.max_arrival_offset,
            min_task_util=args.min_task_util,
            max_task_util=args.max_task_util,
            wcet_rounding=args.wcet_rounding,
            actual_utilization_tolerance_total=(
                args.actual_utilization_tolerance_total
            ),
        )
        
        # 计算总利用率
        regular_tasks = [t for t in tasks if t['name'].startswith('task_')]
        target_total_utilization = float(args.utilization)
        target_normalized_utilization = (
            target_total_utilization / args.cpus if args.cpus else 0.0
        )
        actual_total_utilization = sum(
            (t.get('runtime', 0) / t.get('iat', 1))
            for t in regular_tasks
            if t.get('iat', 0) > 0
        )
        actual_normalized_utilization = (
            actual_total_utilization / args.cpus if args.cpus else 0.0
        )
        generation_metadata = {
            "target_total_utilization": target_total_utilization,
            "target_normalized_utilization": target_normalized_utilization,
            "actual_total_utilization": actual_total_utilization,
            "actual_normalized_utilization": actual_normalized_utilization,
            "utilization_error_total": (
                actual_total_utilization - target_total_utilization
            ),
            "utilization_error_normalized": (
                actual_normalized_utilization - target_normalized_utilization
            ),
            "task_util_min": args.min_task_util,
            "task_util_max": args.max_task_util,
            "wcet_rounding": args.wcet_rounding,
            "deadline_mode": (
                "constrained" if args.constrained_deadlines else "implicit"
            ),
            "actual_utilization_tolerance_total": (
                ""
                if args.actual_utilization_tolerance_total is None
                else args.actual_utilization_tolerance_total
            ),
            "period_min": args.min_period,
            "period_max": args.max_period,
            "num_tasks": args.num_tasks,
            "num_cores": args.cpus,
            "M": args.cpus,
            "task_workload_contract_version": (
                TASK_WORKLOAD_CONTRACT_VERSION
            ),
            "task_workload_candidates": ",".join(
                generator.task_workload_candidates
            ),
            "task_workload_candidate_identity": (
                _task_workload_candidate_identity(
                    generator.task_workload_candidates
                )
            ),
        }
        
        # 计算每个工作负载类型的总能耗
        from collections import defaultdict
        workload_energy = defaultdict(float)
        for task in regular_tasks:
            workload = task.get('workload', 'unknown')
            workload_energy[workload] += task.get('energy', 0)
        
        # 统计到达时间偏移
        arrival_offsets = [t.get('arrival_offset', 0) for t in tasks]
        max_arrival_offset = max(arrival_offsets) if arrival_offsets else 0
        avg_arrival_offset = sum(arrival_offsets) / len(arrival_offsets) if arrival_offsets else 0
        
        # 创建YAML内容
        yaml_content = create_yaml_content(
            tasks=tasks,
            resources=resources,
            system_config=args.system_config,
            total_utilization=actual_total_utilization,
            dag_enabled=args.dag,
            energy_info=energy_info,
            arrival_offset_enabled=args.arrival_offset,
            generation_metadata=generation_metadata
        )
        
        # 保存文件
        with open(args.output, 'w') as f:
            f.write(yaml_content)
        
        logger.info(f"✓ 任务集已保存至: {args.output}")
        
        # 显示详细信息
        logger.info("\n📊 任务集统计:")
        logger.info(f"  任务数量: {len(regular_tasks)}")
        logger.info(f"  目标总利用率: {target_total_utilization:.3f}")
        logger.info(f"  实际总利用率: {actual_total_utilization:.3f}")
        logger.info(f"  总估算能耗: {energy_info['total_energy']:.3f} J")
        
        logger.info(f"  各工作负载能耗分布:")
        for workload, energy in sorted(workload_energy.items(), key=lambda x: x[1], reverse=True):
            percentage = (energy / energy_info['total_energy'] * 100) if energy_info['total_energy'] > 0 else 0
            logger.info(f"    - {workload}: {energy:.3f} J ({percentage:.1f}%)")
        
        if args.arrival_offset:
            logger.info(f"  到达时间偏移:")
            logger.info(f"    最大偏移: {max_arrival_offset} ms")
            logger.info(f"    平均偏移: {avg_arrival_offset:.1f} ms")
        
        if energy_info['energy_constrained']:
            logger.warning(f"  ⚠️  任务因能量限制已调整")
        
        if args.energy_aware and energy_manager:
            # 尝试获取能量状态
            try:
                if hasattr(energy_manager, 'get_energy_status'):
                    status = energy_manager.get_energy_status()
                    if 'remaining_energy' in status:
                        logger.info(f"  剩余能量: {status['remaining_energy']:.3f} J")
                    if 'energy_utilization' in status:
                        logger.info(f"  能量利用率: {status['energy_utilization']:.3f}")
                elif hasattr(energy_manager, 'current_energy'):
                                logger.info(f"  剩余能量: {energy_manager.current_energy:.3f} J")
            except Exception as e:
                logger.error(f"  无法获取能量状态: {e}")
        
        # 使用建议
        logger.info("\n💡 使用建议:")
        logger.info(f"   运行仿真: ./run_sim.sh -s {args.system_config} -t {args.output} -d 100000 -st 43200000")
        logger.info(f"   不同时间:")
        logger.info(f"     早上: ./run_sim.sh -s {args.system_config} -t {args.output} -d 100000 -st 28800000")
        logger.info(f"     中午: ./run_sim.sh -s {args.system_config} -t {args.output} -d 100000 -st 43200000")
        logger.info(f"     晚上: ./run_sim.sh -s {args.system_config} -t {args.output} -d 100000 -st 72000000")
        
    except Exception as e:
        logger.error(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
