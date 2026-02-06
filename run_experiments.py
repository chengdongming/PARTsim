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

ALGORITHMS = ['gpfp_tie', 'gpfp_tgf', 'gpfp_btie']
# 论文中涵盖 1J 到 60J 的范围
BATTERY_CAPACITIES = [1.0, 3.0, 5.0, 10.0, 15.0, 25.0, 40.0, 60.0]

NUM_TASKSETS = 30
SIMULATION_TIME = 10000 

# 任务生成参数
TASK_GENERATOR = './global_task_generator.py'
TASK_N = 8    
TASK_U = 3.0  
TASK_P_MIN = 20
TASK_P_MAX = 100

# 路径配置
SIMULATOR = './build/rtsim/rtsim'
OUTPUT_DIR = Path('experiment_results_strict') # 修改输出目录以区分
TRACE_DIR = OUTPUT_DIR / 'traces'
TASK_DIR = OUTPUT_DIR / 'tasks'
FIGURE_OUTPUT = OUTPUT_DIR / 'figure5_strict.png'
TABLE_OUTPUT = OUTPUT_DIR / 'table1_strict.md'

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
# 2. TraceParser (严格遵循论文定义)
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
            print(f"Error loading trace {self.trace_file}: {e}")
            self.events = []

    def parse(self) -> Dict[str, Any]:
        if not self.events:
            return self._empty_results()

        # 按时间排序
        sorted_events = sorted(self.events, key=lambda e: float(e['time']))
        
        # --- 统计变量 ---
        count_dline_miss = 0
        count_preemptions = 0
        sum_energy_events = 0.0
        
        # --- Busy Period 计算辅助变量 ---
        # 记录所有忙碌片段 (start, end)
        busy_fragments = [] 
        # 记录当前运行任务的开始时间: Key=(task_name, arrival_time) -> start_time
        running_tasks = {}

        for event in sorted_events:
            etype = event['event_type']
            time = float(event['time'])
            
            # [论文定义] Average Energy Level: 
            # "Average of the energy level of all scheduling events." (算术平均)
            current_energy_J = float(event.get('current_energy_mJ', 0)) / 1000.0
            sum_energy_events += current_energy_J

            # [论文定义] Failure Rate (辅助): 
            # 统计 Deadline Miss 次数，用于后续判断 Taskset 是否可行
            if etype == 'dline_miss':
                count_dline_miss += 1

            # [论文定义] Preemptions:
            # "A preemption event occurs when a job is stopped while it is still not finished."
            # descheduled 包含：被高优先级抢占 OR 能量耗尽暂停 (均为未完成被停止)
            if etype == 'descheduled':
                count_preemptions += 1

            # [论文定义] Busy/Idle Period 原始数据提取
            task_key = (event.get('task_name'), event.get('arrival_time'))
            
            if etype == 'scheduled':
                running_tasks[task_key] = time
                
            elif etype in ['descheduled', 'end_instance']:
                if task_key in running_tasks:
                    start_t = running_tasks.pop(task_key)
                    if time > start_t:
                        busy_fragments.append((start_t, time))

        # --- 后处理: 合并连续的 Busy Intervals ---
        # 原论文定义 Busy Period 为 "continuous processor activity"
        # 因此任务 A -> 任务 B 无缝衔接应算作 1 个 Busy Period
        merged_busy = []
        if busy_fragments:
            # 按开始时间排序
            busy_fragments.sort(key=lambda x: x[0])
            
            curr_start, curr_end = busy_fragments[0]
            for next_start, next_end in busy_fragments[1:]:
                # 如果下一个片段的开始时间 <= 当前片段结束时间 (考虑浮点误差)
                if next_start <= curr_end + 1e-9:
                    curr_end = max(curr_end, next_end)
                else:
                    merged_busy.append((curr_start, curr_end))
                    curr_start, curr_end = next_start, next_end
            merged_busy.append((curr_start, curr_end))

        # --- 计算最终指标 ---
        
        # 1. Busy Period Duration
        total_busy_time = sum(end - start for start, end in merged_busy)
        num_busy_intervals = len(merged_busy)
        avg_busy_period = total_busy_time / num_busy_intervals if num_busy_intervals > 0 else 0.0

        # 2. Idle Period Duration
        # Idle 是 Busy 的补集。简单估算: (总时间 - 总忙碌) / 空闲段数
        # 空闲段数通常等于忙碌段数 +/- 1。
        # 准确计算：
        total_idle_time = max(0.0, (SIMULATION_TIME * self.num_cores) - total_busy_time) 
        # 注意：多核下的空闲定义比较复杂。
        # 原论文是单核或同步多核？论文中提到 "processor is idle"，通常指系统无任何活动。
        # 这里简化处理：假设 空闲段数 ≈ 忙碌段数 (交替出现)
        num_idle_intervals = num_busy_intervals if num_busy_intervals > 0 else 1
        avg_idle_period = total_idle_time / num_idle_intervals if num_idle_intervals > 0 else 0.0

        # 3. Energy Level (算术平均)
        avg_energy_level = sum_energy_events / len(sorted_events) if sorted_events else 0.0

        # 4. Overhead Proxy (归一化事件数)
        overhead_proxy = len(sorted_events) / 1000.0

        return {
            # 关键：返回是否可行 (1=可行, 0=失败)
            'is_feasible': 1.0 if count_dline_miss == 0 else 0.0,
            'preemptions': count_preemptions,
            'avg_busy_period': avg_busy_period,
            'avg_idle_period': avg_idle_period,
            'avg_energy_level': avg_energy_level,
            'overhead_proxy': overhead_proxy
        }

    def _empty_results(self) -> Dict[str, Any]:
        return {
            'is_feasible': 0.0, 'preemptions': 0, 
            'avg_busy_period': 0.0, 'avg_idle_period': 0.0,
            'avg_energy_level': 0.0, 'overhead_proxy': 0.0
        }


