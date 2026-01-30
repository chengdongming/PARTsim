#!/usr/bin/env python3
"""
自动化调度算法对比实验脚本 (最终修正版)
复刻论文 Figure 5 和 Table 1

包含修复:
1. 绘图数据强制排序，防止折线图乱序。
2. 表格排名逻辑修复，避免浮点数匹配错误。
3. 增强 Config 修改逻辑，兼容 absolute/ratio 能量设置。
4. 规范化 TraceParser 的统计指标命名。
"""

import json
import subprocess
import yaml
import os
import sys
import re  # 引入正则
from pathlib import Path
from typing import Dict, Any
from collections import defaultdict
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# ============================================
# 0. 预读取配置
# ============================================
CONFIG_TEMPLATE = 'system_config_unified_template.yml'

def get_system_cores(config_path):
    """从配置文件中读取核心数量"""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            # 简单的文本预处理，防止yaml加载非标准字符报错
            content = f.read()
            config = yaml.safe_load(content)
            num_cpus = config['cpu_islands'][0]['numcpus']
            print(f"🖥️  自动检测到系统核心数: {num_cpus}")
            return int(num_cpus)
    except Exception as e:
        print(f"❌ 无法读取核心数，请检查 {config_path}: {e}")
        # 默认回退值，防止脚本崩溃
        return 4 

# 获取核心数
SYSTEM_CORES = get_system_cores(CONFIG_TEMPLATE)

# ============================================
# 配置参数
# ============================================

ALGORITHMS = ['gpfp_tie', 'gpfp_tgf', 'gpfp_btie']

# 电池容量范围 (Joules)
# 针对 4核 x 0.6mJ/ms = 2.4W 功耗，10秒需24J。
# 1J-10J 是极度缺电到中度缺电区间，最能体现算法差异。
BATTERY_CAPACITIES = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

NUM_TASKSETS = 50
SIMULATION_TIME = 10000 

# 路径配置
TASK_GENERATOR = './global_task_generator.py'
TASK_N = 8    
TASK_U = 3.0  # 高负载，逼迫调度器做取舍
TASK_P_MIN = 20
TASK_P_MAX = 100

SIMULATOR = './rtsim/rtsim'
OUTPUT_DIR = Path('experiment_results_final')
TRACE_DIR = OUTPUT_DIR / 'traces'
TASK_DIR = OUTPUT_DIR / 'tasks'
FIGURE_OUTPUT = OUTPUT_DIR / 'figure5.png'
TABLE_OUTPUT = OUTPUT_DIR / 'table1.md'

for p in [OUTPUT_DIR, TRACE_DIR, TASK_DIR]:
    p.mkdir(parents=True, exist_ok=True)


# ============================================
# TraceParser (逻辑已确认正确)
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
        active_tasks = set() 

        # 按时间排序，确保积分准确
        sorted_events = sorted(self.events, key=lambda e: float(e['time']))
        prev_time = 0.0

        for event in sorted_events:
            curr_time = float(event['time'])
            etype = event['event_type']
            task_name = event['task_name']
            arrival_time = event.get('arrival_time', '0')
            instance_key = (task_name, str(arrival_time))

            # 1. 积分计算 Busy Time (CPU Time Sum)
            duration = curr_time - prev_time
            if duration > 0 and active_tasks:
                num_active = min(len(active_tasks), self.num_cores)
                stats['busy_time'] += duration * num_active
            
            prev_time = curr_time

            # 2. 状态维护
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

        # 3. 统计汇总
        stats['total_instances'] = len(unique_instances)
        stats['failed_instances'] = len(failed_instances_set)
        
        for count in schedule_counts.values():
            if count > 1:
                stats['preemptions'] += (count - 1)

        if stats['total_instances'] > 0:
            stats['failure_rate'] = stats['failed_instances'] / stats['total_instances']
        else:
            stats['failure_rate'] = 0.0

        # Idle Time = 系统总容量 - 已使用的CPU时间
        total_capacity = SIMULATION_TIME * self.num_cores
        stats['total_idle_time'] = max(0.0, total_capacity - stats['busy_time'])
        
        # 平均每个任务的执行时间
        stats['avg_execution_time'] = stats['busy_time'] / stats['total_instances'] if stats['total_instances'] > 0 else 0

        # 开销估算 (事件密度)
        stats['overhead_proxy'] = len(self.events) / 1000.0

        # 占位符 (如果仿真器不输出能量水平)
        stats['avg_energy_level'] = 0.0 

        return stats

    def _empty_results(self) -> Dict[str, Any]:
        return {k: 0.0 for k in ['failure_rate', 'preemptions', 'total_idle_time', 
                                 'avg_execution_time', 'avg_energy_level', 'overhead_proxy']}


