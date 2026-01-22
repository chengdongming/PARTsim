#!/usr/bin/env python3
"""
抢占测试脚本
测试场景：低优先级任务先执行，高优先级任务到达后抢占
"""

import sys
import json
import yaml

# 添加librtsim Python绑定路径
sys.path.insert(0, '/home/devcontainers/PARTSim-project/build/librtsim')

from rtsim import *

def create_task_from_config(kernel, task_config, scheduler):
    """从配置创建任务"""
    name = task_config['name']
    period = task_config['period']
    wcet = task_config['wcet']
    arrival = task_config['arrival']
    deadline = task_config['deadline']
    workload = task_config['workload']
    energy_coeff = task_config.get('energy_coefficient', 1.0)

    # 创建周期性任务
    task = PeriodicTask(period, wcet, name, deadline)
    task.insertCode("fixed(0," + str(wcet) + ")")

    # 设置任务到达时间
    task.setArrival(arrival)

    # 添加到kernel
    kernel.addTask(task, f"{workload}:{energy_coeff}")

    return task

def run_preemption_test():
    """运行抢占测试"""

    print("=" * 60)
    print("抢占测试开始")
    print("=" * 60)

    # 加载配置
    with open('/home/devcontainers/PARTSim-project/test_preemption.yml', 'r') as f:
        config = yaml.safe_load(f)

    system_cfg = config['system']
    sched_cfg = config['scheduler']
    cpus_cfg = config['cpus']
    tasks_cfg = config['tasks']

    # 创建仿真环境
    SIMUL.init()

    # 创建CPU
    cpus = []
    for cpu_cfg in cpus_cfg:
        cpu = CPU(
            cpu_cfg['id'],
            cpu_cfg.get('frequency', 1000),
            cpu_cfg.get('voltage', 1.0)
        )
        cpus.append(cpu)

    # 创建调度器
    scheduler = Scheduler.createInstance(
        sched_cfg['type'],
        [
            f"energy={system_cfg['energy']['initial']}",
            f"max_energy={system_cfg['energy']['max']}",
            f"pv_efficiency={system_cfg['energy']['pv_efficiency']}",
            f"pv_area={system_cfg['energy']['pv_area']}",
            f"solar_data={system_cfg['energy']['solar_data']}",
            f"tick_period={sched_cfg['tick_period']}"
        ]
    )

    # 创建Kernel
    kernel = MRTKernel(cpus, scheduler, "MRTKernel")

    # 设置调度器的kernel
    scheduler.setKernel(kernel)

    # 创建任务
    tasks = []
    for task_cfg in tasks_cfg:
        task = create_task_from_config(kernel, task_cfg, scheduler)
        tasks.append(task)
        print(f"✅ 创建任务: {task_cfg['name']}, 周期={task_cfg['period']}ms, WCET={task_cfg['wcet']}ms, 到达时间={task_cfg['arrival']}ms")

    # 设置仿真结束时间
    SIMUL.run_to(system_cfg['simulation']['duration'])

    # 运行仿真
    print("\n🚀 开始仿真...")
    SIMUL.run()

    print("\n✅ 仿真完成")

    # 输出统计信息
    scheduler.printStats()

    # 生成追踪文件
    trace_events = []
    for task in tasks:
        task_name = task.getName()
        # 收集任务的执行记录
        # 这里需要根据实际的追踪接口调整

    # 保存追踪到JSON
    trace_file = '/home/devcontainers/PARTSim-project/trace_preemption_test.json'
    with open(trace_file, 'w') as f:
        json.dump({"events": trace_events}, f, indent=2)

    print(f"\n📄 追踪文件已保存到: {trace_file}")

    return scheduler, tasks

if __name__ == "__main__":
    try:
        run_preemption_test()
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