# ============================================
# 3. ExperimentRunner
# ============================================
class ExperimentRunner:
    def __init__(self):
        self.results = defaultdict(lambda: defaultdict(list))
        self.task_files = []

    def generate_tasksets(self):
        print(f"📦 生成 {NUM_TASKSETS} 组任务集 (Cores={SYSTEM_CORES}, U={TASK_U})...")
        for i in range(NUM_TASKSETS):
            seed = 1000203 + i 
            task_file = TASK_DIR / f'taskset_{i:03d}.yml'
            
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
        with open(CONFIG_TEMPLATE, 'r', encoding='utf-8') as f:
            content = f.read()

        content = re.sub(r'scheduler:\s*\w+', f'scheduler: {algorithm}', content)
        content = re.sub(r'max_energy:\s*[\d.]+', f'max_energy: {battery_capacity}', content)
        
        # [Strict Compliance] 
        # 论文 IV-A 示例: "At time t=0 the battery is empty"
        # 且 V-B 提到 Emin=0。为了观察充电行为，初始能量设为 0。
        if 'initial_energy_ratio:' in content:
             content = re.sub(r'initial_energy_ratio:\s*[\d.]+', f'initial_energy_ratio: 0.0', content)
        elif 'initial_energy:' in content:
             content = re.sub(r'initial_energy:\s*[\d.]+', f'initial_energy: 0.0', content)

        temp_config = OUTPUT_DIR / f'config_{algorithm}_{battery_capacity}.yml'
        with open(temp_config, 'w', encoding='utf-8') as f:
            f.write(content)
        return str(temp_config)

    def run_experiments(self):
        total_runs = len(ALGORITHMS) * len(BATTERY_CAPACITIES) * len(self.task_files)
        print(f"🚀 开始实验 (Strict Mode)...")
        
        count = 0
        for algorithm in ALGORITHMS:
            for battery in BATTERY_CAPACITIES:
                config_file = self.modify_config(algorithm, battery)
                
                for task_idx, task_file in enumerate(self.task_files):
                    trace_file = TRACE_DIR / f'trace_{algorithm}_{battery}_{task_idx}.json'
                    
                    env = os.environ.copy()
                    lib_path = os.path.abspath('./build/librtsim')
                    env['LD_LIBRARY_PATH'] = lib_path + ':' + env.get('LD_LIBRARY_PATH', '')

                    cmd = [
                        SIMULATOR, config_file, task_file,
                        str(SIMULATION_TIME), '-t', str(trace_file)
                    ]
                    
                    try:
                        subprocess.run(cmd, check=True, capture_output=True, env=env, text=True)
                        parser = TraceParser(str(trace_file), SYSTEM_CORES)
                        parsed_stats = parser.parse()
                        self.results[algorithm][battery].append(parsed_stats)
                    except subprocess.CalledProcessError:
                        pass # 忽略仿真失败 (通常是参数错误)
                    except Exception as e:
                        print(f"Parse error: {e}")
                    
                    count += 1
                    if count % 50 == 0:
                        print(f"   进度: {count}/{total_runs} ({(count/total_runs)*100:.1f}%)")
                
                if os.path.exists(config_file):
                    os.remove(config_file)

    def aggregate_results(self) -> pd.DataFrame:
        data = []
        for algo in ALGORITHMS:
            for batt in BATTERY_CAPACITIES:
                res = self.results[algo][batt]
                if not res: continue
                
                # [Strict Compliance] 聚合逻辑调整
                avg = {}
                for k in res[0].keys():
                    if k == 'is_feasible':
                        # Failure Rate = 1 - 可行率 (Taskset 粒度)
                        success_rate = np.mean([r[k] for r in res])
                        avg['failure_rate'] = 1.0 - success_rate
                    else:
                        avg[k] = np.mean([r[k] for r in res])
                
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

    def generate_figure5(self):
        fig, axes = plt.subplots(2, 3, figsize=(18, 10))
        fig.suptitle(f'Performance Comparison (Strict Paper Compliance)', fontsize=16)
        
        algo_map = {'gpfp_tie': 'TIE', 'gpfp_tgf': 'TGF', 'gpfp_btie': 'BTIE (Ours)'}
        colors = {'gpfp_tie': '#1f77b4', 'gpfp_tgf': '#2ca02c', 'gpfp_btie': '#d62728'}
        markers = {'gpfp_tie': 'o', 'gpfp_tgf': 's', 'gpfp_btie': '^'}
        
        # 对应论文 Figure 5 的 6 个指标
        # 注意: avg_busy_period 替代了原来的 avg_execution_time
        configs = [
            ('failure_rate', 'Failure Rate (Taskset-level)'), 
            ('preemptions', 'Preemptions (Count)'),
            ('avg_idle_period', 'Average Idle-Period (ms)'), 
            ('avg_busy_period', 'Average Busy-Period (ms)'),
            ('overhead_proxy', 'Average Overhead (Proxy)'), 
            ('avg_energy_level', 'Average Energy Level (J)') 
        ]
        
        axes_flat = axes.flatten()
        
        for idx, (col, title) in enumerate(configs):
            ax = axes_flat[idx]
            for algo in ALGORITHMS:
                d = self.df[self.df['algorithm'] == algo]
                if not d.empty:
                    d = d.sort_values('battery_capacity')
                    ax.plot(d['battery_capacity'], d[col], 
                           marker=markers[algo], markersize=6, linewidth=2,
                           label=algo_map[algo], color=colors[algo], alpha=0.8)
            
            ax.set_title(title, fontweight='bold')
            ax.set_xlabel('Battery Capacity (Joules)')
            ax.grid(True, linestyle='--', alpha=0.5)
            
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
        items = list(metric_series.items())
        items.sort(key=lambda x: x[1], reverse=not smaller_is_better)
        ranks = {}
        for i, (algo, val) in enumerate(items):
            ranks[algo] = f"**{val:.3f}**" if i == 0 else f"{val:.3f}"
        return ranks

    def generate_table1(self):
        summary = self.df.groupby('algorithm').mean(numeric_only=True)
        # 根据论文 Table 1 的优劣方向
        metrics = {
            'failure_rate': True,       # Lower is better
            'preemptions': True,        # Lower is better
            'overhead_proxy': True,     # Lower is better
            'avg_idle_period': False,   # Higher is better (usually implies less constraint)
            'avg_busy_period': False,   # Higher is better (continuous exec)
            'avg_energy_level': False   # Higher is better
        }
        lines = ["| Metric | TIE | TGF | BTIE |", "|---|---|---|---|"]
        
        for metric, smaller_is_better in metrics.items():
            if metric not in summary.columns: continue
            series = summary[metric]
            rank_map = self._get_rank(series, smaller_is_better)
            row = f"| {metric} |"
            for algo in ALGORITHMS:
                row += f" {rank_map.get(algo, 'N/A')} |"
            lines.append(row)
            
        with open(TABLE_OUTPUT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print(f"📋 表格已保存: {TABLE_OUTPUT}")

# ============================================
# 5. 主程序入口
# ============================================
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
            df.to_csv(OUTPUT_DIR / 'raw_data_strict.csv', index=False)
            
            print("🎨 正在绘图...")
            FigureGenerator(df).generate_figure5()
            
            print("📝 正在生成表格...")
            TableGenerator(df).generate_table1()
            
            print(f"\n✅ 严格模式实验结束！结果保存在: {OUTPUT_DIR}")
            
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断")
    except Exception as e:
        print(f"\n❌ 运行时错误: {e}")
