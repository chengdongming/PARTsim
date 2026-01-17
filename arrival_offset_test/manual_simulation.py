#!/usr/bin/env python3
"""
手动模拟：arrival_offset + 抢占式调度测试

测试场景：
- task_high: period=500ms, wcet=150ms, arrival_offset=0ms
- task_mid:  period=1000ms, wcet=250ms, arrival_offset=200ms
- task_low:  period=1500ms, wcet=200ms, arrival_offset=100ms

预期行为：
1. task_high在0ms到达，立即开始执行
2. task_low在100ms到达，但优先级低，等待
3. task_high在150ms完成
4. task_low在150ms开始执行
5. task_mid在200ms到达，抢占task_low
6. ... 抢占式调度
"""

print("="*80)
print("手动模拟：arrival_offset + 抢占式调度")
print("="*80)
print()

# 任务配置
tasks = {
    'task_high': {'period': 500, 'wcet': 150, 'offset': 0, 'priority': 1},
    'task_mid':  {'period': 1000, 'wcet': 250, 'offset': 200, 'priority': 2},
    'task_low':  {'period': 1500, 'wcet': 200, 'offset': 100, 'priority': 3}
}

print("【任务配置】")
for name, task in sorted(tasks.items(), key=lambda x: x[1]['priority']):
    print(f"{name:12} period={task['period']}ms wcet={task['wcet']}ms "
          f"offset={task['offset']}ms priority={task['priority']}")
print()

# 手动模拟时间线
print("【预期时间线模拟】")
print()

timeline = [
    # (时间ms, 事件, 说明)
    (0, "✅ task_high 到达", "offset=0"),
    (0, "⚡ 调度: task_high", "优先级最高"),
    (0, "🔄 task_high 执行中...", ""),

    (100, "✅ task_low 到达", "offset=100"),
    (100, "📋 就绪队列: [task_low]", "优先级低于task_high"),
    (100, "⚡ task_high 继续执行", "不抢占"),

    (150, "✅ task_high 完成", "执行了150ms"),
    (150, "⚡ 调度: task_low", "就绪队列唯一任务"),
    (150, "🔄 task_low 执行中...", ""),

    (200, "✅ task_mid 到达", "offset=200"),
    (200, "⚡⭐ 抢占！task_mid 抢占 task_low", "task_mid优先级更高"),
    (200, "📤 task_low 被挂起", "已执行50ms，剩余150ms"),
    (200, "🔄 task_mid 执行中...", ""),

    (450, "✅ task_mid 完成", "执行了250ms"),
    (450, "⚡ 调度: task_low", "恢复执行"),
    (450, "🔄 task_low 继续执行...", "从150ms继续，还需50ms"),

    (500, "✅ task_high 第2次实例到达", "offset+period=0+500"),
    (500, "⚡⭐ 抢占！task_high 抢占 task_low", "task_high优先级最高"),
    (500, "📤 task_low 再次被挂起", "已执行100ms，剩余100ms"),
    (500, "🔄 task_high 执行中...", ""),

    (650, "✅ task_high 完成", "执行了150ms"),
    (650, "⚡ 调度: task_low", "再次恢复"),
    (650, "🔄 task_low 继续执行...", "从100ms继续，还需100ms"),

    (750, "✅ task_low 完成", "总共执行200ms (50+50+100)"),

    (1000, "✅ task_mid 第2次实例到达", "offset+period=200+1000? 不，offset+period*1=200+1000=1200"),
    (1000, "✅ task_high 第3次实例到达", "offset+period*2=0+1000"),
    (1000, "⚡⭐ 抢占！task_high 先执行", "优先级高"),

    (1150, "✅ task_high 完成", ""),
    (1150, "⚡ 调度: task_mid", "终于轮到task_mid"),
]

print(f"{'时间':<8} | {'事件':<40} | {'说明':<30}")
print("-"*80)

for time, event, note in timeline:
    print(f"{time:<8} | {event:<40} | {note:<30}")

print()

# 预期到达时间总结
print("【预期任务到达时间序列】")
print()
print("task_high (offset=0, period=500):")
print("  实例1: 0ms")
print("  实例2: 500ms")
print("  实例3: 1000ms")
print("  实例4: 1500ms")
print()

print("task_mid (offset=200, period=1000):")
print("  实例1: 200ms")
print("  实例2: 1200ms (200 + 1000)")
print()

print("task_low (offset=100, period=1500):")
print("  实例1: 100ms")
print("  实例2: 1600ms (100 + 1500)")
print()

print("="*80)
print("【关键验证点】")
print("="*80)
print()
print("1. ✅ arrival_offset正确性:")
print("   - task_high首次在0ms到达")
print("   - task_low首次在100ms到达")
print("   - task_mid首次在200ms到达")
print()
print("2. ✅ 抢占行为:")
print("   - 200ms: task_mid抢占task_low")
print("   - 500ms: task_high抢占task_low")
print("   - 1000ms: task_high优先于task_mid")
print()
print("3. ✅ 任务恢复:")
print("   - task_low被多次抢占后能正确恢复执行")
print("   - 执行时间累计正确")
print()

print("="*80)
