#!/usr/bin/env python3
import json
import subprocess
import yaml
import os
import sys
import re
from pathlib import Path
from typing import Dict, Any
from collections import defaultdict
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import cpu_count

# ============================================
# 0. 全局配置与预读取
# ============================================
CONFIG_TEMPLATE = 'system_config_unified_template.yml'

def get_system_cores(config_path):
    """从配置文件中读取核心数量"""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
            config = yaml.safe_load(content)
            num_cpus = config['cpu_islands'][0]['numcpus']
            print(f"🖥️  自动检测到系统核心数: {num_cpus}")
            return int(num_cpus)
    except Exception as e:
        print(f"⚠️ 无法读取核心数 (默认回退到 4): {e}")
        return 4

SYSTEM_CORES = get_system_cores(CONFIG_TEMPLATE)

# 并行执行配置
MAX_WORKERS = min(12, cpu_count() - 2)
print(f"🔧 并行工作进程数: {MAX_WORKERS}")

ALGORITHMS = [
    'gpfp_asap_block', 'gpfp_asap_nonblock', 'gpfp_asap_sync',
    'gpfp_alap_block', 'gpfp_alap_nonblock', 'gpfp_alap_sync',
    'gpfp_st_block', 'gpfp_st_nonblock', 'gpfp_st_sync'
]

ALGORITHM_FAMILIES = {
    'asap': ['gpfp_asap_block', 'gpfp_asap_nonblock', 'gpfp_asap_sync'],
    'alap': ['gpfp_alap_block', 'gpfp_alap_nonblock', 'gpfp_alap_sync'],
    'st': ['gpfp_st_block', 'gpfp_st_nonblock', 'gpfp_st_sync'],
}

ALGO_DISPLAY_NAMES = {
    'gpfp_asap_block': 'asapblock',
    'gpfp_asap_nonblock': 'asapnonblock',
    'gpfp_asap_sync': 'asapsync',
    'gpfp_alap_block': 'alapblock',
    'gpfp_alap_nonblock': 'alapnonblock',
    'gpfp_alap_sync': 'alapsync',
    'gpfp_st_block': 'stblock',
    'gpfp_st_nonblock': 'stnonblock',
    'gpfp_st_sync': 'stsync',
}

ALGO_STYLES = {
    'gpfp_asap_block': {'color': '#1f77b4', 'marker': 'o'},
    'gpfp_asap_nonblock': {'color': '#2ca02c', 'marker': 's'},
    'gpfp_asap_sync': {'color': '#d62728', 'marker': '^'},
    'gpfp_alap_block': {'color': '#1f77b4', 'marker': 'o'},
    'gpfp_alap_nonblock': {'color': '#2ca02c', 'marker': 's'},
    'gpfp_alap_sync': {'color': '#d62728', 'marker': '^'},
    'gpfp_st_block': {'color': '#1f77b4', 'marker': 'o'},
    'gpfp_st_nonblock': {'color': '#2ca02c', 'marker': 's'},
    'gpfp_st_sync': {'color': '#d62728', 'marker': '^'},
}
BATTERY_CAPACITIES = [1.0, 3.0, 5.0, 10.0, 15.0, 25.0, 40.0, 60.0]

NUM_TASKSETS = 20
SIMULATION_TIME = 10000

# ============================================
# [核心修改区] 任务生成参数调整
# ============================================
TASK_GENERATOR = './global_task_generator.py'

# 1. 增加任务数量：增加调度干扰
TASK_N = 10

# 2. [关键修改] 提高利用率到 2.8
# 目的：制造能源赤字 (Demand > Supply)，迫使算法在缺电时表现出差异。
TASK_U = 2.8

# 3. [关键修改] 恢复周期范围 (20ms - 100ms)
# 恢复到原来的标准范围，避免过度碎片化。
TASK_P_MIN = 20
TASK_P_MAX = 100

# ============================================

