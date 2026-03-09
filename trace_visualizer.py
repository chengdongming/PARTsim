#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实时调度追踪文件可视化工具（增强版）
Real-Time Scheduling Trace File Visualizer (Enhanced)

根据JSON追踪文件生成标准调度甘特图
支持从PARTSim项目生成的调度追踪文件

使用方法:
    python3 trace_visualizer.py <trace_file> [options]

示例:
    python3 trace_visualizer.py trace.json
    python3 trace_visualizer.py trace.json --output my_chart.png
"""

import matplotlib
matplotlib.use('Agg')  # 使用非交互式后端
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import json
import yaml
import sys
import argparse
import os
from typing import List, Dict, Tuple, Any
from collections import defaultdict

# 配置中文字体
import matplotlib.font_manager as fm
font_path = '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'
font_prop = fm.FontProperties(fname=font_path)
plt.rcParams['font.family'] = font_prop.get_name()
plt.rcParams['axes.unicode_minus'] = False

# ===============================
# 默认配置
# ===============================
DEFAULT_CONFIG = {
    'figure_size': (20, 8),
    'dpi': 150,
    'format': 'png',
    'output': None,
    'title': None,
    'verbose': True,
}

# 学术配色方案
COLOR_SCHEME = {
    'task_0': '#3498db',   # 蓝色
    'task_1': '#e74c3c',   # 红色
    'task_2': '#2ecc71',   # 绿色
    'task_3': '#f39c12',   # 橙色
    'task_4': '#9b59b6',   # 紫色
    'task_5': '#1abc9c',   # 青色
    'task_6': '#34495e',   # 深灰
    'task_7': '#e67e22',   # 深橙
    'task_8': '#16a085',   # 深青
    'task_9': '#8e44ad',   # 深紫
}

def get_color_for_task(task_name: str) -> str:
    """为任务获取颜色"""
    if task_name in COLOR_SCHEME:
        return COLOR_SCHEME[task_name]
    hash_val = hash(task_name) % 16777215
    color = f'#{hash_val:06x}'
    COLOR_SCHEME[task_name] = color
    return color

# ===============================
# 命令行参数解析
# ===============================
def parse_arguments():
    parser = argparse.ArgumentParser(
        description='实时调度追踪文件可视化工具',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('trace_file', type=str, help='追踪文件路径 (JSON格式)')
    parser.add_argument('-o', '--output', type=str, default=None, help='输出文件名')
    parser.add_argument('-f', '--format', type=str, default='png', choices=['png', 'pdf', 'svg'])
    parser.add_argument('--dpi', type=int, default=150, help='图表分辨率')
    parser.add_argument('--width', type=float, default=20, help='图表宽度（英寸）')
    parser.add_argument('--height', type=float, default=8, help='图表高度（英寸）')
    parser.add_argument('-t', '--title', type=str, default=None, help='图表标题')
    parser.add_argument('--no-stats', action='store_true', help='不显示统计信息')
    parser.add_argument('--no-grid', action='store_true', help='不显示网格线')
    parser.add_argument('--taskset', type=str, default=None, help='任务集配置文件 (YAML)')

    return parser.parse_args()

# ===============================
# 追踪文件解析器（核心逻辑修复版）
# ===============================
class TraceParser:
    """追踪文件解析器 - 使用唯一实例标识"""

    def __init__(self, trace_file: str, verbose: bool = True):
        self.trace_file = trace_file
        self.events = []
        self.tasks = set()
        self.schedule_intervals = {}  # {task_name: [(start, end), ...]}
        self.task_arrivals = {}       # {task_name: [arrival_times]}
        self.task_completions = {}    # {task_name: [completion_times]}
        self.deadline_misses = defaultdict(dict)  # {task_name: {arrival_time: miss_time}}
        self.time_range = (float('inf'), 0)
        self.verbose = verbose

        self._parse_trace()

    def _parse_trace(self):
        if self.verbose:
            print(f"正在解析追踪文件: {self.trace_file}")

        try:
            with open(self.trace_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.events = data.get('events', [])
        except FileNotFoundError:
            print(f"错误：找不到文件 {self.trace_file}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"错误：JSON解析失败 - {e}")
            sys.exit(1)

        if not self.events:
            print("错误：追踪文件中没有事件")
            sys.exit(1)

        self._extract_schedule_info()

        if self.verbose:
            print(f"✓ 解析完成：{len(self.events)} 个事件, {len(self.tasks)} 个任务")
            print(f"✓ 时间范围: {self.time_range[0]} - {self.time_range[1]}")

    def _extract_schedule_info(self):
        """
        从事件中提取调度信息 - 核心修复版
        1. 使用唯一实例标识: task_name + arrival_time
        2. 正确处理 kill 事件
        """
        # 初始化数据结构
        self.schedule_intervals = defaultdict(list)
        self.task_arrivals = defaultdict(list)
        self.task_completions = defaultdict(list)

        # ⭐ 核心修复1: 使用唯一实例标识 (task_name, arrival_time)
        # active_tasks 存储: {(task_name, arrival_time): start_time}
        active_tasks = {}  # {(task_name, arrival_time): start_time}

        # 记录每个任务的最新到达时间（用于处理没有明确arrival_time的场景）
        latest_arrival = {}  # {task_name: arrival_time}

        event_stats = defaultdict(int)

        # 按时间顺序处理事件
        for event in self.events:
            event_type = event['event_type']
            task_name = event.get('task_name', 'unknown')
            time = int(event['time'])

            # 记录任务
            self.tasks.add(task_name)

            # 更新时间范围
            self.time_range = (min(self.time_range[0], time), max(self.time_range[1], time))

            # 统计事件类型
            event_stats[event_type] += 1

            # 获取或更新到达时间
            arrival_time = int(event.get('arrival_time', time))
            if task_name in latest_arrival:
                # 如果当前时间小于最新到达时间，说明是旧实例的事件
                if time < latest_arrival[task_name]:
                    arrival_time = latest_arrival[task_name]
            latest_arrival[task_name] = max(latest_arrival.get(task_name, 0), arrival_time)

            # 实例唯一标识
            instance_key = (task_name, arrival_time)

            # 处理不同类型的事件
            if event_type == 'arrival':
                self.task_arrivals[task_name].append(time)
                latest_arrival[task_name] = time

            elif event_type == 'scheduled':
                # 任务开始执行 - 使用唯一实例标识
                if instance_key not in active_tasks:
                    active_tasks[instance_key] = time

            elif event_type == 'descheduled':
                # 任务被抢占 - 使用唯一实例标识
                if instance_key in active_tasks:
                    start_time = active_tasks[instance_key]
                    self.schedule_intervals[task_name].append((start_time, time))
                    del active_tasks[instance_key]

            elif event_type == 'end_instance':
                # 任务实例正常结束 - 使用唯一实例标识
                self.task_completions[task_name].append(time)
                if instance_key in active_tasks:
                    start_time = active_tasks[instance_key]
                    self.schedule_intervals[task_name].append((start_time, time))
                    del active_tasks[instance_key]

            # ⭐ 核心修复2: 处理 kill 事件（强制终止）
            elif event_type == 'kill':
                if instance_key in active_tasks:
                    start_time = active_tasks[instance_key]
                    self.schedule_intervals[task_name].append((start_time, time))
                    del active_tasks[instance_key]

            # ⭐ 核心修复3: 记录 deadline miss（使用唯一实例标识）
            elif event_type == 'dline_miss':
                miss_arrival = int(event.get('arrival_time', time))
                self.deadline_misses[task_name][miss_arrival] = time

        # 处理可能未关闭的任务（以防追踪文件不完整）
        for (task_name, arrival_time), start_time in active_tasks.items():
            self.schedule_intervals[task_name].append((start_time, self.time_range[1]))

        # 打印统计信息
        if self.verbose:
            print("\n事件类型统计:")
            for et, count in sorted(event_stats.items()):
                print(f"  {et}: {count}")

            print(f"\n任务列表:")
            for task in sorted(self.tasks):
                intervals = len(self.schedule_intervals.get(task, []))
                total_time = sum(end - start for start, end in self.schedule_intervals.get(task, []))
                misses = len(self.deadline_misses.get(task, []))
                print(f"  {task}: {intervals} 个执行区间, 总执行时间 {total_time}, Deadline Miss: {misses}")

    def get_schedule_intervals(self) -> Dict[str, List[Tuple[int, int]]]:
        return dict(self.schedule_intervals)

    def get_task_arrivals(self) -> Dict[str, List[int]]:
        return dict(self.task_arrivals)

    def get_tasks(self) -> List[str]:
        return sorted(self.tasks)

    def get_time_range(self) -> Tuple[int, int]:
        return self.time_range

    def get_deadline_misses(self) -> Dict[str, Dict[int, int]]:
        return dict(self.deadline_misses)

# ===============================
# 任务集配置解析器
# ===============================
class TaskSetParser:
    def __init__(self, taskset_file: str):
        self.taskset_file = taskset_file
        self.task_configs = {}

        self._parse_taskset()

    def _parse_taskset(self):
        try:
            with open(self.taskset_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                tasks = data.get('taskset', [])

                for task in tasks:
                    name = task['name']
                    params_str = task.get('params', '')
                    params = {}

                    for param in params_str.split(','):
                        if '=' in param:
                            key, value = param.strip().split('=')
                            try:
                                params[key] = int(value)
                            except ValueError:
                                params[key] = value

                    T = params.get('period', task.get('iat'))
                    D = task.get('deadline', T)
                    O = params.get('arrival_offset', 0)

                    self.task_configs[name] = {'T': T, 'D': D, 'O': O}

            print(f"✓ 解析任务集配置完成：{len(self.task_configs)} 个任务")
            for task_name, config in self.task_configs.items():
                print(f"  {task_name}: T={config['T']}, D={config['D']}, O={config['O']}")
        except Exception as e:
            print(f"警告：解析任务集配置失败 - {e}")

    def get_task_config(self, task_name: str) -> Dict[str, int]:
        return self.task_configs.get(task_name, {})

    def get_all_configs(self) -> Dict[str, Dict[str, int]]:
        return self.task_configs


# ===============================
# 可视化绘图器（增强版）
# ===============================
class TraceVisualizer:
    _font_prop = None

    @classmethod
    def _get_font_prop(cls):
        if cls._font_prop is None:
            import matplotlib.font_manager as fm
            font_path = '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'
            cls._font_prop = fm.FontProperties(fname=font_path)
        return cls._font_prop

    def __init__(self, parser: TraceParser, config: Dict[str, Any], taskset_parser: TaskSetParser = None):
        self.parser = parser
        self.config = config
        self.taskset_parser = taskset_parser
        self.tasks = parser.get_tasks()
        self.schedule_intervals = parser.get_schedule_intervals()
        self.task_arrivals = parser.get_task_arrivals()
        self.time_range = parser.get_time_range()
        self.deadline_misses = parser.get_deadline_misses()

    def plot_gantt_chart(self, output_file: str = None):
        """绘制调度甘特图 - 带防重叠的起止时间标注"""
        fig, ax = plt.subplots(figsize=self.config['figure_size'], dpi=self.config['dpi'])

        task_positions = {task: i for i, task in enumerate(self.tasks)}
        time_offset = self.time_range[0]
        time_span = self.time_range[1] - self.time_range[0]

        # ⭐ 计算刻度间隔
        if time_span <= 20:
            tick_interval = 2
        elif time_span <= 50:
            tick_interval = 5
        elif time_span <= 100:
            tick_interval = 10
        else:
            tick_interval = 20

        # ⭐ 绘制时间轴和刻度
        for task_name in self.tasks:
            pos = task_positions[task_name]
            time_axis_y = pos - 0.125
            ax.plot([0, time_span * 1.02], [time_axis_y, time_axis_y],
                   color='gray', linestyle='-', linewidth=0.8, alpha=0.5)

            for tick in range(0, int(time_span * 1.02) + 1, tick_interval):
                ax.plot([tick, tick], [time_axis_y, time_axis_y - 0.05],
                       color='gray', linestyle='-', linewidth=0.5, alpha=0.5)
                ax.text(tick, time_axis_y - 0.08, str(tick),
                       ha='center', va='top', fontsize=5, color='gray',
                       fontproperties=self._get_font_prop())

        # ⭐ 用于跟踪已标注的时间点（去重）
        labeled_times = defaultdict(set)  # {task_name: {time1, time2, ...}}

        # 绘制任务执行区间
        for task_name, intervals in self.schedule_intervals.items():
            pos = task_positions[task_name]
            color = get_color_for_task(task_name)

            for start, end in intervals:
                duration = end - start
                adjusted_start = start - time_offset
                adjusted_end = end - time_offset

                # 绘制任务条
                ax.barh(pos, duration, left=adjusted_start, height=0.25,
                       color=color, edgecolor='black', linewidth=1.0, alpha=0.85)

                # ⭐ 防重叠的起止时间标注
                self._add_time_labels(ax, task_name, pos, adjusted_start, adjusted_end, duration, time_span, labeled_times)

        # 设置坐标轴
        ax.set_yticks(range(len(self.tasks)))
        ax.set_yticklabels(self.tasks, fontsize=10, fontproperties=self._get_font_prop())
        ax.set_xlabel(f'时间 (刻度间隔: {tick_interval}ms)', fontsize=12, fontweight='bold',
                     fontproperties=self._get_font_prop())
        ax.set_ylabel('任务', fontsize=12, fontweight='bold', fontproperties=self._get_font_prop())

        ax.set_ylim(-0.7, len(self.tasks) + 0.1)

        # ⭐ 绘制 deadline miss 标记（使用唯一实例标识）
        self._plot_deadline_misses(ax, task_positions, time_offset, time_span)

        # 绘制到达时间和截止时间标记
        if self.taskset_parser:
            self._plot_arrival_deadlines(ax, task_positions, time_offset, time_span)

        ax.set_xlim(0, time_span * 1.02)
        ax.tick_params(axis='x', bottom=False, labelbottom=False)

        if not self.config.get('no_grid', False):
            ax.grid(True, axis='x', linestyle='--', alpha=0.3)
            ax.set_axisbelow(True)

        # 设置标题
        if self.config.get('title'):
            title = self.config['title']
        else:
            title = f'调度甘特图 - {os.path.basename(self.parser.trace_file)}'
        ax.set_title(title, fontsize=14, fontweight='bold', pad=20,
                    fontproperties=self._get_font_prop())

        # 创建图例
        legend_elements = []
        total_execution = defaultdict(int)
        for task_name, intervals in self.schedule_intervals.items():
            for start, end in intervals:
                total_execution[task_name] += (end - start)

        has_any_miss = any(len(misses) > 0 for misses in self.deadline_misses.values())
        if has_any_miss:
            from matplotlib.lines import Line2D
            legend_elements.append(Line2D([0], [0], marker='x', markersize=10, linestyle='None',
                           color='red', markeredgewidth=2, label='Deadline Miss'))

        for task in self.tasks:
            color = get_color_for_task(task)
            intervals = len(self.schedule_intervals.get(task, []))
            exec_time = total_execution.get(task, 0)
            legend_elements.append(mpatches.Patch(color=color,
                         label=f'{task} ({intervals}x, {exec_time}t)'))

        ax.legend(handles=legend_elements, loc='upper left', fontsize=9, framealpha=0.9, ncol=2)

        plt.tight_layout()

        # 保存图表
        if output_file is None:
            base_name = os.path.splitext(os.path.basename(self.parser.trace_file))[0]
            output_format = self.config['format']
            output_file = f'scheduling_gantt_{base_name}.{output_format}'
        else:
            if not output_file.endswith(f".{self.config['format']}"):
                base = os.path.splitext(output_file)[0]
                output_file = f"{base}.{self.config['format']}"

        plt.savefig(output_file, dpi=self.config['dpi'], bbox_inches='tight')
        print(f"\n✓ 图表已保存至: {output_file}")
        print(f"  图表大小: {self.config['figure_size'][0]}x{self.config['figure_size'][1]} 英寸, DPI: {self.config['dpi']}")

        plt.close(fig)

    def _add_time_labels(self, ax, task_name: str, pos: int, adjusted_start: float, adjusted_end: float,
                         duration: float, time_span: float, labeled_times: dict):
        """
        ⭐ 防重叠的起止时间标注策略
        1. 时间标注在任务条上方
        2. 颜色为灰色，和底部刻度一致
        3. 去重：同一任务同一时间只标注一次
        """
        # 去重检查 - 开始时间
        start_key = int(adjusted_start)
        if start_key not in labeled_times[task_name]:
            labeled_times[task_name].add(start_key)

            # 开始时间标注在任务条上方
            ax.text(adjusted_start, pos + 0.35, f'{start_key}',
                   ha='center', va='bottom', fontsize=7, color='gray',
                   fontproperties=self._get_font_prop())

        # 去重检查 - 结束时间（避免和下一个区块的开始重叠）
        end_key = int(adjusted_end)
        # 只有当结束时间和开始时间不同时才标注
        if end_key != start_key and end_key not in labeled_times[task_name]:
            labeled_times[task_name].add(end_key)

            # 结束时间标注在任务条上方
            ax.text(adjusted_end, pos + 0.35, f'{end_key}',
                   ha='center', va='bottom', fontsize=7, color='gray',
                   fontproperties=self._get_font_prop())

    def _plot_deadline_misses(self, ax, task_positions: dict, time_offset: float, time_span: float):
        """绘制 deadline miss 标记"""
        for task_name in self.tasks:
            pos = task_positions[task_name]
            task_misses = self.deadline_misses.get(task_name, {})
            task_intervals = self.schedule_intervals.get(task_name, [])

            for arrival_time, miss_time in task_misses.items():
                adjusted_miss_time = miss_time - time_offset

                if adjusted_miss_time <= time_span * 1.02:
                    # 检查该任务实例是否有执行区间
                    has_execution = False
                    interval_end_time = None

                    for start, end in task_intervals:
                        if start >= arrival_time and end > arrival_time:
                            has_execution = True
                            interval_end_time = end - time_offset
                            break

                    if has_execution and interval_end_time is not None:
                        ax.plot(interval_end_time, pos, marker='x', markersize=10,
                               color='red', markeredgewidth=2, label='_nolegend_', zorder=10)
                    else:
                        adjusted_arrival = arrival_time - time_offset
                        ax.plot(adjusted_arrival + 0.5, pos, marker='x', markersize=10,
                               color='red', markeredgewidth=2, label='_nolegend_', zorder=10)

    def _plot_arrival_deadlines(self, ax, task_positions: dict, time_offset: float, time_span: float):
        """绘制到达时间和截止时间标记"""
        for task_name in self.tasks:
            pos = task_positions[task_name]
            task_config = self.taskset_parser.get_task_config(task_name)

            if not task_config:
                continue

            T = task_config['T']
            D = task_config['D']
            O = task_config['O']

            k = 0
            while True:
                abs_release = O + (k * T)
                if abs_release > self.time_range[1]:
                    break

                abs_deadline = abs_release + D

                adjusted_release = abs_release - time_offset
                adjusted_deadline = abs_deadline - time_offset

                if adjusted_release <= time_span * 1.02:
                    ax.plot([adjusted_release, adjusted_release], [pos - 0.125, pos + 0.3],
                           color='green', linestyle='-', linewidth=1.5, alpha=0.7)
                    ax.plot(adjusted_release, pos + 0.3, marker='^', markersize=8,
                           color='green', markeredgecolor='darkgreen', markeredgewidth=1,
                           label='到达' if task_name == self.tasks[0] and k == 0 else '')

                    if adjusted_deadline <= time_span * 1.02:
                        ax.plot([adjusted_deadline, adjusted_deadline], [pos + 0.3, pos - 0.125],
                               color='red', linestyle='-', linewidth=1.5, alpha=0.7)
                        ax.plot(adjusted_deadline, pos - 0.125, marker='v', markersize=8,
                               color='red', markeredgecolor='darkred', markeredgewidth=1,
                               label='截止' if task_name == self.tasks[0] and k == 0 else '')

                k += 1

    def print_statistics(self):
        """打印调度统计信息"""
        if not self.config.get('verbose', True):
            return

        print("\n" + "="*70)
        print("调度统计信息 / Scheduling Statistics")
        print("="*70)

        total_time = self.time_range[1] - self.time_range[0]

        print(f"\n【全局信息】")
        print(f"时间范围: {self.time_range[0]} - {self.time_range[1]} (总时长: {total_time})")
        print(f"任务数量: {len(self.tasks)}")
        print(f"总事件数: {len(self.parser.events)}")

        print(f"\n【任务详细统计】")
        print(f"{'任务':<15} {'执行次数':<10} {'总执行时间':<12} {'CPU占用率':<12} {'首次到达':<10}")
        print("-" * 70)

        for task in sorted(self.tasks):
            intervals = self.schedule_intervals.get(task, [])
            arrivals = self.task_arrivals.get(task, [])

            exec_count = len(intervals)
            exec_time = sum(end - start for start, end in intervals)
            cpu_usage = (exec_time / total_time * 100) if total_time > 0 else 0
            first_arrival = min(arrivals) if arrivals else "N/A"

            print(f"{task:<15} {exec_count:<10} {exec_time:<12} {cpu_usage:<12.2f}% {first_arrival:<10}")

        total_exec_time = sum(sum(end - start for start, end in intervals)
                             for intervals in self.schedule_intervals.values())
        overall_cpu_usage = (total_exec_time / total_time * 100) if total_time > 0 else 0

        print("-" * 70)
        print(f"{'总计':<15} {'':<10} {total_exec_time:<12} {overall_cpu_usage:<12.2f}% {'':<10}")

        print("="*70 + "\n")

# ===============================
# 主程序
# ===============================
def main():
    args = parse_arguments()

    config = DEFAULT_CONFIG.copy()
    config['format'] = args.format
    config['dpi'] = args.dpi
    config['figure_size'] = (args.width, args.height)
    config['output'] = args.output
    config['title'] = args.title
    config['verbose'] = not args.no_stats
    config['no_grid'] = args.no_grid

    if config['verbose']:
        print("\n" + "="*70)
        print("实时调度追踪文件可视化工具")
        print("="*70 + "\n")

    # 解析追踪文件
    parser = TraceParser(args.trace_file, verbose=config['verbose'])

    # 解析任务集配置
    taskset_parser = None
    if args.taskset:
        taskset_parser = TaskSetParser(args.taskset)

    # 创建可视化器
    visualizer = TraceVisualizer(parser, config, taskset_parser)

    # 打印统计信息
    visualizer.print_statistics()

    # 生成甘特图
    if config['verbose']:
        print("\n正在生成甘特图...")
    visualizer.plot_gantt_chart(output_file=config['output'])

    if config['verbose']:
        print("\n✓ 可视化完成！")

if __name__ == "__main__":
    main()
