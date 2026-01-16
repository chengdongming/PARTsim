#!/usr/bin/env python3
"""
诊断EPP调度器trace文件
"""
import json

def analyze_trace(trace_file):
    with open(trace_file, 'r') as f:
        data = json.load(f)

    events = data.get('events', [])
    print(f"总事件数: {len(events)}\n")

    # 统计事件类型
    event_types = {}
    for event in events:
        etype = event.get('event_type', 'unknown')
        event_types[etype] = event_types.get(etype, 0) + 1

    print("事件类型统计:")
    for etype, count in sorted(event_types.items()):
        print(f"  {etype}: {count}")

    print("\n前20个事件:")
    for i, event in enumerate(events[:20]):
        print(f"  {i}: {event.get('event_type', 'unknown')} - {event.get('task_name', 'N/A')} @ {event.get('time', 0)}ms")

    # 检查是否有scheduled事件
    has_scheduled = any(e.get('event_type') == 'scheduled' for e in events)
    has_descheduled = any(e.get('event_type') == 'descheduled' for e in events)

    print(f"\n关键检查:")
    print(f"  有scheduled事件: {has_scheduled}")
    print(f"  有descheduled事件: {has_descheduled}")

    if not has_scheduled:
        print("\n⚠️ 警告: 没有发现scheduled事件！")
        print("可能原因:")
        print("  1. 初始能量(1.0J)太低，所有任务都无法调度")
        print("  2. EPP调度器的getFirst()/getTaskN()因为能量检查返回nullptr")
        print("  3. 任务在到达后立即进入等待队列")

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        trace_file = sys.argv[1]
    else:
        trace_file = 'trace_epp_test.json'

    analyze_trace(trace_file)
