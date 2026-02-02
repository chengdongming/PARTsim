#!/bin/bash

# 运行太阳能测试并生成对比报告

# 测试时间点
hours=(0 4 8 10 12 15)
# 算法
algos=(tie tgf btie)

# 手动计算的预期值（基于NASA数据）
declare -A expected_energy
expected_energy[0]=0.0000
expected_energy[4]=0.1701
expected_energy[8]=5.3577
expected_energy[10]=10.2451
expected_energy[12]=10.4648
expected_energy[15]=3.0019

# 输出文件
output_file="test_results/solar_test/final_comparison.txt"

# 清空输出文件
> "$output_file"

# 写入标题
cat >> "$output_file" << 'EOF'
===========================================
太阳能收集测试：手动计算 vs 实际仿真对比
===========================================

测试条件：
- 日期：第182天（7月1日，夏季）
- 仿真时长：100ms
- 初始能量：100J
- 光伏效率：0.18
- 光伏面积：1.0 m²

-------------------------------------------
手动计算预期值（基于NASA太阳能数据）
-------------------------------------------
时刻 | 辐照度(W/m²) | 功率(W)   | 预期收集(J)
-----|-------------|-----------|------------
0h   | 0.00        | 0.000     | 0.0000
4h   | 9.45        | 1.701     | 0.1701
8h   | 297.65      | 53.577    | 5.3577
10h  | 569.17      | 102.451   | 10.2451
12h  | 581.38      | 104.648   | 10.4648
15h  | 166.77      | 30.019    | 3.0019

-------------------------------------------
实际仿真结果
-------------------------------------------

EOF

# 对每个算法运行测试
for algo in "${algos[@]}"; do
    echo "=== ${algo^^} 算法 ===" >> "$output_file"
    echo "时刻 | 完成任务 | 消耗(J)  | 收集(J)  | 剩余(J)   | 误差(J)" >> "$output_file"
    echo "-----|----------|----------|----------|-----------|--------" >> "$output_file"

    for hour in "${hours[@]}"; do
        echo "运行 $algo 算法在 ${hour}h 的测试..."

        # 运行仿真并捕获输出
        output=$(./build/rtsim/rtsim \
            test_results/solar_test/config_${algo}_${hour}h.yml \
            test_results/preemption_test/tasks_preemption_v3.yml \
            100 \
            -t test_results/solar_test/${hour}h/traces/${algo}_trace.json 2>&1)

        # 提取统计信息（去除ANSI颜色代码）
        # BTIE的统计标题是"BTIE批量调度统计"，其他是"XXX调度统计"
        if [ "$algo" = "btie" ]; then
            stats=$(echo "$output" | sed 's/\x1b\[[0-9;]*m//g' | grep -A 10 "BTIE批量调度统计")
        else
            stats=$(echo "$output" | sed 's/\x1b\[[0-9;]*m//g' | grep -A 10 "${algo^^}调度统计")
        fi

        # 提取各项数值
        tasks=$(echo "$stats" | grep "任务完成数:" | sed 's/.*任务完成数: \([0-9]*\).*/\1/')
        consumed=$(echo "$stats" | grep "总消耗能量:" | sed 's/.*总消耗能量: \([0-9.]*\)J.*/\1/')
        collected=$(echo "$stats" | grep "总收集能量:" | sed 's/.*总收集能量: \([0-9.]*\)J.*/\1/')
        remaining=$(echo "$stats" | grep "剩余能量:" | sed 's/.*剩余能量: \([0-9.]*\)J.*/\1/')

        # 计算误差
        expected=${expected_energy[$hour]}
        if [ -n "$collected" ] && [ -n "$expected" ]; then
            error=$(echo "$collected - $expected" | bc -l)
        else
            error="N/A"
        fi

        # 输出结果
        printf "%sh   | %-8s | %-8s | %-8s | %-9s | %s\n" \
            "$hour" "$tasks" "$consumed" "$collected" "$remaining" "$error" >> "$output_file"
    done

    echo "" >> "$output_file"
done

echo "测试完成！结果已保存到 $output_file"
cat "$output_file"
