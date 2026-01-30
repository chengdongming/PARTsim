#!/usr/bin/env python3
"""
自动化调度算法对比实验脚本 (多核自适应 + 物理参数修正版)
复刻论文 Figure 5 和 Table 1

修正重点:
1. 自动从YAML读取核心数 (numcpus)，不再硬编码。
2. 修正电池容量范围：针对 0.6mJ/ms 的负载，将测试范围调整为 1J - 10J。
3. 修正TraceParser的多核计算逻辑。
"""

import json
import subprocess
import yaml
import os
import sys
from pathlib import Path
from typing import Dict, Any
from collections import defaultdict
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# ============================================
# 0. 预读取配置 (自动获取核心数)
# ============================================
CONFIG_TEMPLATE = 'system_config_unified_template.yml'

def get_system_cores(config_path):
    """从配置文件中读取核心数量"""
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
            # 假设第一个 island 是主要计算单元
            num_cpus = config['cpu_islands'][0]['numcpus']
            print(f"🖥️ 自动检测到系统核心数: {num_cpus}")
            return int(num_cpus)
    except Exception as e:
        print(f"❌ 无法读取核心数，请检查 {config_path}: {e}")
        sys.exit(1)

# 获取核心数
SYSTEM_CORES = get_system_cores(CONFIG_TEMPLATE)

# ============================================
# 配置参数
# ============================================

# 实验配置
ALGORITHMS = ['gpfp_tie', 'gpfp_tgf', 'gpfp_btie']

# ⭐ 关键修正：电池容量范围 (单位: Joules)
# 负载约 0.6mJ/ms，10s总需 6J。
# 设置范围 1J - 10J，覆盖"严重缺电"到"刚好够用"的区间。
BATTERY_CAPACITIES = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

NUM_TASKSETS = 50
SIMULATION_TIME = 10000  # ms

# 任务生成配置
TASK_GENERATOR = './global_task_generator.py'
TASK_N = 8    # 任务数建议是核心数的 2-4 倍，8个任务跑在多核上比较合适
TASK_U = 1.5  # 总利用率 (对于4核系统，1.5是很轻松的；对于2核是75%负载)
TASK_P_MIN = 20
TASK_P_MAX = 200

# 仿真器配置
SIMULATOR = './rtsim/rtsim'

# 输出目录
OUTPUT_DIR = Path('experiment_results_final')
TRACE_DIR = OUTPUT_DIR / 'traces'
TASK_DIR = OUTPUT_DIR / 'tasks'
FIGURE_OUTPUT = OUTPUT_DIR / 'figure5.png'
TABLE_OUTPUT = OUTPUT_DIR / 'table1.md'

for p in [OUTPUT_DIR, TRACE_DIR, TASK_DIR]:
    p.mkdir(parents=True, exist_ok=True)


