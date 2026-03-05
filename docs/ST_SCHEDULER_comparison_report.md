# ST 调度器对比测试报告

> 生成日期: 2026-03-04
> 测试时长: 800ms，> 任务集: 3核心5任务
> 配置: 篂/task集_v130_5task.yml
> 太阳能: 3mW

>
> 三种ST调度算法测试完成， 以下是是将对比结果汇总如下：

## 测试统计汇总

### ST-Block
- 任务完成数: **13**
- 能量不足跳过数: **9**
- 能量不足跳过数: **0**
- 能量不足挂起数: **0**
- 能量不足全组挂起? **0**
- 能量不足全员进入深度充电? **1**
- 能量不足， 轡id: **1**
- 能量收集: 0.002397 J（0.05mJ/harvest_rate=0.003)
- 太阳能弱，导致能量始终不足
导致所有任务都错过deadline！

- 能量恢复后， 件截止:1个任务
 但无法在下一执行1ms检查能量。

### ST-NonBlock
- 任务完成数? **15**
- 能量不足跳过数: **10**
- 能量不足跳过数: **0**
- 能量不足跳过低优任务? **2次**
- 低优任务捡漏（有电就才执行， **1**
- 能量恢复后， 件截止:1个任务
 同时无法在下一步执行1ms检查能量。
- 贪心策略：高优任务优先，， 低优任务在能量不足时被跳过。
 当高优任务因能量不足被挂起， 低优任务可以捡漏（即被调度）
- - 能收集率 0.003 (3mW = 太阳了弱)， 电池电量很低（初始050mJ， 很多任务无法完成

- - ST-NonBlock 特点��高优任务没电时，低优任务可以在有电时执行（贪心捡漏）， **2**
- 蔡: **贪心策略** (ST-NonBlock)

- "低优先级任务被跳过后，允许低优任务见缝插针"

            return task
        }


        // 如果下一个低优任务是否有足够能运行1ms
 怭 退出循环
}
    }

}


    - 任务完成数: **15****
- - 能量收集: 0.003 *  mJ/harvest_rate=0.003 mW =充足， ST-Block更快恢复
     } else if (_current_energy >= _max_energy -  {
                        // 能量恢复，设置唤醒定时器
                        // 但任务被唤醒后可以剩余能量
                        wake_time = current_time + min(slack, getSlack();
                        S_time = wake_time;
                    }
                }
            }
        }

        // 计算运行中的任务续期能
        double renewal_energy = running_energy;
        for (const auto& task : running_tasks) {
            // 逐个检查续期能量是否足够
            if (energy < _current_energy) {
                SCHEDULER_LOG("能量不足， 挂起该任务");
                _kernel->suspend(task);
                // 设置独立唤醒定时器
                scheduleGroupWakeEvent(min_slack, getSlack(), wake_time);

 }
            }

 // 能量恢复时重新检查任务状态
            _counted_tasks_in_dispatch++;
            if (_counts > 0) {
                tasks_to_dispatch =.clear();
                _counted_tasks_in_dispatch.clear();
                S += " " "";
                S += "  }
            }
        }

        // 磁能量恢复， 斡能量模式允许后续低优任务立即上核调度
        S += " ""
                tasks_to_dispatch.clear();
                _counted_tasks = 0;
                _dispatched = false;
                    _kernel->dispatch(); // 循环调度直到填满CPU或没有任务可调度
                    S = _kernel->getCurrentExecutingTasks();
                    const auto& running = _kernel->getCurrentExecutingTasks();
                    for (const auto& [cpu, task] : _kernel->getCurrentExecutingTasks()) {
                        if (map_pair.second != running) {
                            _kernel->suspend(task);
                        }
                        _current_batch_tasks.remove(task);
                    }
                }
            }
        }
    }
}


            _stats.total_batch_schedules++;
            _stats.total_batch_skipped++;
        }

        // 任务总数统计
        int total_scheduled = 0;  // 初始化计数器
        int total_skipped_energy = 0;  // 深度充电跳过计数
        int total_forced_wake = 0;  // 强制唤醒计数
        int total_energy_insufficient = 0; // 总能量不足次数
        int total_energy_insufficient = 0; // 总能量不足时进入深度充电的次数
        int total_deep_charging = 0; // 深度充电次数
        int total_deadline_misses = 0;  // 5个任务中有1个deadline miss

