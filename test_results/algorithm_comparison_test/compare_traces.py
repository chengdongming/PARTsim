import json

# 读取三个追踪文件
with open('traces/trace_tie_12mj.json') as f:
    tie = json.load(f)
with open('traces/trace_tgf_12mj.json') as f:
    tgf = json.load(f)
with open('traces/trace_btie_12mj.json') as f:
    btie = json.load(f)

print("=" * 80)
print("三种算法追踪文件对比分析（12mJ场景）")
print("=" * 80)

# 对比arrival事件
print("\n【1. Arrival事件对比】")
for i in range(3):
    tie_evt = tie['events'][i]
    tgf_evt = tgf['events'][i]
    btie_evt = btie['events'][i]
    
    print(f"\n任务{i+1} arrival:")
    print(f"  TIE:  energy={tie_evt['current_energy_mJ']:.3f} mJ, consumed={tie_evt['total_consumed_mJ']:.3f} mJ")
    print(f"  TGF:  energy={tgf_evt['current_energy_mJ']:.3f} mJ, consumed={tgf_evt['total_consumed_mJ']:.3f} mJ")
    print(f"  BTIE: energy={btie_evt['current_energy_mJ']:.3f} mJ, consumed={btie_evt['total_consumed_mJ']:.3f} mJ")
    
    if tie_evt['current_energy_mJ'] == tgf_evt['current_energy_mJ'] == btie_evt['current_energy_mJ']:
        print("  ✓ 三者一致")
    else:
        print("  ✗ 存在差异")

# 对比scheduled事件
print("\n【2. Scheduled事件对比】")
for i in [3, 4]:  # task_1和task_2的scheduled事件
    tie_evt = tie['events'][i]
    tgf_evt = tgf['events'][i]
    btie_evt = btie['events'][i]
    
    print(f"\n{tie_evt['task_name']} scheduled:")
    print(f"  TIE:  energy={tie_evt['current_energy_mJ']:.3f} mJ, consumed={tie_evt['total_consumed_mJ']:.3f} mJ")
    print(f"  TGF:  energy={tgf_evt['current_energy_mJ']:.3f} mJ, consumed={tgf_evt['total_consumed_mJ']:.3f} mJ")
    print(f"  BTIE: energy={btie_evt['current_energy_mJ']:.3f} mJ, consumed={btie_evt['total_consumed_mJ']:.3f} mJ")
    
    if tie_evt['current_energy_mJ'] == btie_evt['current_energy_mJ']:
        print("  → TIE和BTIE一致")
    if tgf_evt['current_energy_mJ'] != tie_evt['current_energy_mJ']:
        print("  → TGF与TIE/BTIE不同（能量扣除时机差异）")

# 对比end_instance事件
print("\n【3. End_instance事件对比】")
tie_end = tie['events'][5]  # task_1 end
tgf_end = tgf['events'][5]
btie_end = btie['events'][5]

print(f"\ntask_1 end_instance (时间5ms):")
print(f"  TIE:  energy={tie_end['current_energy_mJ']:.3f} mJ, consumed={tie_end['total_consumed_mJ']:.3f} mJ, task_consumed={tie_end['task_consumed_mJ']:.3f} mJ")
print(f"  TGF:  energy={tgf_end['current_energy_mJ']:.3f} mJ, consumed={tgf_end['total_consumed_mJ']:.3f} mJ, task_consumed={tgf_end['task_consumed_mJ']:.3f} mJ")
print(f"  BTIE: energy={btie_end['current_energy_mJ']:.3f} mJ, consumed={btie_end['total_consumed_mJ']:.3f} mJ, task_consumed={btie_end['task_consumed_mJ']:.3f} mJ")

print("\n【4. 能量消耗分析】")
print(f"初始能量: 12 mJ")
print(f"\nTIE/BTIE:")
print(f"  - Scheduled时扣除: 1.116 mJ (两个任务各0.558 mJ)")
print(f"  - Task_1执行消耗: 4.464 mJ")
print(f"  - 剩余能量: 12 - 1.116 - 4.464 = {12 - 1.116 - 4.464:.3f} mJ")
print(f"\nTGF:")
print(f"  - Scheduled时未扣除: 0 mJ")
print(f"  - Task_1执行消耗: 4.464 mJ")
print(f"  - 剩余能量: 12 - 4.464 = {12 - 4.464:.3f} mJ")
print(f"  ⚠️ 问题：scheduled的初始能量何时扣除？")