# 路径配置
SIMULATOR = './build/rtsim/rtsim'
OUTPUT_DIR = Path('experiment_results_u2.8_init0.3') # 修改目录名以防覆盖
TRACE_DIR = OUTPUT_DIR / 'traces'
TASK_DIR = OUTPUT_DIR / 'tasks'
FIGURE_OUTPUTS = {
    'asap': OUTPUT_DIR / 'figure_asap_diff.png',
    'alap': OUTPUT_DIR / 'figure_alap_diff.png',
    'st': OUTPUT_DIR / 'figure_st_diff.png',
}
TABLE_OUTPUT = OUTPUT_DIR / 'table1_diff.md'

for p in [OUTPUT_DIR, TRACE_DIR, TASK_DIR]:
    p.mkdir(parents=True, exist_ok=True)


# ============================================
# 1. 环境自检
# ============================================
def check_environment():
    required_files = [
        CONFIG_TEMPLATE,
        TASK_GENERATOR,
        SIMULATOR,
        './build/librtsim'
    ]
    missing = []
    for f in required_files:
        if not os.path.exists(f):
            missing.append(f)
    
    if missing:
        print("❌ 环境检查失败！以下文件缺失：")
        for m in missing:
            print(f"   - {m}")
        sys.exit(1)
    print("✅ 环境检查通过。")


# ============================================
# 2. TraceParser (混合优化策略)
# ============================================
class TraceParser:
    def __init__(self, trace_file: str, num_cores: int):
        self.trace_file = trace_file
        self.num_cores = num_cores
        self.events = []
        self._load_data()

    def _load_data(self):
        try:
            with open(self.trace_file, 'r') as f:
                data = json.load(f)
                self.events = data.get('events', [])
        except Exception as e:
            self.events = []

    def parse(self) -> Dict[str, Any]:
        if not self.events:
            return self._empty_results()

        # 按时间排序
        sorted_events = sorted(self.events, key=lambda e: float(e['time']))

        # --- 统计变量 ---
        stats = {
            'total_instances': 0,        # 总到达任务数 (分母)
            'completed_instances': 0,    # 实际完成任务数
            'failed_instances': 0,       # 失败任务数 (显式 dline_miss)
            'preemptions': 0,            # 抢占次数 (Descheduled)
            'busy_time': 0.0,            # 累计执行时间
            'energy_sum': 0.0,           # 能量累加 (算术平均用)
            'overhead_proxy': len(sorted_events) / 1000.0
        }

        # 辅助变量
        task_start_times = {}      # task_key -> 开始时间
        open_jobs = set()          # 未完成/未失败的作业集合

        for event in sorted_events:
            etype = event['event_type']
            curr_time = float(event['time'])

            # [Strict] Average Energy Level
            current_energy_J = float(event.get('current_energy_mJ', 0)) / 1000.0
            stats['energy_sum'] += current_energy_J

            task_key = (event.get('task_name'), str(event.get('arrival_time')))

            if etype == 'arrival':
                stats['total_instances'] += 1
                # 记录到达时间用于饿死判定 (task_key, arrival_time)
                open_jobs.add((task_key, curr_time))

            elif etype == 'dline_miss':
                stats['failed_instances'] += 1
                # 从 open_jobs 中移除
                to_remove = [j for j in open_jobs if j[0] == task_key]
                for j in to_remove:
                    open_jobs.remove(j)

            elif etype == 'kill':
                # kill 事件也视为失败
                stats['failed_instances'] += 1
                to_remove = [j for j in open_jobs if j[0] == task_key]
                for j in to_remove:
                    open_jobs.remove(j)

            elif etype == 'descheduled':
                # [Strict] Preemptions
                stats['preemptions'] += 1
                if task_key in task_start_times:
                    stats['busy_time'] += (curr_time - task_start_times.pop(task_key))

            elif etype == 'scheduled':
                task_start_times[task_key] = curr_time

            elif etype == 'end_instance':
                stats['completed_instances'] += 1
                if task_key in task_start_times:
                    stats['busy_time'] += (curr_time - task_start_times.pop(task_key))
                # 从 open_jobs 中移除
                to_remove = [j for j in open_jobs if j[0] == task_key]
                for j in to_remove:
                    open_jobs.remove(j)

        # --- 饿死判定逻辑 ---
        # 获取最后一个事件的时间戳
        last_time = float(sorted_events[-1]['time']) if sorted_events else 0.0
        # 边界免责期：最后 200ms 内到达的任务不算饿死
        starvation_threshold = last_time - 200.0

        starved_count = 0
        for job_key, arrival_time in open_jobs:
            if arrival_time <= starvation_threshold:
                # 该任务在队列中被饿死
                starved_count += 1

        stats['failed_instances'] += starved_count

        # --- 计算最终指标 (修正后) ---

        # 1. [Fixed] Job-level Failure Rate
        if stats['total_instances'] > 0:
            stats['failure_rate'] = stats['failed_instances'] / stats['total_instances']
        else:
            stats['failure_rate'] = 0.0

        # 2. [Fixed] Avg Execution Time (使用 completed_instances 作为分母)
        if stats['completed_instances'] > 0:
            stats['avg_execution_time'] = stats['busy_time'] / stats['completed_instances']
        else:
            stats['avg_execution_time'] = 0.0

        # 3. [Strict] Idle Time
        total_capacity = SIMULATION_TIME * self.num_cores
        stats['total_idle_time'] = max(0.0, total_capacity - stats['busy_time'])

        # 4. [Strict] Energy Level (算术平均)
        stats['avg_energy_level'] = stats['energy_sum'] / len(sorted_events) if sorted_events else 0.0

        return stats

    def _empty_results(self) -> Dict[str, Any]:
        return {
            'failure_rate': 0.0, 'preemptions': 0, 
            'avg_execution_time': 0.0, 'total_idle_time': 0.0,
            'avg_energy_level': 0.0, 'overhead_proxy': 0.0
        }