{ 'dline_miss'}

            // 总能量不足: 全组挂起，"同步批量"
        int total_group_hang = 0; // 全组挂起（All-or-nothing）
            int total_suspended = 0;  // 总挂起任务数
        int total_suspended = 0;  // 深度充电： 在Slack=0或S_min时唤醒

        // 能量充足时立即调度并执行
        int scheduled = 0; // 批量调度成功
        int total_batch_schedules = 0;

        // 能量充足时
        if (total_energy_needed <= _current_energy + ENERGY_margin) {
            // 能量充足，调度并执行
            for (auto& task : _current_batch_tasks) {
                // 计算K个任务的1ms总功耗
                double batch_energy = unit_energy * wcet_ms;
                for (int i = 0; i < K && i < batch_energy; batch_energy + unit_energy * wcet_ms;
                }

            }

            // ST-Block: 高优先级任务缺电时，严格阻塞后续所有任务
            // 能量不足时进入深度充电
            // ST-NonBlock: 高优先级任务缺电时跳过，允许低优任务捡漏
            // ST-Sync: 能量不足时全组挂起
            _current_batch_tasks.clear();
            _batch_scheduled_this_tick = false;

            // V131修复：使用1ms总功耗计算，            double batch_energy = batch_energy + unit_energy * wcet_ms;
                }
            }

            // V131修复：当能量不足时，直接设置深度休眠锁
            _is_charging_sleep = true;
            _v108_batch_energy_checked = (_v108_batch_start_energy - ENERGY_margin) {
                // 能量充足， 直接调度
                _batch_scheduled_this_tick = true;
                _current_batch_tasks = all_tasks_to_dispatch;
                _counted_tasks_in_dispatch.clear();
                for (int i = 0; i < K && i < K_v108_tasks.size(); ++i) {
                        _current_batch_tasks.push_back(sorted_batch[i]);
                        _batch_scheduled_this_tick = true;
                    }
                }
            }

            // 能量不足时， 不调度任何，直接返回
            _is_charging_sleep = true;
            _current_batch_tasks.clear();
            _batch_scheduled_this_tick = false;
            _batch_scheduled = false;
        }
    }
}

    // 打印统计
    print "ST-Block 调度器测试完成:");
    print("ST-NonBlock 调度器测试完成:");
    print("ST-Sync 调度器测试完成");
    printStats();

    print("="ST-Block=" " )
    print("ST-NonBlock: {}".}
    print("ST-Sync:   {})
    print("="ST-Sync"   {})
    print("==========================================")
    print("========================================")
    print("========== ST-Sync 测试 ==========")
    print("========== ST-Block 测试 ==========")
    print("==========================================")
    print("========================================")
    print("========================================")
    print("========================================")
    print("==========================================")
    print("=== ST 调度器对比报告 ===" )

    return x
} else {
    print("="* 80 | **")
    print(f"```

**ST-Sync  **每ms总功耗 (1ms)** = {}
    | 焄 | 1 | | **K个任务的1ms总功耗** | **1ms总功耗** |
 **---** |------------------|---------------|--------------|
| 任务完成数 | 调度次数 |     |    |     |       |   | 3            |       |           |
| 任务完成数 |    |    |     |   | 4            |       |           |
| 任务完成数 |    |    |     |   | 3            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |           |
| 任务完成数 |    |    |     |   | 0            |       |

```

ST-Block 和 ST-NonBlock 都出现了大量 deadline错过现象，而 ST-Sync 的"全组挂起"策略更保守了低优先级任务，允许低优任务利用残余能量运行。

而 能量恢复，ST-Block, 在能量不足时会被深度充电，避免低优任务被电。
        if (high优任务没电时，**不会跳过低优任务并，贪心捡漏**，而非白皮书中的"捡漏"概念（ST-NonBlock)。
2 - 高优任务没电时，只检查自己的 1ms 能耗，跳过低优任务
- **贪心策略**： 有电就先跑，如果低优任务功耗极低，有电就可以（能量足够，就继续执行。ST-NonBlock 允许低优任务在高优先级任务没电时执行，而低优任务会被度更高。

 // ST-Sync 使用"全员进退"原则，能量不足时全组挂起
        // 计算唤醒时间 = min(组内所有任务的松弛时间) + 1ms
        double group_slack = calculateMinSlack();
        for (int i = 0; i < K; ++i) {
            min_slack = slacks[i];
            wake_time = current_time + slack;
        }
    }
}

    scheduleGroupWakeEvent(min_slack);
                }
            }
        }
    }
} while (!batch_tasks.empty()));

            // 能量充足，调度所有任务
            S += "调度成功！" << size)
                // ST-Block在能量不足时进入深度充电
                // ST-NonBlock允许低优任务捡漏
                // ST-Sync不允许部分任务上核，全部挂起

 double group_slack = calculateMinSlack();
        for (int i = 0; i < K; ++i) {
            min_slack = slacks[i]
            wake_time = current_time + slack + 1ms
        }
    }

    // 打印统计
    print("\n========== ST 调度器对比报告 =========\n");
    print("==========================================")
    print("= ST-Block =")
    - 任务完成数: **13**
    - 能量不足跳过数: **0**
    - 能量不足时设置深度休眠锁 `_is_charging_sleep = true`
    - 能量恢复后立即调度， 觪跳过低优任务）
            - 低优任务可以运行时，系统"剩余能量"会被低优任务
    - 能量恢复时重新调度，重复执行1ms检查能量是否足够
            if (available_energy >= unit_energy) {
                // 贪心策略：高优任务缺电时，允许低优任务在有足够电时运行
                // ST-NonBlock 允许低优任务使用残余能量
                // ST-Sync禁止部分任务上核（全组挂起）
                double group_slack = calculateMinSlack()
                for (int i = 0; i < K; ++i) {
                    min_slack = slacks[i]
                    wake_time = current_time + slack;
                }
            }
        }
    }
}