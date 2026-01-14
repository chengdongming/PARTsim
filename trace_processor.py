#!/usr/bin/env python3
"""
PARTSim 追踪文件处理器 - 完整版
整合了 trace_processor.py、json_time_offset.py 和 process_trace.py 的功能
处理C++调度器输出的追踪文件，添加时间偏移和元数据
"""

import json
import sys
import os
import argparse
import re
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# 使用统一日志系统
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.unified_logger import get_trace_logger

# 获取追踪处理器的日志记录器
logger = get_trace_logger()

class TraceProcessor:
    """追踪文件处理器 - 整合所有功能"""
    
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        if not verbose:
            # 使用 logging.WARNING 常量值
            import logging as std_logging
            logger.setLevel(std_logging.WARNING)
    
    def format_time(self, total_ms: int) -> str:
        """格式化时间（毫秒转为HH:MM:SS）"""
        hours = (total_ms // 3600000) % 24
        minutes = (total_ms % 3600000) // 60000
        seconds = (total_ms % 60000) // 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    def parse_trace_file(self, input_file: str) -> List[Dict[str, Any]]:
        """解析追踪文件，支持多种格式"""
        events = []
        
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            
            file_size = len(content)
            logger.info(f"文件大小: {file_size} 字节")
            
            # 方法1: 完整JSON对象
            if content.startswith('{') and content.endswith('}'):
                try:
                    data = json.loads(content)
                    if 'events' in data and isinstance(data['events'], list):
                        events = data['events']
                        logger.info(f"检测到完整JSON格式: {len(events)} 个事件")
                        return events
                except json.JSONDecodeError as e:
                    logger.warning(f"完整JSON解析失败: {e}")
            
            # 方法2: JSON数组格式
            if content.startswith('[') and content.endswith(']'):
                try:
                    events = json.loads(content)
                    logger.info(f"检测到JSON数组格式: {len(events)} 个事件")
                    return events
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON数组解析失败: {e}")
            
            # 方法3: 逐行JSON格式（常见PARTSim格式）
            lines = content.split('\n')
            parsed_lines = 0
            error_lines = 0
            
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                
                # 跳过注释
                if line.startswith('//') or line.startswith('#'):
                    continue
                
                # 清理可能的尾部逗号
                line_clean = line.rstrip(',')
                
                if line_clean.startswith('{') and line_clean.endswith('}'):
                    try:
                        event = json.loads(line_clean)
                        
                        # 修复时间字段类型
                        if 'time' in event:
                            event['time'] = self._fix_time_value(event['time'])
                        
                        # 修复arrival_time
                        if 'arrival_time' in event:
                            event['arrival_time'] = self._fix_time_value(event['arrival_time'])
                        
                        events.append(event)
                        parsed_lines += 1
                        
                    except json.JSONDecodeError as e:
                        error_lines += 1
                        if error_lines <= 5:  # 只显示前5个错误
                            logger.warning(f"第{i+1}行解析失败: {line[:50]}...")
                        continue
            
            logger.info(f"逐行解析: {parsed_lines} 个事件，{error_lines} 个错误")
            
        except Exception as e:
            logger.error(f"解析文件失败: {e}")
        
        return events
    
    def _fix_time_value(self, time_value: Any) -> int:
        """修复时间值类型"""
        if isinstance(time_value, (int, float)):
            return int(time_value)
        elif isinstance(time_value, str):
            try:
                # 尝试转换为整数
                if '.' in time_value:
                    return int(float(time_value))
                else:
                    return int(time_value)
            except:
                return 0
        else:
            return 0
    
    def apply_time_offset(self, events: List[Dict[str, Any]], offset_ms: int) -> List[Dict[str, Any]]:
        """应用时间偏移到事件"""
        if offset_ms == 0:
            return events
        
        for event in events:
            # 偏移原始时间
            if 'time' in event:
                event['original_time'] = event['time']
                event['time'] += offset_ms
            
            # 偏移到达时间
            if 'arrival_time' in event:
                event['original_arrival_time'] = event['arrival_time']
                event['arrival_time'] += offset_ms
        
        logger.info(f"已应用时间偏移: {offset_ms} ms")
        return events
    
    def create_metadata(self, start_time_offset: int, events_count: int, 
                       input_file: str, scheduler_type: str = "gpfp_asap") -> Dict[str, Any]:
        """创建元数据"""
        time_str = self.format_time(start_time_offset)
        hours = (start_time_offset // 3600000) % 24
        minutes = (start_time_offset % 3600000) // 60000
        seconds = (start_time_offset % 60000) // 1000
        
        return {
            'simulation_info': {
                'start_time_offset_ms': start_time_offset,
                'start_time_absolute': start_time_offset,
                'start_time_human': time_str,
                'start_hour': hours,
                'start_minute': minutes,
                'start_second': seconds,
                'energy_aware': True,
                'scheduler_type': scheduler_type,
                'input_file': os.path.basename(input_file),
                'processing_timestamp': datetime.now().isoformat()
            },
            'energy_config': {
                'unit_time_ms': 50,
                'base_harvest_rate_j_per_s': 0.02,
                'base_harvest_rate_j_per_ms': 0.00002
            },
            'processing': {
                'script': 'trace_processor.py',
                'version': '2.0',
                'description': '整合了时间偏移修复和JSON处理功能'
            },
            'statistics': {
                'total_events': events_count,
                'time_range': {'min': 0, 'max': 0}
            }
        }
    
    def calculate_statistics(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算统计信息"""
        if not events:
            return {'event_types': {}, 'time_range': {'min': 0, 'max': 0}}
        
        event_types = {}
        times = []
        
        for event in events:
            # 统计事件类型
            event_type = event.get('event_type', 'unknown')
            event_types[event_type] = event_types.get(event_type, 0) + 1
            
            # 收集时间
            time_val = event.get('time', 0)
            if isinstance(time_val, (int, float)):
                times.append(time_val)
        
        time_range = {'min': min(times) if times else 0, 'max': max(times) if times else 0}
        
        return {
            'event_types': event_types,
            'time_range': time_range,
            'duration_ms': time_range['max'] - time_range['min'] if times else 0
        }
    
    def calculate_execution_times(self, events: List[Dict[str, Any]], task_runtimes: Dict[str, int]) -> Dict[str, Any]:
        """计算执行时间统计，与配置的runtime比较"""
        if not events:
            return {'execution_times': {}}
        
        # 创建字典来存储scheduled时间
        scheduled_times = {}
        for event in events:
            if event.get('event_type') == 'scheduled':
                task_name = event.get('task_name', '')
                arrival_time = event.get('arrival_time', 0)
                scheduled_time = event.get('time', 0)
                key = f"{task_name}_{arrival_time}"
                scheduled_times[key] = scheduled_time
        
        # 计算执行时间
        task_exec_times = {}
        for event in events:
            if event.get('event_type') == 'end_instance':
                task_name = event.get('task_name', '')
                arrival_time = event.get('arrival_time', 0)
                end_time = event.get('time', 0)
                
                # 查找对应的scheduled时间
                key = f"{task_name}_{arrival_time}"
                start_time = scheduled_times.get(key, arrival_time)
                
                exec_time = end_time - start_time
                if task_name not in task_exec_times:
                    task_exec_times[task_name] = []
                task_exec_times[task_name].append(exec_time)
        
        # 计算统计信息
        execution_stats = {}
        for task_name, times in task_exec_times.items():
            if times:
                avg_time = sum(times) / len(times)
                config_runtime = task_runtimes.get(task_name, 0)
                
                if config_runtime > 0:
                    ratio = avg_time / config_runtime
                    execution_stats[task_name] = {
                        'actual_avg_ms': avg_time,
                        'config_runtime_ms': config_runtime,
                        'ratio': ratio,
                        'instances': len(times),
                        'times': times
                    }
                else:
                    execution_stats[task_name] = {
                        'actual_avg_ms': avg_time,
                        'config_runtime_ms': 'N/A',
                        'ratio': 'N/A',
                        'instances': len(times),
                        'times': times
                    }
        
        return {
            'execution_times': execution_stats,
            'summary': {
                'total_tasks': len(execution_stats),
                'tasks_with_config': sum(1 for stats in execution_stats.values() 
                                       if stats.get('config_runtime_ms') != 'N/A')
            }
        }
    
    def process(self, input_file: str, output_file: str, start_time_offset: int = 0, 
               scheduler_type: str = "gpfp_asap", taskset_file: str = "") -> bool:
        """主处理函数"""
        logger.info("=" * 60)
        logger.info("PARTSim 追踪文件处理器 - 完整版")
        logger.info("=" * 60)
        
        # 检查文件
        if not os.path.exists(input_file):
            logger.error(f"输入文件不存在: {input_file}")
            return False
        
        # 解析事件
        events = self.parse_trace_file(input_file)
        
        if not events:
            logger.warning("警告: 未找到任何事件")
        
        # 检查事件是否已经包含时间偏移
        # 如果第一个事件的original_time字段存在且不为0，说明已经应用过偏移
        already_offset_applied = False
        if events and 'original_time' in events[0]:
            first_original_time = events[0].get('original_time', 0)
            first_time = events[0].get('time', 0)
            if first_original_time != 0 and first_time != first_original_time:
                already_offset_applied = True
                logger.info(f"检测到事件已包含时间偏移，跳过重复应用")
        
        # 应用时间偏移（如果没有已经应用过）
        if start_time_offset > 0 and not already_offset_applied:
            events = self.apply_time_offset(events, start_time_offset)
        elif start_time_offset > 0 and already_offset_applied:
            logger.info(f"时间偏移 {start_time_offset} ms 已包含在输入文件中")
        
        # 按时间排序
        events.sort(key=lambda x: x.get('time', 0))
        
        # 创建元数据
        metadata = self.create_metadata(start_time_offset, len(events), input_file, scheduler_type)
        
        # 计算统计信息
        stats = self.calculate_statistics(events)
        metadata['statistics'].update(stats)
        
        # 如果提供了任务集文件，计算执行时间校正
        execution_stats = {}
        if taskset_file and os.path.exists(taskset_file):
            try:
                import yaml
                with open(taskset_file, 'r') as f:
                    tasks_config = yaml.safe_load(f)
                
                if tasks_config and 'taskset' in tasks_config:
                    # 创建任务runtime映射
                    task_runtimes = {}
                    for task in tasks_config['taskset']:
                        task_name = task['name']
                        runtime = task['runtime']
                        task_runtimes[task_name] = runtime
                    
                    # 计算执行时间校正
                    corrected_stats = self.calculate_execution_times(events, task_runtimes)
                    execution_stats = corrected_stats.get('execution_times', {})
                    metadata['statistics'].update(corrected_stats)
                    metadata['task_runtimes'] = task_runtimes
                    
                    logger.info(f"已加载任务配置: {len(task_runtimes)} 个任务")
            except Exception as e:
                logger.warning(f"无法加载任务配置文件 {taskset_file}: {e}")
        
        # 创建输出结构
        output_data = {
            'events': events,
            'metadata': metadata
        }
        
        # 写入输出文件
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False, default=str)
            
            # 验证写入
            if os.path.exists(output_file):
                output_size = os.path.getsize(output_file)
                logger.info(f"写入成功: {output_file} ({output_size} 字节)")
                
                # 显示摘要（包含执行时间统计）
                self._print_summary(events, start_time_offset, stats, execution_stats)
                return True
            else:
                logger.error(f"写入后文件不存在: {output_file}")
                return False
                
        except Exception as e:
            logger.error(f"写入输出文件失败: {e}")
            
            # 尝试简单格式作为后备
            try:
                simple_data = {
                    'events': events,
                    'metadata': {
                        'start_time_offset': start_time_offset,
                        'total_events': len(events),
                        'error': str(e)
                    }
                }
                
                with open(output_file, 'w') as f:
                    json.dump(simple_data, f)
                
                logger.warning("使用简单格式写入成功")
                return True
                
            except Exception as e2:
                logger.error(f"后备方案也失败: {e2}")
                return False
    
    def _print_summary(self, events: List[Dict[str, Any]], start_time_offset: int, 
                      stats: Dict[str, Any], execution_stats: Dict[str, Any] = None) -> None:
        """打印处理摘要"""
        logger.info("\n" + "=" * 60)
        logger.info("处理摘要")
        logger.info("=" * 60)
        logger.info(f"事件总数: {len(events)}")
        
        if start_time_offset > 0:
            time_str = self.format_time(start_time_offset)
            logger.info(f"开始时间: {time_str} (偏移: {start_time_offset} ms)")
        
        # 显示事件类型分布
        event_types = stats.get('event_types', {})
        if event_types:
            logger.info("事件类型分布:")
            for etype, count in sorted(event_types.items(), key=lambda x: x[1], reverse=True):
                percentage = count / len(events) * 100
                logger.info(f"  {etype}: {count} ({percentage:.1f}%)")
        
        # 显示时间范围
        time_range = stats.get('time_range', {'min': 0, 'max': 0})
        if time_range['max'] > 0:
            duration_ms = time_range['max'] - time_range['min']
            duration_s = duration_ms / 1000.0
            logger.info(f"时间范围: {time_range['min']} - {time_range['max']} ms")
            logger.info(f"持续时间: {duration_ms} ms ({duration_s:.1f} 秒)")
        
        # 显示执行时间统计
        execution_times = execution_stats if execution_stats is not None else stats.get('execution_times', {})
        if execution_times:
            logger.info("执行时间统计:")
            for task_name, task_stats in sorted(execution_times.items()):
                actual_avg = task_stats.get('actual_avg_ms', 0)
                config_runtime = task_stats.get('config_runtime_ms', 'N/A')
                ratio = task_stats.get('ratio', 'N/A')
                instances = task_stats.get('instances', 0)
                
                if config_runtime != 'N/A' and ratio != 'N/A':
                    logger.info(f"  {task_name}: 配置={config_runtime}ms, 实际平均={actual_avg:.1f}ms, 比例={ratio:.2f}倍, 实例数={instances}")
                else:
                    logger.info(f"  {task_name}: 实际平均={actual_avg:.1f}ms, 实例数={instances}")
        
        logger.info("=" * 60)
    
    def json_time_offset_fix(self, input_file: str, output_file: str, offset_ms: int) -> bool:
        """JSON时间偏移修复（兼容旧接口）"""
        logger.info("使用JSON时间偏移修复功能")
        return self.process(input_file, output_file, offset_ms)
    
    def process_trace(self, input_file: str, output_file: str, offset_ms: int) -> bool:
        """处理追踪文件（兼容旧接口）"""
        logger.info("使用追踪处理功能")
        return self.process(input_file, output_file, offset_ms)

def main():
    """主函数 - 支持多种使用方式"""
    parser = argparse.ArgumentParser(
        description='PARTSim追踪文件处理器 - 整合版',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  1. 基本使用: python3 trace_processor.py input.json output.json 43200000
  2. 指定调度器: python3 trace_processor.py -i input.json -o output.json -t 43200000 -s gpfp_asap
  3. 仅修复时间偏移: python3 trace_processor.py --json-fix input.json output.json 43200000
  
时间偏移参考:
  0: 午夜 00:00
  28800000: 早上 08:00
  43200000: 中午 12:00
  64800000: 晚上 18:00
        """
    )
    
    # 子命令模式
    subparsers = parser.add_subparsers(dest='command', help='子命令')
    
    # 主处理命令
    main_parser = subparsers.add_parser('process', help='处理追踪文件')
    main_parser.add_argument('input_file', help='输入文件')
    main_parser.add_argument('output_file', help='输出文件')
    main_parser.add_argument('time_offset', type=int, help='时间偏移（毫秒）')
    main_parser.add_argument('--scheduler', default='gpfp_asap', help='调度器类型')
    main_parser.add_argument('--verbose', action='store_true', help='详细输出')
    
    # JSON时间偏移修复命令
    json_parser = subparsers.add_parser('json-fix', help='JSON时间偏移修复')
    json_parser.add_argument('input_file', help='输入文件')
    json_parser.add_argument('output_file', help='输出文件')
    json_parser.add_argument('time_offset', type=int, help='时间偏移（毫秒）')
    json_parser.add_argument('--verbose', action='store_true', help='详细输出')
    
    # 兼容模式（旧用法）
    parser.add_argument('input_file', nargs='?', help='输入文件')
    parser.add_argument('output_file', nargs='?', help='输出文件')
    parser.add_argument('time_offset', nargs='?', type=int, help='时间偏移（毫秒）')
    parser.add_argument('-i', '--input', help='输入文件')
    parser.add_argument('-o', '--output', help='输出文件')
    parser.add_argument('-t', '--time', type=int, help='时间偏移（毫秒）')
    parser.add_argument('-s', '--scheduler', default='gpfp_asap', help='调度器类型')
    parser.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    parser.add_argument('--version', action='store_true', help='显示版本')
    
    args = parser.parse_args()
    
    # 显示版本
    if args.version:
        logger.info("PARTSim Trace Processor v3.0 - 整合版")
        return 0
    
    # 确定输入参数
    input_file = args.input or args.input_file
    output_file = args.output or args.output_file
    time_offset = args.time or args.time_offset
    
    # 检查必需参数
    if not input_file or not output_file or time_offset is None:
        if args.command:
            # 子命令模式已处理
            pass
        else:
            parser.print_help()
            return 1
    
    # 创建处理器
    processor = TraceProcessor(verbose=args.verbose)
    
    # 根据命令执行相应操作
    if args.command == 'process' or (input_file and output_file and time_offset is not None):
        scheduler = getattr(args, 'scheduler', 'gpfp_asap')
        success = processor.process(input_file, output_file, time_offset, scheduler)
        return 0 if success else 1
    elif args.command == 'json-fix':
        success = processor.json_time_offset_fix(input_file, output_file, time_offset)
        return 0 if success else 1
    else:
        parser.print_help()
        return 1

# 兼容性函数
def process_trace_file(input_file: str, output_file: str, time_offset: int) -> bool:
    """兼容性函数 - 供其他脚本调用"""
    processor = TraceProcessor()
    return processor.process(input_file, output_file, time_offset)

def fix_json_time_offset(input_file: str, output_file: str, time_offset: int) -> bool:
    """兼容性函数 - 修复JSON时间偏移"""
    processor = TraceProcessor()
    return processor.json_time_offset_fix(input_file, output_file, time_offset)

if __name__ == "__main__":
    sys.exit(main())