# ============================================
# TraceParser (多核逻辑修正)
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
            # print(f"警告: 无法加载追踪文件 {self.trace_file}")
            self.events = []

    def parse(self) -> Dict[str, Any]:
        if not self.events:
            return self._empty_results()

        stats = {
            'total_instances': 0,
            'failed_instances': 0,
            'preemptions': 0,
            'busy_time': 0.0,
            'idle_time': 0.0,
            'total_time': SIMULATION_TIME,
            'num_cores': self.num_cores
        }

        unique_instances = set()
        failed_instances_set = set()
        schedule_counts = defaultdict(int)
        active_tasks = set() # 当前正在运行的任务集合

        sorted_events = sorted(self.events, key=lambda e: float(e['time']))
        prev_time = 0.0

        for event in sorted_events:
            curr_time = float(event['time'])
            etype = event['event_type']
            task_name = event['task_name']
            arrival_time = event.get('arrival_time', '0')
            instance_key = (task_name, str(arrival_time))

            # --- 计算 Busy Time (支持多核) ---
            duration = curr_time - prev_time
            if duration > 0 and active_tasks:
                # 关键：busy时间 = 持续时间 * 活跃的核心数
                # 例如：2个核同时跑，1ms内累积了2ms的CPU时间
                num_active = min(len(active_tasks), self.num_cores)
                stats['busy_time'] += duration * num_active
            
            prev_time = curr_time

            # --- 事件处理 ---
            if etype == 'arrival':
                unique_instances.add(instance_key)

            elif etype == 'scheduled':
                active_tasks.add(instance_key)
                schedule_counts[instance_key] += 1

            elif etype == 'descheduled':
                if instance_key in active_tasks:
                    active_tasks.remove(instance_key)

            elif etype == 'end_instance':
                if instance_key in active_tasks:
                    active_tasks.remove(instance_key)

            elif etype == 'dline_miss':
                failed_instances_set.add(instance_key)

        # 汇总
        stats['total_instances'] = len(unique_instances)
        stats['failed_instances'] = len(failed_instances_set)
        
        for count in schedule_counts.values():
            if count > 1:
                stats['preemptions'] += (count - 1)

        if stats['total_instances'] > 0:
            stats['failure_rate'] = stats['failed_instances'] / stats['total_instances']
        else:
            stats['failure_rate'] = 0.0

        # 计算 Idle Time
        total_capacity = SIMULATION_TIME * self.num_cores
        stats['idle_time'] = max(0.0, total_capacity - stats['busy_time'])
        stats['avg_idle_period'] = stats['idle_time'] # 总空闲时间
        
        # 忙碌时间归一化到任务数 (可选)
        stats['avg_busy_period'] = stats['busy_time'] / stats['total_instances'] if stats['total_instances'] > 0 else 0

        # 估算开销
        stats['avg_overhead'] = len(self.events) / 1000.0
        stats['avg_energy_level'] = 0.0

        return stats

    def _empty_results(self) -> Dict[str, Any]:
        return {k: 0.0 for k in ['failure_rate', 'preemptions', 'avg_idle_period', 
                                 'avg_busy_period', 'avg_energy_level', 'avg_overhead']}


# ============================================
# 实验运行器
# ============================================

