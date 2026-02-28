#include <rtsim/json_trace.hpp>

namespace RTSim {

    using namespace MetaSim;

    JSONTrace::JSONTrace(const string &name) {
        fd.open(name.c_str());
        fd << "{" << std::endl;
        fd << "    \"events\" : [" << std::endl;
        first_event = true;
        max_time = MetaSim::Tick(-1);
        _energy_provider = nullptr;
    }

    JSONTrace::JSONTrace(const string &name, MetaSim::Tick max) {
        fd.open(name.c_str());
        fd << "{" << std::endl;
        fd << "    \"events\" : [" << std::endl;
        first_event = true;
        max_time = max;
        _energy_provider = nullptr;
    }

    // V98修复：显式清空容器，避免析构顺序问题
    JSONTrace::~JSONTrace() {
        fd << "] }" << std::endl;

        // 先清空所有容器
        _task_start_times.clear();
        _task_start_consumed.clear();
        _deadline_missed_tasks.clear();

        fd.close();
    }

    // ⭐ 写入全局能量信息
    void JSONTrace::writeEnergyInfo() {
        if (_energy_provider) {
            fd << ", \"current_energy_mJ\": " << (_energy_provider->getCurrentEnergy() * 1000.0);
            fd << ", \"total_consumed_mJ\": " << (_energy_provider->getTotalEnergyConsumed() * 1000.0);
            fd << ", \"total_harvested_mJ\": " << (_energy_provider->getTotalEnergyHarvested() * 1000.0);
        }
    }

    // ⭐ 写入任务能量信息
    void JSONTrace::writeTaskEnergyInfo(AbsRTTask *task) {
        if (_energy_provider && task) {
            fd << ", \"task_unit_energy_mJ\": " << (_energy_provider->getTaskUnitEnergy(task) * 1000.0);
            fd << ", \"task_total_energy_mJ\": " << (_energy_provider->getTaskTotalEnergy(task) * 1000.0);
        }
    }

    void JSONTrace::writeTaskEvent(const Task &tt,
                                   const std::string &evt_name) {
        // 检查当前时间是否超过最大时间
        if (max_time >= 0 && SIMUL.getTime() >= max_time) {
            return; // 超过最大时间，不记录此事件
        }

        if (!first_event)
            fd << "," << std::endl;
        else
            first_event = false;
        fd << "{ ";
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"" << evt_name << "\", ";
        fd << "\"task_name\": \"" << tt.getName() << "\", ";
        // ⭐ 修复：使用getLastArrival()获取当前实例的到达时间，而不是第一次实例的到达时间
        fd << "\"arrival_time\": \"" << tt.getLastArrival() << "\"";

        // ⭐ 添加能量信息
        writeEnergyInfo();

        // ⭐ 对于scheduled和end_instance事件，添加任务能量信息
        if (evt_name == "scheduled" || evt_name == "end_instance") {
            AbsRTTask *task = const_cast<AbsRTTask*>(dynamic_cast<const AbsRTTask*>(&tt));
            writeTaskEnergyInfo(task);
        }

        fd << "}";
    }

    void JSONTrace::probe(ArrEvt &e) {
        Task &tt = *(e.getTask());

        // ⭐ 修复：arrival事件应该使用当前时间作为arrival_time
        // 因为在ArrEvt触发时，task->getLastArrival()还没有更新
        if (max_time >= 0 && SIMUL.getTime() >= max_time) {
            return;
        }

        if (!first_event)
            fd << "," << std::endl;
        else
            first_event = false;
        fd << "{ ";
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"arrival\", ";
        fd << "\"task_name\": \"" << tt.getName() << "\", ";
        fd << "\"arrival_time\": \"" << SIMUL.getTime() << "\"";  // 使用当前时间

        // ⭐ 添加能量信息
        writeEnergyInfo();

        fd << "}";
    }

    void JSONTrace::probe(EndEvt &e) {
        Task &tt = *(e.getTask());
        AbsRTTask *task = dynamic_cast<AbsRTTask*>(&tt);

        // 检查当前时间是否超过最大时间
        if (max_time >= 0 && SIMUL.getTime() >= max_time) {
            return;
        }

        if (!first_event)
            fd << "," << std::endl;
        else
            first_event = false;

        fd << "{ ";
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"end_instance\", ";
        fd << "\"task_name\": \"" << tt.getName() << "\", ";
        fd << "\"arrival_time\": \"" << tt.getLastArrival() << "\"";

        // ⭐ 添加能量信息
        writeEnergyInfo();

        // ⭐ 添加任务能量信息
        if (task) {
            writeTaskEnergyInfo(task);

            // ⭐ 计算execution_time和task_consumed
            if (_task_start_times.find(task) != _task_start_times.end()) {
                MetaSim::Tick execution_time = SIMUL.getTime() - _task_start_times[task];
                fd << ", \"execution_time_ms\": " << execution_time;

                // ⭐ V115修复：task_consumed 必须是"该任务本身的消耗"
                // 正确公式：executed_time (ms) × task_unit_energy (mJ/ms)
                // 不能用 global_total_consumed 的差值（那包含了所有并行任务的消耗）
                if (_energy_provider) {
                    double unit_energy = _energy_provider->getTaskUnitEnergy(task);  // J/ms
                    double task_consumed = (double)execution_time * unit_energy;     // J
                    fd << ", \"task_consumed_mJ\": " << (task_consumed * 1000.0);
                }

                // 清除记录
                _task_start_times.erase(task);
                _task_start_consumed.erase(task);
            }
        }

        fd << "}";
    }