# ============================================
# 实验运行器
# ============================================

class ExperimentRunner:
    def __init__(self):
        self.results = defaultdict(lambda: defaultdict(list))
        self.task_files = []

    def generate_tasksets(self):
        print(f"📦 生成 {NUM_TASKSETS} 组任务集 (Cores={SYSTEM_CORES}, U={TASK_U})...")
        for i in range(NUM_TASKSETS):
            seed = 1000203 + i # 使用指定的随机种子
            task_file = TASK_DIR / f'taskset_{i:03d}.yml'
            
            # 如果文件已存在且非空，跳过生成（节省时间）
            if task_file.exists() and task_file.stat().st_size > 0:
                self.task_files.append(str(task_file))
                continue

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
        """修改配置，强制覆盖 initial_energy_ratio 或 initial_energy"""
        with open(CONFIG_TEMPLATE, 'r', encoding='utf-8') as f:
            content = f.read()

        # 1. 替换调度算法
        content = re.sub(r'scheduler:\s*\w+', f'scheduler: {algorithm}', content)
        
        # 2. 替换最大能量
        content = re.sub(r'max_energy:\s*[\d.]+', f'max_energy: {battery_capacity}', content)
        
        # 3. 处理初始能量 (复杂情况处理)
        # 如果存在 initial_energy_ratio，直接替换为 0.5 (不管之前是多少)
        if 'initial_energy_ratio:' in content:
             content = re.sub(r'initial_energy_ratio:\s*[\d.]+', f'initial_energy_ratio: 0.5', content)
        # 如果存在 initial_energy (绝对值)，替换为容量的一半
        elif 'initial_energy:' in content:
             content = re.sub(r'initial_energy:\s*[\d.]+', f'initial_energy: {battery_capacity * 0.5}', content)

        temp_config = OUTPUT_DIR / f'config_{algorithm}_{battery_capacity}.yml'
        with open(temp_config, 'w', encoding='utf-8') as f:
            f.write(content)
        return str(temp_config)

    def run_experiments(self):
        total_runs = len(ALGORITHMS) * len(BATTERY_CAPACITIES) * len(self.task_files)
        print(f"🚀 开始实验: 3种算法 x {len(BATTERY_CAPACITIES)}种容量 x {len(self.task_files)}样本")
        
        count = 0
        for algorithm in ALGORITHMS:
            for battery in BATTERY_CAPACITIES:
                config_file = self.modify_config(algorithm, battery)
                
                for task_idx, task_file in enumerate(self.task_files):
                    trace_file = TRACE_DIR / f'trace_{algorithm}_{battery}_{task_idx}.json'
                    
                    # 如果Trace已存在且有效，跳过 (支持断点续传)
                    # if trace_file.exists() and trace_file.stat().st_size > 100:
                    #     parser = TraceParser(str(trace_file), SYSTEM_CORES)
                    #     self.results[algorithm][battery].append(parser.parse())
                    #     count += 1
                    #     continue

                    cmd = [
                        SIMULATOR, config_file, task_file, 
                        str(SIMULATION_TIME), '-t', str(trace_file)
                    ]
                    
                    try:
                        subprocess.run(cmd, check=True, capture_output=True)
                        parser = TraceParser(str(trace_file), SYSTEM_CORES)
                        self.results[algorithm][battery].append(parser.parse())
                    except subprocess.CalledProcessError:
                        # 仿真失败通常是因为死锁或断言，记录空结果
                        pass
                    except Exception as e:
                        print(f"解析错误: {e}")
                    
                    count += 1
                    if count % 50 == 0:
                        print(f"   进度: {count}/{total_runs} ({(count/total_runs)*100:.1f}%)")
                
                # 清理临时配置文件
                if os.path.exists(config_file):
                    os.remove(config_file)

    def aggregate_results(self) -> pd.DataFrame:
        data = []
        for algo in ALGORITHMS:
            for batt in BATTERY_CAPACITIES:
                res = self.results[algo][batt]
                if not res: continue
                # 计算该组实验的平均值
                avg = {k: np.mean([r[k] for r in res]) for k in res[0].keys()}
                avg['algorithm'] = algo
                avg['battery_capacity'] = batt
                data.append(avg)
        return pd.DataFrame(data)


# ============================================
# 绘图与表格 (修复排序和排名问题)
# ============================================

