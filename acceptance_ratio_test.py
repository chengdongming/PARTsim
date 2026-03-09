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
import subprocess
import yaml
import os
import sys
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import cpu_count
from pathlib import Path
import numpy as np
import pandas as pd
from collections import defaultdict
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


def get_system_cores(config_path):
    """从配置文件中读取系统核心数"""
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f.read())
        return int(config['cpu_islands'][0]['numcpus'])


def run_single_simulation_worker(task):
    """执行单次仿真并返回二元可调度性结果"""
    algorithm, config_file, task_file, task_idx, utilization, simulation_time, trace_dir = task
    trace_file = Path(trace_dir) / f'trace_{algorithm}_u{utilization:.2f}_{task_idx:03d}.json'

    env = os.environ.copy()
    lib_path = os.path.abspath('./build/librtsim')
    env['LD_LIBRARY_PATH'] = lib_path + ':' + env.get('LD_LIBRARY_PATH', '')

    cmd = [
        SIMULATOR, config_file, task_file,
        str(simulation_time), '-t', str(trace_file)
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, env=env, text=True, timeout=120)
        acceptance_ratio = TraceParser(str(trace_file)).get_acceptance_ratio()
        return (algorithm, utilization, acceptance_ratio, None)
    except subprocess.TimeoutExpired:
        return (algorithm, utilization, 0.0,
                f"⏱️ 仿真超时: {algorithm}, U={utilization:.2f}, idx={task_idx}")
    except subprocess.CalledProcessError as e:
        error_output = (e.stderr or e.stdout or '').strip()
        message = f"❌ 仿真失败: {algorithm}, U={utilization:.2f}, idx={task_idx}"
        if error_output:
            message = f"{message}\n{error_output}"
        return (algorithm, utilization, 0.0, message)
    except Exception as e:
        return (algorithm, utilization, 0.0,
                f"❌ 仿真异常: {algorithm}, U={utilization:.2f}, idx={task_idx}: {e}")

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
DEFAULT_NUM_TASKSETS = 20  # 每个利用率点20个任务集
DEFAULT_TASK_N = 10  # 每个任务集10个任务
DEFAULT_TASK_P_MIN = 40  # 周期范围：最小40ms
DEFAULT_TASK_P_MAX = 400  # 周期范围：最大400ms（增加多样性）
DEFAULT_SIMULATION_TIME = 30000  # 30秒仿真
DEFAULT_BATTERY_CAPACITY = 20.0  # 20J电池
DEFAULT_INITIAL_ENERGY_RATIO = 1.0  # 100%初始能量（满电）
DEFAULT_SOLAR_START_TIME_MS = 21975000  # 太阳能起始时间（毫秒）
DEFAULT_USE_REAL_SOLAR_DATA = False  # 使用分段函数模拟，不使用真实太阳能数据
DEFAULT_MAX_WORKERS = max(1, min(12, cpu_count() - 2))

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

    def get_acceptance_ratio(self):
        """
        计算严格二元可调度性（Strict Binary Schedulability）

        逻辑：
        - 如果追踪文件为空或无效 -> 返回 0.0（失败）
        - 如果不存在任何 'arrival' -> 返回 0.0（无效测试）
        - 如果存在任何 'dline_miss' -> 返回 0.0（任务集失败，一票否决）
        - 如果存在已释放但未闭合的 job（既没有 end_instance，也没有 dline_miss）-> 返回 0.0
        - 只有当所有已释放 job 都完整闭合且无 deadline miss 时 -> 返回 1.0

        说明：
        - 仍然是二元判定，只返回 0.0 或 1.0
        - job 使用 (task_name, arrival_time) 进行匹配
        """
        if not self.events:
            return 0.0

        open_jobs = set()
        has_arrivals = False

        for event in self.events:
            event_type = event.get('event_type', '')
            task_name = event.get('task_name')
            arrival_time = event.get('arrival_time')

            if event_type == 'arrival':
                has_arrivals = True
                job_key = (task_name, str(arrival_time if arrival_time is not None else event.get('time')))
                open_jobs.add(job_key)

            elif event_type == 'dline_miss':
                return 0.0

            elif event_type == 'end_instance':
                job_key = (task_name, str(arrival_time if arrival_time is not None else event.get('time')))
                open_jobs.discard(job_key)

        if not has_arrivals:
            return 0.0

        if open_jobs:
            return 0.0

        return 1.0