    void JSONTrace::probe(SchedEvt &e) {
        Task &tt = *(e.getTask());
        AbsRTTask *task = dynamic_cast<AbsRTTask*>(&tt);

        // 检查当前时间是否超过最大时间
        if (max_time >= 0 && SIMUL.getTime() >= max_time) {
            return;
        }

        if (!first_event)
            fd << "," << std::endl;
        else
            first_event = false;

        fd << "{ ";
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"scheduled\", ";
        fd << "\"task_name\": \"" << tt.getName() << "\", ";
        fd << "\"arrival_time\": \"" << tt.getLastArrival() << "\"";

        // ⭐ 添加能量信息
        writeEnergyInfo();

        // ⭐ 添加任务能量信息
        if (task) {
            writeTaskEnergyInfo(task);

            // ⭐ 添加energy_sufficient字段
            if (_energy_provider) {
                double current_energy = _energy_provider->getCurrentEnergy();
                double task_unit_energy = _energy_provider->getTaskUnitEnergy(task);
                bool energy_sufficient = (current_energy >= task_unit_energy);
                fd << ", \"energy_sufficient\": " << (energy_sufficient ? "true" : "false");
            }

            // ⭐ 记录任务开始执行的时间和累计消耗
            _task_start_times[task] = SIMUL.getTime();
            if (_energy_provider) {
                _task_start_consumed[task] = _energy_provider->getTotalEnergyConsumed();
            }
        }

        fd << "}";
    }

    void JSONTrace::probe(DeschedEvt &e) {
        Task &tt = *(e.getTask());
        AbsRTTask *task = dynamic_cast<AbsRTTask*>(&tt);

        // 检查当前时间是否超过最大时间
        if (max_time >= 0 && SIMUL.getTime() >= max_time) {
            return;
        }

        // ⭐ V83修复：跳过已经deadline miss的任务
        // 当任务已经触发deadline miss时，不应该再记录descheduled事件
        // 因为任务已经被标记为死亡，descheduled事件在逻辑上是错误的
        if (task && _deadline_missed_tasks.find(task) != _deadline_missed_tasks.end()) {
            // 从集合中移除（因为kill事件会紧随其后）
            _deadline_missed_tasks.erase(task);
            return;
        }

        if (!first_event)
            fd << "," << std::endl;
        else
            first_event = false;

        fd << "{ ";
        fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\": \"descheduled\", ";
        fd << "\"task_name\": \"" << tt.getName() << "\", ";
        fd << "\"arrival_time\": \"" << tt.getLastArrival() << "\"";

        // ⭐ 添加能量信息
        writeEnergyInfo();

        // ⭐ 添加descheduled特定信息
        if (task) {
            // 计算已执行时间
            if (_task_start_times.find(task) != _task_start_times.end()) {
                MetaSim::Tick executed_time = SIMUL.getTime() - _task_start_times[task];
                fd << ", \"executed_time_ms\": " << executed_time;

                // ⭐ 修复：基于执行时间计算部分消耗能量，而不���能量差值
                // 原因：能量差值会排除调度时预扣的初始能量（在scheduled事件中扣除的1ms）
                //       导致partial_consumed记录不准确
                // 示例：任务执行10ms，单位能耗0.42mJ/ms
                //       - 能量差值法：4.2 - 0.42 = 3.78mJ (错误！缺少1ms的能量)
                //       - 执行时间法：10 * 0.42 = 4.2mJ (正确！)
                if (_energy_provider) {
                    double task_unit_energy = _energy_provider->getTaskUnitEnergy(task);
                    double partial_consumed = double(executed_time) * task_unit_energy;
                    fd << ", \"partial_consumed_mJ\": " << (partial_consumed * 1000.0);
                }

                // 尝试获取WCET并计算剩余时间
                // 注意：这里需要从任务模型获取WCET，但我们没有直接访问权限
                // 所以这个字段可能需要调度器提供额外接口
            }

            // ⭐ V115修复：使用调度器记录的真正挂起原因，消灭"幽灵抢占"
            if (_energy_provider) {
                std::string suspend_reason = _energy_provider->getSuspendReason(task);

                if (suspend_reason == "energy_depleted" || suspend_reason == "insufficient_energy") {
                    fd << ", \"preempted_by\": \"energy_insufficient\"";
                    fd << ", \"reason\": \"insufficient_energy\"";
                } else if (suspend_reason == "preemption") {
                    fd << ", \"preempted_by\": \"higher_priority_task\"";
                    fd << ", \"reason\": \"preemption\"";
                } else {
                    // 未知原因，使用能量启发式作为后备
                    double current_energy = _energy_provider->getCurrentEnergy();
                    double task_unit_energy = _energy_provider->getTaskUnitEnergy(task);
                    double min_run_energy = task_unit_energy * 5;
                    if (current_energy < min_run_energy) {
                        fd << ", \"preempted_by\": \"energy_insufficient\"";
                        fd << ", \"reason\": \"insufficient_energy\"";
                    } else {
                        fd << ", \"preempted_by\": \"higher_priority_task\"";
                        fd << ", \"reason\": \"preemption\"";
                    }
                }

                // 清除挂起原因记录
                _energy_provider->clearSuspendReason(task);
            }

            // 清除记录（任务被下处理机）
            _task_start_times.erase(task);
            _task_start_consumed.erase(task);
        }

        fd << "}";
    }