# ============================================
# 独立函数用于并行执行 (Top-level)
# ============================================
def run_single_simulation_worker(args):
    """独立函数用于并行执行单个仿真"""
    algorithm, battery, config_file, task_file, task_idx = args
    trace_file = TRACE_DIR / f'trace_{algorithm}_{battery}_{task_idx}.json'

    env = os.environ.copy()
    lib_path = os.path.abspath('./build/librtsim')
    env['LD_LIBRARY_PATH'] = lib_path + ':' + env.get('LD_LIBRARY_PATH', '')

    cmd = [
        SIMULATOR, config_file, task_file,
        str(SIMULATION_TIME), '-t', str(trace_file)
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, env=env, text=True, timeout=120)
        # 实例化 Parser 并解析
        parser = TraceParser(str(trace_file), SYSTEM_CORES)
        parsed_stats = parser.parse()
        return (algorithm, battery, parsed_stats, None)
    except subprocess.TimeoutExpired:
        print(f"\n❌ [致命错误] 算法 {algorithm} 在 Battery={battery} 时死锁卡住，超过120秒被强杀！请检查 C++ 代码！")
        return (algorithm, battery, None, "Timeout")
    except subprocess.CalledProcessError:
        return (algorithm, battery, None, "Simulation failed")
    except Exception as e:
        return (algorithm, battery, None, f"Parse error: {e}")


