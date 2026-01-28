#!/bin/bash
echo "=== 验证文件一致性 ==="
echo ""
echo "1. 检查6mJ场景的初始能量配置:"
grep "initial_energy" system_3core_tie_0.006J.yml
echo ""
echo "2. 检查6mJ场景的任务完成数:"
echo "TIE:"
grep "任务完成数:" tie_0.006J.log | tail -1
echo "BTIE:"
grep "任务完成数:" btie_0.006J.log | tail -1
echo "TGF:"
grep "任务完成数:" tgf_0.006J.log | tail -1
echo ""
echo "3. 检查6mJ场景的剩余能量:"
echo "TIE:"
grep "剩余能量:" tie_0.006J.log | tail -1
echo "BTIE:"
grep "剩余能量:" btie_0.006J.log | tail -1
echo "TGF:"
grep "剩余能量:" tgf_0.006J.log | tail -1