    void JSONTrace::probe(DeadEvt &e) {
        Task &tt = *(e.getTask());
        AbsRTTask *task = dynamic_cast<AbsRTTask*>(&tt);

        // 修复假阳性deadline miss：只有当前时间 >= 绝对截止时间才记录为deadline miss
        // 这是RTSim框架的一个已知问题，在Buffered模式下会调用deadEvt.process()
        // 但这并不一定意味着真的有deadline miss
        MetaSim::Tick current_time = SIMUL.getTime();
        MetaSim::Tick arrival_time = tt.getArrival();
        // ⭐ 修复：使用getRelDline()获取相对截止时间，而不是getDeadline()（返回绝对截止时间）
        MetaSim::Tick relative_deadline = tt.getRelDline();
        MetaSim::Tick absolute_deadline = arrival_time + relative_deadline;

        // 只有当前时间真的超过截止时间时才记录
        if (current_time >= absolute_deadline) {
            // ⭐ 修复：使用当前时间作为arrival_time
            // 因为对于周期性任务，getLastArrival()可能返回第一次实例的到达时间
            if (max_time >= 0 && SIMUL.getTime() >= max_time) {
                return;
            }

            // ⭐ V83修复：将任务添加到deadline miss集合
            // 这样后续的descheduled事件会被跳过
            if (task) {
                _deadline_missed_tasks.insert(task);
            }

            if (!first_event)
                fd << "," << std::endl;
            else
                first_event = false;
            fd << "{ ";
            fd << "\"time\": \"" << SIMUL.getTime() << "\", ";
            fd << "\"event_type\": \"dline_miss\", ";
            fd << "\"task_name\": \"" << tt.getName() << "\", ";
            fd << "\"arrival_time\": \"" << current_time - relative_deadline << "\", ";  // 反推到达时间
            fd << "\"deadline\": \"" << absolute_deadline << "\", ";
            fd << "\"miss_amount\": \"" << (current_time - absolute_deadline) << "\"";

            // ⭐ 添加能量信息
            writeEnergyInfo();

            // ⭐ 添加deadline miss原因
            if (_energy_provider) {
                double current_energy = _energy_provider->getCurrentEnergy();
                if (current_energy < 0.001) {  // 能量接近0
                    fd << ", \"reason\": \"energy_depleted\"";
                } else {
                    fd << ", \"reason\": \"insufficient_time\"";
                }
            } else {
                fd << ", \"reason\": \"unknown\"";
            }

            fd << "}";
        }
        // 否则忽略这个假阳性事件
    }

    void JSONTrace::probe(KillEvt &e) {
        Task &tt = *(e.getTask());
        AbsRTTask *task = dynamic_cast<AbsRTTask*>(&tt);

        // ⭐ V109修复：在任务被kill时，清理所有相关的指针
        // 避免在JSONTrace析构时访问无效指针导致崩溃
        if (task) {
            _task_start_times.erase(task);
            _task_start_consumed.erase(task);
            _deadline_missed_tasks.erase(task);
        }

        writeTaskEvent(tt, "kill");
    }

    void JSONTrace::attachToTask(AbsRTTask &t) {
        // new Particle<ArrEvt, JSONTrace>(&t->arrEvt, this);
        // new Particle<EndEvt, JSONTrace>(&t->endEvt, this);
        // new Particle<SchedEvt, JSONTrace>(&t->schedEvt, this);
        // new Particle<DeschedEvt, JSONTrace>(&t->deschedEvt, this);
        // new Particle<DeadEvt, JSONTrace>(&t->deadEvt, this);

        Task &tt = dynamic_cast<Task &>(t);
        attach_stat(*this, tt.arrEvt);
        attach_stat(*this, tt.endEvt);
        attach_stat(*this, tt.schedEvt);
        attach_stat(*this, tt.deschedEvt);
        attach_stat(*this, tt.deadEvt);
        attach_stat(*this, tt.killEvt);
    }
} // namespace RTSim