# ============================================
# 3. ExperimentRunner
# ============================================
class ExperimentRunner:
    def __init__(self):
        self.results = defaultdict(lambda: defaultdict(list))
        self.task_files = []

    def generate_tasksets(self):
        print(f"📦 生成 {NUM_TASKSETS} 组任务集 (Cores={SYSTEM_CORES}, U={TASK_U}, Period={TASK_P_MIN}-{TASK_P_MAX})...")
        for i in range(NUM_TASKSETS):
            seed = 1000203 + i 
            task_file = TASK_DIR / f'taskset_{i:03d}.yml'
            
            # 强制重新生成任务集 (因为参数变了)
            if task_file.exists():
                os.remove(task_file)

            cmd = [
                'python3', TASK_GENERATOR,
                '-n', str(TASK_N), '-u', str(TASK_U),
                '-p', str(TASK_P_MIN), '-P', str(TASK_P_MAX),
                '-c', str(SYSTEM_CORES),
                '--seed', str(seed),
                '-o', str(task_file)
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                self.task_files.append(str(task_file))
            except subprocess.CalledProcessError as e:
                print(f"❌ 生成任务集失败: {e}")
                continue
        print(f"✅ 任务集准备就绪: {len(self.task_files)} 组")

    def modify_config(self, algorithm: str, battery_capacity: float) -> str:
        with open(CONFIG_TEMPLATE, 'r', encoding='utf-8') as f:
            content = f.read()

        content = re.sub(r'scheduler:\s*\w+', f'scheduler: {algorithm}', content)
        content = re.sub(r'max_energy:\s*[\d.]+', f'max_energy: {battery_capacity}', content)
        
        # 初始能量 30%
        initial_energy = battery_capacity * 0.3
        if 'initial_energy_ratio:' in content:
             content = re.sub(r'initial_energy_ratio:\s*[\d.]+', f'initial_energy_ratio: 0.3', content)
        elif 'initial_energy:' in content:
             content = re.sub(r'initial_energy:\s*[\d.]+', f'initial_energy: {initial_energy}', content)
             
        # 能量收集时间点为 04:00 (1.42W)
        if 'start_time_ms:' in content:
             content = re.sub(r'start_time_ms:\s*\d+', 'start_time_ms: 14400000', content)

        temp_config = OUTPUT_DIR / f'config_{algorithm}_{battery_capacity}.yml'
        with open(temp_config, 'w', encoding='utf-8') as f:
            f.write(content)
        return str(temp_config)

    def run_experiments(self):
        total_runs = len(ALGORITHMS) * len(BATTERY_CAPACITIES) * len(self.task_files)
        print(f"🚀 开始实验 (High Heterogeneity Mode)...")
        print(f"   总仿真数: {total_runs}")
        print(f"   并行进程: {MAX_WORKERS}")

        config_files = {}
        for algorithm in ALGORITHMS:
            for battery in BATTERY_CAPACITIES:
                config_files[(algorithm, battery)] = self.modify_config(algorithm, battery)

        tasks = []
        for algorithm in ALGORITHMS:
            for battery in BATTERY_CAPACITIES:
                cfg = config_files[(algorithm, battery)]
                for task_idx, task_file in enumerate(self.task_files):
                    tasks.append((algorithm, battery, cfg, task_file, task_idx))

        count = 0
        with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(run_single_simulation_worker, task): task for task in tasks}

            for future in as_completed(futures):
                algorithm, battery, parsed_stats, error = future.result()
                if parsed_stats is not None:
                    self.results[algorithm][battery].append(parsed_stats)
                count += 1
                if count % 100 == 0 or count == total_runs:
                    print(f"   进度: {count}/{total_runs} ({(count/total_runs)*100:.1f}%)")

        for config_file in config_files.values():
            if os.path.exists(config_file):
                os.remove(config_file)

    def aggregate_results(self) -> pd.DataFrame:
        data = []
        for algo in ALGORITHMS:
            for batt in BATTERY_CAPACITIES:
                res = self.results[algo][batt]
                if not res: continue
                avg = {k: np.mean([r[k] for r in res]) for k in res[0].keys()}
                avg['algorithm'] = algo
                avg['battery_capacity'] = batt
                data.append(avg)
        return pd.DataFrame(data)


