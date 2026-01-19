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
    python3 trace_visualizer.py trace.json --format pdf --dpi 300
    python3 trace_visualizer.py trace.json --width 30 --height 10
"""

import matplotlib
matplotlib.use('Agg')  # 使用非交互式后端，避免显示问题
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import json
import yaml
import sys
import argparse
import os
from typing import List, Dict, Tuple, Any
from collections import defaultdict

# 配置中文字体（解决中文显示问题）
# 直接指定字体文件路径
import matplotlib.font_manager as fm
font_path = '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'
font_prop = fm.FontProperties(fname=font_path)
plt.rcParams['font.family'] = font_prop.get_name()
plt.rcParams['axes.unicode_minus'] = False
print(f"✓ 使用中文字体: {font_prop.get_name()}")

# ===============================
# 第一部分：参数配置区
# ===============================

# 默认配置（可通过命令行参数覆盖）
DEFAULT_CONFIG = {
    'figure_size': (20, 8),  # 图表大小（宽，高）英寸
    'dpi': 150,              # 图表分辨率
    'format': 'png',         # 保存格式：'png', 'pdf', 'svg'
    'output': None,          # 输出文件名（None=自动生成）
    'title': None,           # 图表标题（None=自动生成）
    'verbose': True,         # 是否显示详细信息
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

# 为未定义的任务自动生成颜色
def get_color_for_task(task_name: str) -> str:
    """为任务获取或生成颜色"""
    if task_name in COLOR_SCHEME:
        return COLOR_SCHEME[task_name]

    # 自动生成颜色（基于哈希）
    hash_val = hash(task_name) % 16777215
    color = f'#{hash_val:06x}'
    COLOR_SCHEME[task_name] = color
    return color

# ===============================
# 第二部分：命令行参数解析
# ===============================

def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='实时调度追踪文件可视化工具 / Real-Time Scheduling Trace File Visualizer',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s trace.json
  %(prog)s trace.json --output my_chart.png
  %(prog)s trace.json --format pdf --dpi 300
  %(prog)s trace.json --width 30 --height 10
  %(prog)s trace.json --no-stats
        """
    )

    # 必需参数
    parser.add_argument('trace_file', type=str,
                       help='追踪文件路径 (JSON格式)')

    # 可选参数
    parser.add_argument('-o', '--output', type=str, default=None,
                       help='输出文件名 (默认: 自动生成)')

    parser.add_argument('-f', '--format', type=str, default='png',
                       choices=['png', 'pdf', 'svg'],
                       help='输出格式 (默认: png)')

    parser.add_argument('--dpi', type=int, default=150,
                       help='图表分辨率 (默认: 150)')

    parser.add_argument('--width', type=float, default=20,
                       help='图表宽度（英寸）(默认: 20)')

    parser.add_argument('--height', type=float, default=8,
                       help='图表高度（英寸）(默认: 8)')

    parser.add_argument('-t', '--title', type=str, default=None,
                       help='图表标题 (默认: 自动生成)')

    parser.add_argument('--no-stats', action='store_true',
                       help='不显示统计信息')

    parser.add_argument('--no-grid', action='store_true',
                       help='不显示网格线')

    parser.add_argument('--taskset', type=str, default=None,
                       help='任务集配置文件 (YAML格式) - 用于显示到达和截止时��标记')

    return parser.parse_args()

# ===============================
# 第三部分：追踪文件解析器
# ===============================