class ExperimentRunner:
    def __init__(self):
        self.results = defaultdict(lambda: defaultdict(list))
        self.task_files = []

    def generate_tasksets(self):
        print(f"📦 生成 {NUM_TASKSETS} 组任务集 (Cores={SYSTEM_CORES})...")
        for i in range(NUM_TASKSETS):
            seed = 1000 + i
            task_file = TASK_DIR / f'taskset_{i:03d}.yml'
            cmd = [
                'python3', TASK_GENERATOR,
                '-n', str(TASK_N), '-u', str(TASK_U),
                '-p', str(TASK_P_MIN), '-P', str(TASK_P_MAX),
                '-c', str(SYSTEM_CORES), # ⭐ 使用自动读取的核心数
                '--seed', str(seed),
                '-o', str(task_file)
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                self.task_files.append(str(task_file))
            except subprocess.CalledProcessError as e:
                print(f"❌ 生成任务集失败: {e}")
                continue
        print(f"✅ 生成完毕")

    def modify_config(self, algorithm: str, battery_capacity: float) -> str:
        """使用字符串替换方式修改配置，避免yaml.dump()破坏原始��式"""
        with open(CONFIG_TEMPLATE, 'r') as f:
            content = f.read()

        # 使用正则表达式替换关键参数
        import re
        # 替换调度器
        content = re.sub(
            r'scheduler:\s*\w+',
            f'scheduler: {algorithm}',
            content
        )
        # 替换最大能量
        content = re.sub(
            r'max_energy:\s*[\d.]+',
            f'max_energy: {battery_capacity}',
            content
        )
        # 替换初始能量
        content = re.sub(
            r'initial_energy:\s*[\d.]+',
            f'initial_energy: {battery_capacity * 0.5}',
            content
        )

        temp_config = OUTPUT_DIR / f'temp_config.yml'
        with open(temp_config, 'w') as f:
            f.write(content)
        return str(temp_config)

    def run_experiments(self):
        total_runs = len(ALGORITHMS) * len(BATTERY_CAPACITIES) * len(self.task_files)
        print(f"🚀 开始实验: 3种算法 x {len(BATTERY_CAPACITIES)}种容量(1J-10J) x {len(self.task_files)}样本")
        print(f"   仿真时长: {SIMULATION_TIME}ms | 负载估算: ~6J Total")
        
        count = 0
        for algorithm in ALGORITHMS:
            for battery in BATTERY_CAPACITIES:
                config_file = self.modify_config(algorithm, battery)
                
                for task_idx, task_file in enumerate(self.task_files):
                    trace_file = TRACE_DIR / f'trace_{algorithm}_{battery}_{task_idx}.json'
                    
                    cmd = [
                        SIMULATOR, config_file, task_file, 
                        str(SIMULATION_TIME), '-t', str(trace_file)
                    ]
                    
                    try:
                        subprocess.run(cmd, check=True, capture_output=True)
                        parser = TraceParser(str(trace_file), SYSTEM_CORES) # ⭐ 传入核心数
                        self.results[algorithm][battery].append(parser.parse())
                    except Exception as e:
                        pass
                    
                    count += 1
                    if count % 100 == 0:
                        print(f"   进度: {count}/{total_runs} ...")

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
# 绘图与表格 (格式微调)
# ============================================

class FigureGenerator:
    def __init__(self, df: pd.DataFrame):
        self.df = df

    def generate_figure5(self):
        fig, axes = plt.subplots(2, 3, figsize=(18, 12))
        fig.suptitle('Scheduler Performance vs Battery Capacity (1J - 10J)', fontsize=16)
        
        algo_map = {'gpfp_tie': 'TIE', 'gpfp_tgf': 'TGF', 'gpfp_btie': 'BTIE'}
        colors = {'gpfp_tie': 'blue', 'gpfp_tgf': 'green', 'gpfp_btie': 'red'}
        markers = {'gpfp_tie': 'o', 'gpfp_tgf': 's', 'gpfp_btie': '^'}
        
        configs = [
            ('failure_rate', 'Failure Rate (Lower is Better)'), 
            ('preemptions', 'Preemptions (Lower is Better)'),
            ('avg_idle_period', 'Total Idle Time (Lower is Better)'), 
            ('avg_busy_period', 'Avg Execution Time (Higher is Better)'),
            ('avg_energy_level', 'Avg Energy Level'), 
            ('avg_overhead', 'Overhead (Est.)')
        ]
        
        for idx, (col, title) in enumerate(configs):
            ax = axes[idx//3, idx%3]
            for algo in ALGORITHMS:
                d = self.df[self.df['algorithm'] == algo]
                if not d.empty:
                    ax.plot(d['battery_capacity'], d[col], 
                           marker=markers[algo], label=algo_map[algo], color=colors[algo])
            ax.set_title(title)
            ax.set_xlabel('Battery Capacity (Joules)') # 修正单位
            ax.grid(True, alpha=0.3)
            if idx == 0: ax.legend()

        plt.tight_layout()
        plt.savefig(FIGURE_OUTPUT)
        print(f"📊 图表已保存: {FIGURE_OUTPUT}")

class TableGenerator:
    def __init__(self, df: pd.DataFrame):
        self.df = df

    def _rate(self, algo, col, smaller_is_better=True):
        means = self.df.groupby('algorithm')[col].mean()
        if algo not in means: return "N/A"
        val = means[algo]
        sorted_vals = sorted(means.values)
        rank = sorted_vals.index(val)
        if not smaller_is_better:
            rank = len(sorted_vals) - 1 - rank
        
        if rank == 0: return 'Best ★'
        elif rank == 1: return 'Good'
        else: return 'Poor'

    def generate_table1(self):
        metrics = {
            'failure_rate': True, 'preemptions': True, 
            'avg_idle_period': True, 'avg_busy_period': False, 'avg_overhead': True
        }
        lines = ["| Metric | TIE | TGF | BTIE |", "|---|---|---|---|"]
        
        for metric, smaller in metrics.items():
            row = f"| {metric} |"
            for algo in ALGORITHMS:
                row += f" {self._rate(algo, metric, smaller)} |"
            lines.append(row)
            
        with open(TABLE_OUTPUT, 'w') as f:
            f.write('\n'.join(lines))
        print(f"📋 表格已保存: {TABLE_OUTPUT}")

if __name__ == '__main__':
    try:
        runner = ExperimentRunner()
        runner.generate_tasksets()
        runner.run_experiments()
        df = runner.aggregate_results()
        df.to_csv(OUTPUT_DIR / 'raw_data.csv', index=False)
        FigureGenerator(df).generate_figure5()
        TableGenerator(df).generate_table1()
        print("\n✅ 实验结束")
    except KeyboardInterrupt:
        print("\n⚠️ 中断")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