# ============================================
# 4. 绘图与表格
# ============================================
class FigureGenerator:
    def __init__(self, df: pd.DataFrame):
        self.df = df

    @staticmethod
    def _metric_configs():
        return [
            ('failure_rate', 'Failure Rate (Job-level)'),
            ('preemptions', 'Preemptions (Count)'),
            ('total_idle_time', 'Total Idle Time (ms)'),
            ('avg_execution_time', 'Avg Exec Time (ms)'),
            ('overhead_proxy', 'Scheduler Overhead'),
            ('avg_energy_level', 'Avg Energy Level (J)')
        ]

    def generate_family_figure(self, family: str):
        family_algorithms = ALGORITHM_FAMILIES[family]
        fig, axes = plt.subplots(2, 3, figsize=(18, 10))
        fig.suptitle(f'{family.upper()} Performance Comparison (High Task Heterogeneity)', fontsize=16)

        axes_flat = axes.flatten()

        for idx, (col, title) in enumerate(self._metric_configs()):
            ax = axes_flat[idx]
            for algo in family_algorithms:
                d = self.df[self.df['algorithm'] == algo]
                if not d.empty:
                    d = d.sort_values('battery_capacity')
                    style = ALGO_STYLES[algo]
                    ax.plot(
                        d['battery_capacity'], d[col],
                        marker=style['marker'], markersize=6, linewidth=2,
                        label=ALGO_DISPLAY_NAMES[algo], color=style['color'], alpha=0.8
                    )

            ax.set_title(title, fontweight='bold')
            ax.set_xlabel('Battery Capacity (Joules)')
            ax.grid(True, linestyle='--', alpha=0.5)

            if col == 'failure_rate':
                ax.set_ylim(-0.05, 1.05)

            if idx == 0:
                ax.legend(loc='upper right')

        plt.tight_layout(rect=[0, 0.03, 1, 0.95])
        output_path = FIGURE_OUTPUTS[family]
        plt.savefig(output_path, dpi=300)
        plt.close(fig)
        print(f"📊 图表已保存: {output_path}")

    def generate_all_family_figures(self):
        for family in ['asap', 'alap', 'st']:
            self.generate_family_figure(family)

class TableGenerator:
    def __init__(self, df: pd.DataFrame):
        self.df = df

    def _get_rank(self, metric_series, smaller_is_better=True):
        items = list(metric_series.items())
        items.sort(key=lambda x: x[1], reverse=not smaller_is_better)
        ranks = {}
        for i, (algo, val) in enumerate(items):
            ranks[algo] = f"**{val:.3f}**" if i == 0 else f"{val:.3f}"
        return ranks

    def generate_table1(self):
        summary = self.df.groupby('algorithm').mean(numeric_only=True)
        metrics = {
            'failure_rate': True,
            'preemptions': True,
            'overhead_proxy': True,
            'total_idle_time': False,
            'avg_execution_time': False,
            'avg_energy_level': False
        }
        lines = []

        for family in ['asap', 'alap', 'st']:
            family_algorithms = ALGORITHM_FAMILIES[family]
            lines.append(f"## {family.upper()}\n")
            header = "| Metric | " + " | ".join(ALGO_DISPLAY_NAMES[algo] for algo in family_algorithms) + " |"
            divider = "|---|" + "---|" * len(family_algorithms)
            lines.extend([header, divider])

            family_summary = summary.loc[summary.index.intersection(family_algorithms)]
            for metric, smaller_is_better in metrics.items():
                if metric not in family_summary.columns:
                    continue
                series = family_summary[metric]
                rank_map = self._get_rank(series, smaller_is_better)
                row = f"| {metric} |"
                for algo in family_algorithms:
                    row += f" {rank_map.get(algo, 'N/A')} |"
                lines.append(row)
            lines.append("")

        with open(TABLE_OUTPUT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print(f"📋 表格已保存: {TABLE_OUTPUT}")

if __name__ == '__main__':
    check_environment()
    
    try:
        runner = ExperimentRunner()
        runner.generate_tasksets()
        runner.run_experiments()
        
        df = runner.aggregate_results()
        if df.empty:
            print("❌ 没有产生有效数据")
        else:
            print("💾 保存原始数据...")
            df.to_csv(OUTPUT_DIR / 'raw_data_diff.csv', index=False)
            
            print("🎨 正在绘图...")
            FigureGenerator(df).generate_all_family_figures()

            print("📝 正在生成表格...")
            TableGenerator(df).generate_table1()
            
            print(f"\n✅ 实验结束！结果保存在: {OUTPUT_DIR}")
            
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断")
    except Exception as e:
        print(f"\n❌ 运行时错误: {e}")