class TraceParser:
    """追踪文件解析器：解析JSON格式的调度追踪"""

    def __init__(self, trace_file: str, verbose: bool = True):
        """
        初始化解析器

        参数：
            trace_file: 追踪文件路径
            verbose: 是否显示详细信息
        """
        self.trace_file = trace_file
        self.events = []
        self.tasks = set()
        self.schedule_intervals = {}  # {task_name: [(start, end), ...]}
        self.task_arrivals = {}  # {task_name: [arrival_times]}
        self.task_completions = {}  # {task_name: [completion_times]}
        self.time_range = (0, 0)
        self.verbose = verbose

        self._parse_trace()

    def _parse_trace(self):
        """解析追踪文件"""
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

        # 提取任务信息和调度间隔
        self._extract_schedule_info()

        if self.verbose:
            print(f"✓ 解析完成：{len(self.events)} 个事件, {len(self.tasks)} 个任务")
            print(f"✓ 时间范围: {self.time_range[0]} - {self.time_range[1]}")

    def _extract_schedule_info(self):
        """从事件中提取调度信息"""
        # 初始化数据结构
        self.schedule_intervals = defaultdict(list)
        self.task_arrivals = defaultdict(list)
        self.task_completions = defaultdict(list)

        # 跟踪当前正在执行的任务
        active_tasks = {}  # {task_name: start_time}

        # 事件类型统计
        event_stats = defaultdict(int)

        # 直接使用原始时间值，不进行偏移
        for event in self.events:
            event_type = event['event_type']
            task_name = event.get('task_name', 'unknown')
            time = int(event['time'])

            # 记录任务
            self.tasks.add(task_name)

            # 记录时间范围（使用原始时间）
            self.time_range = (min(self.time_range[0], time) if self.time_range != (0, 0) else time,
                             max(self.time_range[1], time))

            # 统计事件类型
            event_stats[event_type] += 1

            # 处理不同类型的事件
            if event_type == 'arrival':
                # 任务到达
                self.task_arrivals[task_name].append(time)

            elif event_type == 'scheduled':
                # 任务开始执行
                if task_name not in active_tasks:
                    active_tasks[task_name] = time

            elif event_type == 'descheduled':
                # 任务停止执行
                if task_name in active_tasks:
                    start_time = active_tasks[task_name]
                    self.schedule_intervals[task_name].append((start_time, time))
                    del active_tasks[task_name]

            elif event_type == 'end_instance':
                # 任务实例结束（也作为descheduled处理）
                self.task_completions[task_name].append(time)
                # 如果任务还在活跃，记录完成时间作为区间结束
                if task_name in active_tasks:
                    start_time = active_tasks[task_name]
                    self.schedule_intervals[task_name].append((start_time, time))
                    del active_tasks[task_name]

        # 处理可能未关闭的任务（以防追踪文件不完整）
        for task_name, start_time in active_tasks.items():
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
                print(f"  {task}: {intervals} 个执行区间, 总执行时间 {total_time}")

    def get_schedule_intervals(self) -> Dict[str, List[Tuple[int, int]]]:
        """获取所有任务的调度间隔"""
        return dict(self.schedule_intervals)

    def get_task_arrivals(self) -> Dict[str, List[int]]:
        """获取所有任务的到达时间"""
        return dict(self.task_arrivals)

    def get_tasks(self) -> List[str]:
        """获取任务列表"""
        return sorted(self.tasks)

    def get_time_range(self) -> Tuple[int, int]:
        """获取时间范围"""
        return self.time_range

# ===============================
# 任务集配置解析器
# ===============================