# ============================================
# 实验执行器
# ============================================
class ExperimentRunner:
    """运行接受率实验"""

    def __init__(self, output_dir, utilization_points, num_tasksets,
                 task_n, task_p_min, task_p_max, simulation_time,
                 battery_capacity, initial_energy_ratio, solar_start_time_ms,
                 use_real_solar_data=True, system_cores=None, max_workers=DEFAULT_MAX_WORKERS):
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
        self.system_cores = system_cores if system_cores is not None else get_system_cores(CONFIG_TEMPLATE)
        self.max_workers = max(1, max_workers)

        print(f"🖥️  系统核心数: {self.system_cores}")
        print(f"📁 输出目录: {self.output_dir}")
        print(f"⚙️  并发进程数: {self.max_workers}")

    def generate_taskset(self, utilization, task_idx, seed):
        """生成指定利用率的任务集"""
        task_file = self.task_dir / f'taskset_u{utilization:.2f}_{task_idx:03d}.yml'

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
            '-o', str(task_file)
        ]

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

        tasks = []
        for u_idx, utilization in enumerate(self.utilization_points):
            print(f"\n📊 处理利用率点 {u_idx+1}/{len(self.utilization_points)}: U_norm={utilization:.2f}")

            task_files = []
            for task_idx in range(self.num_tasksets):
                seed = 2000 + int(utilization * 100) * 100 + task_idx
                task_file = self.generate_taskset(utilization, task_idx, seed)
                if task_file:
                    task_files.append((task_idx, task_file))

            if not task_files:
                print(f"⚠️ 没有成功生成任务集，跳过 U={utilization:.2f}")
                continue

            for task_idx, task_file in task_files:
                for algo in ALGORITHMS:
                    tasks.append((
                        algo,
                        config_files[algo],
                        task_file,
                        task_idx,
                        utilization,
                        self.simulation_time,
                        str(self.trace_dir),
                    ))

        count = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(run_single_simulation_worker, task): task for task in tasks}

            for future in as_completed(futures):
                algorithm, utilization, acceptance_ratio, error = future.result()
                if error:
                    print(error)
                results[algorithm][utilization].append(acceptance_ratio)

                count += 1
                if count % 10 == 0 or count == total_runs:
                    print(f"   进度: {count}/{total_runs} ({(count/total_runs)*100:.1f}%)")

        for config_file in config_files.values():
            if os.path.exists(config_file):
                os.remove(config_file)

        return results

    def aggregate_results(self, results):
        """
        聚合结果：计算每个利用率点的平均接受率

        注意：这里的平均是对二元值（0.0或1.0）求平均
        例如：[1, 1, 0, 1, 0] 的平均值是 0.6，表示60%的任务集可调度
        """
        data = []
        for algo in ALGORITHMS:
            for utilization in self.utilization_points:
                acceptance_ratios = results[algo][utilization]
                if acceptance_ratios:
                    # 计算平均接受率（即可调度任务集的比例）
                    avg_acceptance = np.mean(acceptance_ratios)
                    data.append({
                        'algorithm': algo,
                        'normalized_utilization': utilization,
                        'acceptance_ratio': avg_acceptance,
                        'num_samples': len(acceptance_ratios),
                        'num_successful': int(sum(acceptance_ratios))  # 成功的任务集数量
                    })

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

    # 实验控制
    parser.add_argument('--run-experiment', action='store_true',
                       help='运行实验生成新数据')
    parser.add_argument('--csv', type=str, default=None,
                       help='从CSV文件加载数据（不运行实验）')

    # 实验参数
    parser.add_argument('--output-dir', type=str, default='acceptance_ratio_experiment',
                       help='输出目录 (默认: acceptance_ratio_experiment)')
    parser.add_argument('--num-points', type=int, default=10,
                       help='利用率采样点数 (默认: 10)')
    parser.add_argument('--num-tasksets', type=int, default=DEFAULT_NUM_TASKSETS,
                       help=f'每个利用率点的任务集数量 (默认: {DEFAULT_NUM_TASKSETS})')
    parser.add_argument('--task-n', type=int, default=DEFAULT_TASK_N,
                       help=f'每个任务集的任务数 (默认: {DEFAULT_TASK_N})')
    parser.add_argument('--battery', type=float, default=DEFAULT_BATTERY_CAPACITY,
                       help=f'电池容量 (Joules) (默认: {DEFAULT_BATTERY_CAPACITY})')
    parser.add_argument('--initial-energy', type=float, default=DEFAULT_INITIAL_ENERGY_RATIO,
                       help=f'初始能量比例 (0.0-1.0) (默认: {DEFAULT_INITIAL_ENERGY_RATIO})')
    parser.add_argument('--solar-time-ms', type=int, default=DEFAULT_SOLAR_START_TIME_MS,
                       help=f'太阳能收集开始时间（毫秒）(默认: {DEFAULT_SOLAR_START_TIME_MS})')
    parser.add_argument('--max-workers', type=int, default=DEFAULT_MAX_WORKERS,
                       help=f'并发线程数 (默认: {DEFAULT_MAX_WORKERS})')

    # 图表参数
    parser.add_argument('--figure-output', type=str, default=None,
                       help='综合图表输出文件名（可选，默认生成6张分组图表）')
    parser.add_argument('--x-label', type=str, default=None,
                       help='自定义X轴标签')
    parser.add_argument('--no-group-figures', action='store_true',
                       help='不生成分组图表，只生成综合图表')

    args = parser.parse_args()

    # 决定数据来源
    if args.run_experiment:
        # 运行实验
        utilization_points = np.around(np.linspace(0.1, 1.0, args.num_points), 2)
        system_cores = get_system_cores(CONFIG_TEMPLATE)

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
            max_workers=args.max_workers
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