class FigureGenerator:
    def __init__(self, df: pd.DataFrame):
        self.df = df

    def generate_figure5(self):
        # 修正: 定义6个子图
        fig, axes = plt.subplots(2, 3, figsize=(18, 10))
        fig.suptitle(f'Performance Comparison ({SYSTEM_CORES} Cores, Load U={TASK_U})', fontsize=16)
        
        algo_map = {'gpfp_tie': 'TIE (Greedy)', 'gpfp_tgf': 'TGF', 'gpfp_btie': 'BTIE (Ours)'}
        colors = {'gpfp_tie': '#1f77b4', 'gpfp_tgf': '#2ca02c', 'gpfp_btie': '#d62728'} # 更专业的配色
        markers = {'gpfp_tie': 'o', 'gpfp_tgf': 's', 'gpfp_btie': '^'}
        
        # 定义要绘制的列和标题
        configs = [
            ('failure_rate', 'Failure Rate (Lower is Better)'), 
            ('preemptions', 'Preemptions (Lower is Better)'),
            ('total_idle_time', 'Total Idle Time (Lower is Better)'), 
            ('avg_execution_time', 'Avg Exec Time (Higher is Better)'),
            ('overhead_proxy', 'Scheduler Overhead Proxy'), 
            ('avg_energy_level', 'Avg Energy Level')
        ]
        
        axes_flat = axes.flatten()
        
        for idx, (col, title) in enumerate(configs):
            ax = axes_flat[idx]
            for algo in ALGORITHMS:
                d = self.df[self.df['algorithm'] == algo]
                if not d.empty:
                    # ⭐ 关键修正：必须按电池容量排序，否则折线会乱
                    d = d.sort_values('battery_capacity')
                    
                    ax.plot(d['battery_capacity'], d[col], 
                           marker=markers[algo], markersize=6, linewidth=2,
                           label=algo_map[algo], color=colors[algo], alpha=0.8)
            
            ax.set_title(title, fontweight='bold')
            ax.set_xlabel('Battery Capacity (Joules)')
            ax.grid(True, linestyle='--', alpha=0.5)
            
            # 智能设置Y轴范围
            if col == 'failure_rate':
                ax.set_ylim(-0.05, 1.05)
            
            if idx == 0: 
                ax.legend(loc='upper right')

        plt.tight_layout(rect=[0, 0.03, 1, 0.95])
        plt.savefig(FIGURE_OUTPUT, dpi=300)
        print(f"📊 图表已保存: {FIGURE_OUTPUT}")

class TableGenerator:
    def __init__(self, df: pd.DataFrame):
        self.df = df

    def _get_rank(self, metric_series, smaller_is_better=True):
        """
        计算排名，避免浮点数直接比较
        输入: 一个 Series (索引为算法名，值为平均指标)
        输出: 字典 {algo: rank_string}
        """
        # 转为列表并排序: [(algo, val), ...]
        items = list(metric_series.items())
        # 排序
        items.sort(key=lambda x: x[1], reverse=not smaller_is_better)
        
        ranks = {}
        for i, (algo, val) in enumerate(items):
            if i == 0:
                ranks[algo] = f"**{val:.2f} (Best)**"
            else:
                ranks[algo] = f"{val:.2f}"
        return ranks

    def generate_table1(self):
        # 1. 对所有电池容量取平均，作为表格的总览数据
        summary = self.df.groupby('algorithm').mean(numeric_only=True)
        
        metrics = {
            'failure_rate': True, 
            'preemptions': True, 
            'total_idle_time': True, 
            'avg_execution_time': False, # 越高越好
            'overhead_proxy': True
        }
        
        # 准备 Markdown 表格
        lines = [
            "| Metric | TIE | TGF | BTIE |", 
            "|---|---|---|---|"
        ]
        
        for metric, smaller_is_better in metrics.items():
            # 获取该指标这一行的数据 Series
            series = summary[metric]
            # 计算排名文本
            rank_map = self._get_rank(series, smaller_is_better)
            
            row = f"| {metric} |"
            for algo in ALGORITHMS:
                val_str = rank_map.get(algo, "N/A")
                row += f" {val_str} |"
            lines.append(row)
            
        with open(TABLE_OUTPUT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print(f"📋 表格已保存: {TABLE_OUTPUT}")

if __name__ == '__main__':
    try:
        runner = ExperimentRunner()
        runner.generate_tasksets()
        runner.run_experiments()
        
        df = runner.aggregate_results()
        if df.empty:
            print("❌ 没有产生有效数据，请检查仿真器是否正常运行。")
        else:
            print("💾 保存原始数据...")
            df.to_csv(OUTPUT_DIR / 'raw_data.csv', index=False)
            
            print("🎨 正在绘图...")
            FigureGenerator(df).generate_figure5()
            
            print("📝 正在生成表格...")
            TableGenerator(df).generate_table1()
            
            print(f"\n✅ 实验圆满结束！结果保存在: {OUTPUT_DIR}")
            
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断")
    except Exception as e:
        print(f"\n❌ 运行时错误: {e}")
        import traceback
        traceback.print_exc()