class TaskSetParser:
    """任务集配置解析器：解析YAML格式的任务集配置"""

    def __init__(self, taskset_file: str):
        """
        初始化解析器

        参数：
            taskset_file: 任务集配置文件路径（YAML格式）
        """
        self.taskset_file = taskset_file
        self.task_configs = {}  # {task_name: {'period': int, 'deadline': int, 'arrival_offset': int}}

        self._parse_taskset()

    def _parse_taskset(self):
        """解析任务集配置文件"""
        try:
            with open(self.taskset_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                tasks = data.get('taskset', [])

                for task in tasks:
                    name = task['name']
                    # 从params中提取参数，格式如 "period=50,wcet=20,arrival_offset=0"
                    params_str = task.get('params', '')
                    params = {}

                    for param in params_str.split(','):
                        if '=' in param:
                            key, value = param.strip().split('=')
                            try:
                                params[key] = int(value)
                            except ValueError:
                                # 保持字符串值（如 workload=bzip2）
                                params[key] = value

                    self.task_configs[name] = {
                        'period': params.get('period', task.get('iat', 0)),
                        'deadline': task.get('deadline', params.get('period', 0)),
                        'arrival_offset': params.get('arrival_offset', 0)
                    }

            print(f"✓ 解析任务集配置完成：{len(self.task_configs)} 个任务")
            for task_name, config in self.task_configs.items():
                print(f"  {task_name}: 周期={config['period']}, deadline={config['deadline']}, 到达偏移={config['arrival_offset']}")

        except FileNotFoundError:
            print(f"警告：找不到任务集配置文件 {self.taskset_file}")
        except Exception as e:
            print(f"警告：解析任务集配置失败 - {e}")

    def get_task_config(self, task_name: str) -> Dict[str, int]:
        """获取指定任务的配置"""
        return self.task_configs.get(task_name, {})

    def get_all_configs(self) -> Dict[str, Dict[str, int]]:
        """获取所有任务配置"""
        return self.task_configs

# ===============================
# 第四部分：可视化绘图器
# ===============================

class TraceVisualizer:
    """追踪可视化器：根据追踪文件生成甘特图"""

    # 类级别的字体属性
    _font_prop = None

    @classmethod
    def _get_font_prop(cls):
        """获取字体属性"""
        if cls._font_prop is None:
            import matplotlib.font_manager as fm
            font_path = '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'
            cls._font_prop = fm.FontProperties(fname=font_path)
        return cls._font_prop

    def __init__(self, parser: TraceParser, config: Dict[str, Any], taskset_parser: TaskSetParser = None):
        """
        初始化可视化器

        参数：
            parser: 已解析的追踪文件解析器
            config: 配置字典
            taskset_parser: 任务集配置解析器（可选）
        """
        self.parser = parser
        self.config = config
        self.taskset_parser = taskset_parser
        self.tasks = parser.get_tasks()
        self.schedule_intervals = parser.get_schedule_intervals()
        self.task_arrivals = parser.get_task_arrivals()
        self.time_range = parser.get_time_range()

    def plot_gantt_chart(self, output_file: str = None):
        """
        绘制调度甘特图

        参数：
            output_file: 输出文件路径（可选）
        """
        # 创建图表
        fig, ax = plt.subplots(figsize=self.config['figure_size'], dpi=self.config['dpi'])

        # 任务位置映射
        task_positions = {task: i for i, task in enumerate(self.tasks)}

        # 时间偏移量（将起始时间归零）
        time_offset = self.time_range[0]

        # 绘制每个任务的执行区间
        total_execution = defaultdict(int)

        for task_name, intervals in self.schedule_intervals.items():
            pos = task_positions[task_name]
            color = get_color_for_task(task_name)

            for start, end in intervals:
                duration = end - start
                total_execution[task_name] += duration

                # 使用偏移后的时间
                adjusted_start = start - time_offset

                # 绘制任务块
                ax.barh(pos, duration, left=adjusted_start, height=0.25,
                       color=color, edgecolor='black', linewidth=1.0, alpha=0.85)

                # 添加任务标签（如果区间足够长）
                if duration >= (self.time_range[1] - self.time_range[0]) * 0.02:
                    mid_time = adjusted_start + duration / 2
                    ax.text(mid_time, pos, task_name, ha='center', va='center',
                           fontsize=6, fontweight='bold', color='white')

        # 设置坐标轴
        ax.set_yticks(range(len(self.tasks)))
        ax.set_yticklabels(self.tasks, fontsize=10, fontproperties=self._get_font_prop())
        ax.set_xlabel('时间', fontsize=12, fontweight='bold', fontproperties=self._get_font_prop())
        ax.set_ylabel('任务', fontsize=12, fontweight='bold', fontproperties=self._get_font_prop())

        # 设置Y轴范围（留出空间给箭头）
        ax.set_ylim(-0.5, len(self.tasks) + 0.1)

        # 计算时间跨度
        time_span = self.time_range[1] - self.time_range[0]

        # 绘制到达时间和截止时间标记（如果有任务集配置）
        if self.taskset_parser:
            for task_name in self.tasks:
                pos = task_positions[task_name]
                task_config = self.taskset_parser.get_task_config(task_name)

                if not task_config:
                    continue

                period = task_config['period']
                deadline = task_config['deadline']
                arrival_offset = task_config['arrival_offset']

                # 获取该任务的到达时间列表
                arrivals = self.task_arrivals.get(task_name, [])

                # 为每个到达时间绘制向上箭头（到达）和向下箭头（截止）
                for arrival_time in arrivals:
                    adjusted_arrival = arrival_time - time_offset

                    # 绘制到达时间标记（向上箭头，绿色）+ 垂直线
                    # 垂直线从任��条底边延伸到上箭头（向上突出）
                    ax.plot([adjusted_arrival, adjusted_arrival], [pos - 0.125, pos + 0.3],
                           color='green', linestyle='-', linewidth=1.5, alpha=0.7)
                    ax.plot(adjusted_arrival, pos + 0.3, marker='^', markersize=8,
                           color='green', markeredgecolor='darkgreen', markeredgewidth=1,
                           label='到达' if task_name == self.tasks[0] else '')

                    # 计算截止时间
                    deadline_time = arrival_time + deadline
                    adjusted_deadline = deadline_time - time_offset

                    # 只绘制在时间范围内的截止时间标记
                    if adjusted_deadline <= time_span * 1.01:
                        # 绘制截止时间标记（向下箭头，红色）+ 垂直线
                        # 垂直线从任务条底边延伸到下箭头（向下突出）
                        ax.plot([adjusted_deadline, adjusted_deadline], [pos - 0.125, pos - 0.3],
                               color='red', linestyle='-', linewidth=1.5, alpha=0.7)
                        ax.plot(adjusted_deadline, pos - 0.3, marker='v', markersize=8,
                               color='red', markeredgecolor='darkred', markeredgewidth=1,
                               label='截止' if task_name == self.tasks[0] else '')

        # 设置X轴范围（从0开始，留一些边距）
        ax.set_xlim(-time_span * 0.01, time_span * 1.01)

        # 添加网格
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
        for task in self.tasks:
            color = get_color_for_task(task)
            intervals = len(self.schedule_intervals.get(task, []))
            exec_time = total_execution.get(task, 0)
            legend_elements.append(
                mpatches.Patch(color=color,
                             label=f'{task} ({intervals}x, {exec_time}t)')
            )

        ax.legend(handles=legend_elements, loc='upper left',
                 fontsize=9, framealpha=0.9, ncol=2)

        # 调整布局
        plt.tight_layout()

        # 保存图表
        if output_file is None:
            # 自动生成文件名
            base_name = os.path.splitext(os.path.basename(self.parser.trace_file))[0]
            output_format = self.config['format']
            output_file = f'scheduling_gantt_{base_name}.{output_format}'
        else:
            # 确保使用指定的格式扩展名
            if not output_file.endswith(f".{self.config['format']}"):
                # 移除旧扩展名并添加新扩展名
                base = os.path.splitext(output_file)[0]
                output_file = f"{base}.{self.config['format']}"

        plt.savefig(output_file, dpi=self.config['dpi'], bbox_inches='tight')
        print(f"\n✓ 图表已保存至: {output_file}")
        print(f"  图表大小: {self.config['figure_size'][0]}x{self.config['figure_size'][1]} 英寸, DPI: {self.config['dpi']}")

        plt.close(fig)  # 关闭图表，释放内存

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
# 第五部分：主程序
# ===============================

def main():
    """主函数"""
    # 解析命令行参数
    args = parse_arguments()

    # 构建配置字典
    config = DEFAULT_CONFIG.copy()
    config['format'] = args.format
    config['dpi'] = args.dpi
    config['figure_size'] = (args.width, args.height)
    config['output'] = args.output
    config['title'] = args.title
    config['verbose'] = not args.no_stats
    config['no_grid'] = args.no_grid

    # 打印标题
    if config['verbose']:
        print("\n" + "="*70)
        print("实时调度追踪文件可视化工具")
        print("Real-Time Scheduling Trace File Visualizer")
        print("="*70 + "\n")

    # 1. 解析追踪文件
    parser = TraceParser(args.trace_file, verbose=config['verbose'])

    # 2. 解析任务集配置（如果提供）
    taskset_parser = None
    if args.taskset:
        taskset_parser = TaskSetParser(args.taskset)

    # 3. 创建可视化器
    visualizer = TraceVisualizer(parser, config, taskset_parser)

    # 3. 打印统计信息
    visualizer.print_statistics()

    # 4. 生成甘特图
    if config['verbose']:
        print("\n正在生成甘特图...")
    visualizer.plot_gantt_chart(output_file=config['output'])

    if config['verbose']:
        print("\n✓ 可视化完成！")

if __name__ == "__main__":
    main()
