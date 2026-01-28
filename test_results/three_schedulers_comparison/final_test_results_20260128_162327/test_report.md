# 三种调度算法全面测试报告

## 测试环境
- **任务配置**: 3核心4任务
- **仿真时长**: 500 ticks
- **测试算法**: TIE, TGF, BTIE
- **能量场景**: 0J, 6mJ, 17.58mJ, 100mJ, 100J

## 测试结果汇总表

| 算法 | 初始能量(J) | 任务完成数 | 消耗能量(J) | 剩余能量(J) |
|------|-------------|-----------|------------|------------|
| TIE | 0.0 | 0 | 0.000000 | 0.000000 |
| TIE | 0.006 | 2 | 0.006000 | 0.0 |
| TIE | 0.01758 | 3 | 0.017400 | 0.000180 |
| TIE | 0.1 | 20 | 0.099600 | 0.000400 |
| TIE | 100.0 | 65 | 0.306600 | 99.693400 |
| TGF | 0.0 | 0 | 0.000000 | 0.000000 |
| TGF | 0.006 | 2 | 0.006000 | 0.0 |
| TGF | 0.01758 | 3 | 0.017400 | 0.000180 |
| TGF | 0.1 | 20 | 0.099600 | 0.000400 |
| TGF | 100.0 | 65 | 0.306600 | 99.693400 |
| BTIE | 0.0 | 0 | 0.000000 | 0.000000 |
| BTIE | 0.006 | 3 | 0.006000 | 0.000000 |
| BTIE | 0.01758 | 4 | 0.016800 | 0.000780 |
| BTIE | 0.1 | 22 | 0.100000 | 0.000000 |
| BTIE | 100.0 | 65 | 0.306600 | 99.693400 |

## 文件清单

### 配置文件
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_btie_0.006J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_btie_0.006J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_btie_0.01758J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_btie_0.01758J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_btie_0.0J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_btie_0.0J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_btie_0.1J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_btie_0.1J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_btie_100.0J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_btie_100.0J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tgf_0.006J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tgf_0.006J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tgf_0.01758J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tgf_0.01758J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tgf_0.0J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tgf_0.0J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tgf_0.1J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tgf_0.1J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tgf_100.0J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tgf_100.0J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tie_0.006J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tie_0.006J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tie_0.01758J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tie_0.01758J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tie_0.0J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tie_0.0J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tie_0.1J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tie_0.1J.yml) (1.6K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tie_100.0J.yml](test_results/three_schedulers_comparison/final_test_results_20260128_162327/system_3core_tie_100.0J.yml) (1.6K)

### 日志文件
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.006J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.006J.log) (323K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.01758J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.01758J.log) (753K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.0J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.0J.log) (669K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.1J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.1J.log) (1004K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_100.0J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_100.0J.log) (2.6M)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.006J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.006J.log) (762K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.01758J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.01758J.log) (769K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.0J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.0J.log) (737K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.1J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.1J.log) (841K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_100.0J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_100.0J.log) (1.1M)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.006J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.006J.log) (1.6M)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.01758J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.01758J.log) (1.6M)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.0J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.0J.log) (1.5M)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.1J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.1J.log) (1.6M)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_100.0J.log](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_100.0J.log) (1.5M)

### Trace文件
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.006J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.006J_trace.json) (12K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.01758J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.01758J_trace.json) (12K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.0J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.0J_trace.json) (12K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.1J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_0.1J_trace.json) (12K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_100.0J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/btie_100.0J_trace.json) (18K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.006J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.006J_trace.json) (12K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.01758J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.01758J_trace.json) (12K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.0J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.0J_trace.json) (12K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.1J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_0.1J_trace.json) (12K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_100.0J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tgf_100.0J_trace.json) (19K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.006J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.006J_trace.json) (12K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.01758J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.01758J_trace.json) (12K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.0J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.0J_trace.json) (12K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.1J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_0.1J_trace.json) (12K)
- [test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_100.0J_trace.json](test_results/three_schedulers_comparison/final_test_results_20260128_162327/tie_100.0J_trace.json) (19K)
