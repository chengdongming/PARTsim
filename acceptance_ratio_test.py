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
import re
import argparse
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

SYSTEM_CORES = get_system_cores(CONFIG_TEMPLATE)

# 算法配置
ALGORITHMS = ['gpfp_tie', 'gpfp_tgf', 'gpfp_btie']
ALGO_DISPLAY_NAMES = {'gpfp_tie': 'TIE', 'gpfp_tgf': 'TGF', 'gpfp_btie': 'BTIE'}

# 实验参数（可通过命令行修改）
DEFAULT_UTILIZATION_POINTS = np.linspace(0.1, 1.0, 10)
DEFAULT_NUM_TASKSETS = 10  # 快速测试：10个任务集
DEFAULT_TASK_N = 10
DEFAULT_TASK_P_MIN = 40  # 周期范围翻倍：从20变为40
DEFAULT_TASK_P_MAX = 200  # 周期范围翻倍：从100变为200
DEFAULT_SIMULATION_TIME = 20000  # 20秒仿真，让能量约束生效
DEFAULT_BATTERY_CAPACITY = 15.0  # 15J电池，平衡能量约束
DEFAULT_INITIAL_ENERGY_RATIO = 0.4  # 40%初始能量，需要太阳能补充
DEFAULT_SOLAR_START_TIME_MS = 43200000  # 中午12点（12 * 3600 * 1000 ms）

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
        计算二元可调度性（Binary Schedulability）

        逻辑：
        - 如果追踪文件为空或无效 -> 返回 0.0（失败）
        - 如果存在任何 'dline_miss' 事件 -> 返回 0.0（任务集失败，一票否决）
        - 如果没有任何 'dline_miss' 且至少有一个 'arrival' -> 返回 1.0（任务集成功）
        - 如果没有任何任务到达 -> 返回 0.0（无效测试）

        这与作业级成功率不同：不返回 0.99 这样的分数，只返回 0.0 或 1.0
        """
        if not self.events:
            # 空追踪文件视为失败
            return 0.0

        has_arrivals = False
        has_deadline_miss = False

        for event in self.events:
            event_type = event.get('event_type', '')

            if event_type == 'arrival':
                has_arrivals = True
            elif event_type == 'dline_miss':
                has_deadline_miss = True
                # 一旦发现截止期错过，立即判定为失败（一票否决）
                break

        # 二元判定逻辑
        if not has_arrivals:
            # 没有任务到达，视为无效测试
            return 0.0

        if has_deadline_miss:
            # 存在截止期错过，任务集失败
            return 0.0
        else:
            # 没有截止期错过，任务集成功
            return 1.0

# ============================================
# 实验执行器
# ============================================
class ExperimentRunner:
    """运行接受率实验"""

    def __init__(self, output_dir, utilization_points, num_tasksets,
                 task_n, task_p_min, task_p_max, simulation_time,
                 battery_capacity, initial_energy_ratio, solar_start_time_ms):
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

        print(f"🖥️  系统核心数: {SYSTEM_CORES}")
        print(f"📁 输出目录: {self.output_dir}")

    def generate_taskset(self, utilization, task_idx, seed):
        """生成指定利用率的任务集"""
        task_file = self.task_dir / f'taskset_u{utilization:.2f}_{task_idx:03d}.yml'

        # 计算总利用率（归一化利用率 × 核心数）
        total_utilization = utilization * SYSTEM_CORES

        # 修复：格式化为4位小数，防止浮点精度问题
        utilization_str = f"{total_utilization:.4f}"

        cmd = [
            'python3', TASK_GENERATOR,
            '-n', str(self.task_n),
            '-u', utilization_str,  # 使用格式化后的字符串
            '-p', str(self.task_p_min),
            '-P', str(self.task_p_max),
            '-c', str(SYSTEM_CORES),
            '--seed', str(seed),
            '-o', str(task_file)
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            return str(task_file)
        except Exception as e:
            print(f"❌ 生成任务集失败 (U={utilization:.2f}): {e}")
            return None

    def modify_config(self, algorithm: str):
        """修改系统配置文件"""
        with open(CONFIG_TEMPLATE, 'r', encoding='utf-8') as f:
            content = f.read()

        content = re.sub(r'scheduler:\s*\w+', f'scheduler: {algorithm}', content)
        content = re.sub(r'max_energy:\s*[\d.]+', f'max_energy: {self.battery_capacity}', content)

        # 设置初始能量
        initial_energy = self.battery_capacity * self.initial_energy_ratio
        if 'initial_energy_ratio:' in content:
            content = re.sub(r'initial_energy_ratio:\s*[\d.]+',
                           f'initial_energy_ratio: {self.initial_energy_ratio}', content)
        elif 'initial_energy:' in content:
            content = re.sub(r'initial_energy:\s*[\d.]+', f'initial_energy: {initial_energy}', content)

        # ⭐ 关键修复：设置time_of_day_ms
        # 根据用户测试验证，系统使用time_of_day_ms参数来确定太阳能收集的时间
        # 例如：12:00 PM = 12 * 3600 * 1000 = 43200000 ms
        solar_time_ms = self.solar_start_time_ms

        # 修改energy_management部分的time_of_day_ms参数
        content = re.sub(
            r'time_of_day_ms:\s*\d+',
            f'time_of_day_ms: {solar_time_ms}',
            content
        )

        temp_config = self.output_dir / f'config_{algorithm}.yml'
        with open(temp_config, 'w', encoding='utf-8') as f:
            f.write(content)
        return str(temp_config)

    def run_simulation(self, algorithm, config_file, task_file, utilization, task_idx):
        """运行单次仿真"""
        trace_file = self.trace_dir / f'trace_{algorithm}_u{utilization:.2f}_{task_idx}.json'

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
            print(f"⏱️ 仿真超时: {algorithm}, U={utilization:.2f}")
            return None
        except subprocess.CalledProcessError:
            print(f"❌ 仿真失败: {algorithm}, U={utilization:.2f}")
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

        # 为每个算法生成配置文件
        config_files = {}
        for algo in ALGORITHMS:
            config_files[algo] = self.modify_config(algo)

        count = 0
        for u_idx, utilization in enumerate(self.utilization_points):
            print(f"\n📊 处理利用率点 {u_idx+1}/{len(self.utilization_points)}: U_norm={utilization:.2f}")

            # 生成任务集
            task_files = []
            for task_idx in range(self.num_tasksets):
                seed = 2000 + int(utilization * 100) * 100 + task_idx
                task_file = self.generate_taskset(utilization, task_idx, seed)
                if task_file:
                    task_files.append(task_file)

            if not task_files:
                print(f"⚠️ 没有成功生成任务集，跳过 U={utilization:.2f}")
                continue

            # 对每个任务集运行三种算法
            for task_idx, task_file in enumerate(task_files):
                for algo in ALGORITHMS:
                    trace_file = self.run_simulation(algo, config_files[algo],
                                                    task_file, utilization, task_idx)

                    if trace_file and os.path.exists(trace_file):
                        # 解析追踪文件（二元可调度性）
                        parser = TraceParser(trace_file)
                        acceptance_ratio = parser.get_acceptance_ratio()
                        results[algo][utilization].append(acceptance_ratio)
                    else:
                        # 仿真失败视为任务集失败
                        results[algo][utilization].append(0.0)

                    count += 1
                    if count % 10 == 0:
                        print(f"   进度: {count}/{total_runs} ({(count/total_runs)*100:.1f}%)")

        # 清理临时配置文件
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
                results[display_name] = (x, y)

        return results

    @staticmethod
    def plot_acceptance_ratio(results, save_path, x_label=None):
        """绘制接受率图表"""
        # 修复：确保输出目录存在
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        # 创建图表
        fig, ax = plt.subplots(figsize=(8, 6))

        # 定义算法样式
        styles = {
            'TIE': {'color': 'blue', 'marker': 'o', 'label': 'TIE'},
            'TGF': {'color': 'green', 'marker': 's', 'label': 'TGF'},
            'BTIE': {'color': 'red', 'marker': '^', 'label': 'BTIE'}
        }

        # 绘制每个算法的曲线
        for algo_name in ['TIE', 'TGF', 'BTIE']:
            if algo_name not in results:
                continue
            x, y = results[algo_name]
            style = styles[algo_name]
            ax.plot(x, y,
                   color=style['color'],
                   marker=style['marker'],
                   markersize=6,
                   linewidth=2,
                   label=style['label'],
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

        return fig, ax

    @staticmethod
    def print_data_summary(results):
        """打印数据摘要"""
        print("\n📊 数据摘要:")
        for algo_name, (x, y) in results.items():
            print(f"{algo_name}:")
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
        description='接受率分析：实验执行 + 图表生成（二元可调度性）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 运行完整实验并生成图表
  python3 acceptance_ratio_analysis.py --run-experiment

  # 仅从已有数据生成图表
  python3 acceptance_ratio_analysis.py --csv acceptance_ratio_experiment/acceptance_ratio_data.csv

  # 自定义实验参数
  python3 acceptance_ratio_analysis.py --run-experiment --num-points 15 --num-tasksets 10

注意：本脚本实现二元可调度性评估（Binary Schedulability）
- 每个任务集要么完全成功（1.0），要么失败（0.0）
- 接受率 = 成功任务集数量 / 总任务集数量
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
    parser.add_argument('--task-n', type=int, default=10,
                       help='每个任务集的任务数 (默认: 10)')
    parser.add_argument('--battery', type=float, default=DEFAULT_BATTERY_CAPACITY,
                       help=f'电池容量 (Joules) (默认: {DEFAULT_BATTERY_CAPACITY})')
    parser.add_argument('--initial-energy', type=float, default=DEFAULT_INITIAL_ENERGY_RATIO,
                       help=f'初始能量比例 (0.0-1.0) (默认: {DEFAULT_INITIAL_ENERGY_RATIO})')
    parser.add_argument('--solar-time', type=int, default=int(DEFAULT_SOLAR_START_TIME_MS / 3600000),
                       help=f'太阳能收集开始时间（小时，0-23）(默认: {int(DEFAULT_SOLAR_START_TIME_MS / 3600000)})')

    # 图表参数
    parser.add_argument('--figure-output', type=str, default=None,
                       help='图表输出文件名')
    parser.add_argument('--x-label', type=str, default=None,
                       help='自定义X轴标签')

    args = parser.parse_args()

    # 决定数据来源
    if args.run_experiment:
        # 运行实验
        utilization_points = np.linspace(0.1, 1.0, args.num_points)

        # 计算太阳能开始时间（小时转毫秒）
        solar_start_time_ms = args.solar_time * 3600 * 1000

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
            solar_start_time_ms=solar_start_time_ms
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

        # 设置图表输出路径
        if args.figure_output:
            figure_path = args.figure_output
        else:
            csv_path = Path(args.csv)
            figure_path = csv_path.parent / 'acceptance_ratio_figure.png'

    else:
        print("❌ 错误：必须指定 --run-experiment 或 --csv")
        print("使用 --help 查看帮助信息")
        sys.exit(1)

    # 生成图表
    print("\n🎨 正在生成图表...")
    FigureGenerator.plot_acceptance_ratio(plot_data, figure_path, args.x_label)
    FigureGenerator.print_data_summary(plot_data)

    print(f"\n✅ 完成！")

if __name__ == '__main__':
    main()
