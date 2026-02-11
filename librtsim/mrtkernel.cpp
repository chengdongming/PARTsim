/***************************************************************************
    begin                : Thu Apr 24 15:54:58 CEST 2003
    copyright            : (C) 2003 by Giuseppe Lipari
    email                : lipari@sssup.it
 ***************************************************************************/
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/

#include <iostream>
#include <iomanip>
#include <typeinfo>

#include <rtsim/cbserver.hpp>
#include <rtsim/mrtkernel.hpp>
#include <rtsim/scheduler/scheduler.hpp>
#include <rtsim/scheduler/gpfp_cascade_scheduler.hpp>
#include <rtsim/scheduler/gpfp_asap_scheduler.hpp>
#include <rtsim/scheduler/gpfp_epp_scheduler.hpp>
#include <rtsim/scheduler/gpfp_efpp_scheduler.hpp>
#include <rtsim/scheduler/gpfp_cbpp_scheduler.hpp>
#include <rtsim/scheduler/gpfp_tie_scheduler.hpp>
#include <rtsim/scheduler/gpfp_btie_scheduler.hpp>
#include <rtsim/scheduler/gpfp_tgf_scheduler.hpp>

namespace RTSim {
    // =========================================================================
    // class BeginDispatchMultiEvt
    // =========================================================================

    BeginDispatchMultiEvt::BeginDispatchMultiEvt(MRTKernel &k, CPU &c) :
        DispatchMultiEvt(k, c, Event::_DEFAULT_PRIORITY + 10) {}

    void BeginDispatchMultiEvt::doit() {
        _kernel.onBeginDispatchMulti(this);
    }

    // =========================================================================
    // class EndDispatchMultiEvt
    // =========================================================================

    EndDispatchMultiEvt::EndDispatchMultiEvt(MRTKernel &k, CPU &c) :
        DispatchMultiEvt(k, c, Event::_DEFAULT_PRIORITY + 10) {}

    void EndDispatchMultiEvt::doit() {
        _kernel.onEndDispatchMulti(this);
    }

    // =========================================================================
    // class MRTKernel
    // =========================================================================

    // =====================================================
    // Constructors and Destructor
    // =====================================================

    static inline std::set<CPU *> createCPUSet(absCPUFactory *factory,
                                               size_t n) {
        std::set<CPU *> cpus;

        for (size_t i = 0; i < n; i++) {
            cpus.insert(factory->createCPU());
        }

        return cpus;
    }

    MRTKernel::MRTKernel(Scheduler *s, std::set<CPU *> cpus,
                         const string &name) :
        RTKernel(s, name),
        _migrationDelay(0) {
        // internalConstructor(cpus);
        for (auto c : cpus) {
            addCPU(c);
        }
        _sched->setKernel(this);
    }

    // MRTKernel::MRTKernel(Scheduler *s, std::vector<CPU *> cpus,
    //                      const string &name) :
    //     MRTKernel(s, std::set<CPU *>(cpus.begin(), cpus.end()), name) {}

    // MRTKernel::MRTKernel(Scheduler *s, absCPUFactory *factory, int n,
    //                      const string &name) :
    //     MRTKernel(s, createCPUSet(factory, n)) {}

    // // Using std::make_unique, we create a temporary unique_ptr that will
    // // automatically delete the uniformCPUFactory once done
    // MRTKernel::MRTKernel(Scheduler *s, int n, const string &name) :
    //     MRTKernel(s, std::make_unique<uniformCPUFactory>().get(), n, name) {}

    // MRTKernel::MRTKernel(Scheduler *s, const string &name) :
    //     MRTKernel(s, 1, name) {}

    /// Deletes elements pointed by maps
    template <class IT>
    static inline void clean_mapcontainer(IT b, IT e) {
        for (IT i = b; i != e; i++)
            delete i->second;
    }

    MRTKernel::~MRTKernel() {
        // delete _CPUFactory;
        clean_mapcontainer(_beginEvt.begin(), _beginEvt.end());
        clean_mapcontainer(_endEvt.begin(), _endEvt.end());
    }

    // =====================================================
    // Methods
    // =====================================================

    CPU *MRTKernel::getFreeProcessor() {
        for (auto it : _m_currExe) {
            if (it.second == nullptr)
                return it.first;
        }
        return nullptr;
    }

    bool MRTKernel::isDispatched(CPU *p) const {
        for (auto it : _m_dispatched) {
            if (it.second == p)
                return true;
        }
        return false;
    }

    MRTKernel::ITCPU MRTKernel::getNextFreeProc(ITCPU begin, ITCPU end) {
        std::cout << "[DEBUG] getNextFreeProc() - 开始查找空闲CPU" << std::endl;
        for (auto it = begin; it != end; ++it) {
            bool curr_exe_null = (it->second == nullptr);
            bool is_disp = isDispatched(it->first);
            std::cout << "[DEBUG] getNextFreeProc() - CPU: " << it->first->toString()
                      << " _m_currExe=null:" << curr_exe_null
                      << " isDispatched:" << is_disp << std::endl;
            if (curr_exe_null && !is_disp) {
                std::cout << "[DEBUG] getNextFreeProc() - 找到空闲CPU: " << it->first->toString() << std::endl;
                return it;
            }
        }
        std::cout << "[DEBUG] getNextFreeProc() - 未找到空闲CPU" << std::endl;
        return end;
    }

    void MRTKernel::addCPU(CPU *c) {
        DBGENTER(_KERNEL_DBG_LEV);

        _m_currExe[c] = nullptr;
        _isContextSwitching[c] = false;
        _beginEvt[c] = new BeginDispatchMultiEvt(*this, *c);
        _endEvt[c] = new EndDispatchMultiEvt(*this, *c);

        c->setKernel(this);
    }

    void MRTKernel::addTask(AbsRTTask &t, const string &param) {
        RTKernel::addTask(t, param);
        _m_oldExe[&t] = nullptr;
        _m_dispatched[&t] = nullptr;

        CBServer *cbs = dynamic_cast<CBServer *>(&t);
        if (cbs != nullptr)
            _servers.push_back(cbs);
    }

    void MRTKernel::onArrival(AbsRTTask *task) {
        DBGENTER(_KERNEL_DBG_LEV);

        std::cout << "[DEBUG] MRTKernel::onArrival() - 开始: " << taskname(task)
                  << " 当前时间: " << SIMUL.getTime() << "ms" << std::endl;

        // 🔒 V28.10修复：在任务到达时先收集能量
        // 这样即使初始能量为0，任务到达后也能先收集太阳能，然后再调度
        // ⭐ V28.12扩展：为CASCADE和ASAP调度器都启用能量收集
        MetaSim::Tick current_time = SIMUL.getTime();

        GPFPCASCADEScheduler *cascade_sched = dynamic_cast<GPFPCASCADEScheduler*>(_sched);
        GPFPASAPScheduler *asap_sched = dynamic_cast<GPFPASAPScheduler*>(_sched);

        if (cascade_sched) {
            double harvested = cascade_sched->updateEnergyContinuously(static_cast<TimeMs>(current_time));
            if (harvested > 0.001) {
                std::cout << "[DEBUG] onArrival()收集能量(CASCADE): " << harvested << "J @ "
                          << current_time << "ms" << std::endl;
            }
        } else if (asap_sched) {
            double harvested = asap_sched->updateEnergyContinuously(static_cast<TimeMs>(current_time));
            if (harvested > 0.001) {
                std::cout << "[DEBUG] onArrival()收集能量(ASAP): " << harvested << "J @ "
                          << current_time << "ms" << std::endl;
            }
        }

        _sched->insert(task);
        dispatch();

        std::cout << "[DEBUG] MRTKernel::onArrival() - 完成: " << taskname(task) << std::endl;
    }

    void MRTKernel::suspend(AbsRTTask *task) {
        DBGENTER(_MRTKERNEL_DBG_LEV);

        _sched->extract(task);
        CPU *p = getProcessor(task);
        if (p != nullptr) {
            task->deschedule();

            _m_currExe[p] = nullptr;
            _m_oldExe[task] = p;
            _m_dispatched[task] = nullptr;

            // ⭐ BUG修复（2026-01-24）：suspend不应该调用onTaskEnd()
            // onTaskEnd()会永久移除任务、清理能量账户、增加完成计数
            // 对于能量不足的中断，任务应该保留剩余执行时间，等待能量恢复后继续执行
            // 正确做法：将任务重新插入到就绪队列，而不是终止它
            // _sched->onTaskEnd(task);  // ❌ 错误：这会终止任务实例

            // ✅ 修复：将任务重新插入到就绪队列
            // 这样任务会保留剩余执行时间，等待能量恢复后继续执行
            _sched->insert(task);

            std::cout << "[DEBUG] MRTKernel::suspend() - 任务已重新插入队列: "
                      << taskname(task) << " (剩余执行时间保留)" << std::endl;

            dispatch(p);
        }
    }

    void MRTKernel::onEnd(AbsRTTask *task) {
        DBGENTER(_KERNEL_DBG_LEV);

        CPU *p = getProcessor(task);

        if (p == nullptr)
            throw RTKernelExc("Received a onEnd of a non executing task");

        _sched->extract(task);
        _m_oldExe[task] = p;
        _m_currExe[p] = nullptr;
        _m_dispatched[task] = nullptr;

        // ⭐ 通用扩展：调用scheduler的onTaskEnd()虚函数
        // 这样支持带等待队列的调度器（如CASCADE）在任务结束时检查等待队列
        _sched->onTaskEnd(task);


        dispatch(p);
    }

    void MRTKernel::dispatch(CPU *p) {
        DBGENTER(_KERNEL_DBG_LEV);

        if (p == nullptr)
            throw RTKernelExc("Dispatch with NULL parameter");
        DBGPRINT("dispatching on processor ", p);

        // Undo any previous "begin dispatch event" existing on this CPU
        _beginEvt[p]->drop();

        if (_isContextSwitching[p]) {
            DBGPRINT("Context switch is disabled!");

            // Shifting forward the dispatch time on this cpu until the current
            // context switch (event) is done
            _beginEvt[p]->post(_endEvt[p]->getTime());

            // The previous context switch is canceled (the time it took to run
            // will still be accounted though)
            AbsRTTask *task = _endEvt[p]->getTask();
            _endEvt[p]->drop();
            if (task != nullptr) {
                _endEvt[p]->setTask(nullptr);
                _m_dispatched[task] = nullptr;
            }
        } else {
            // Perform the dispatch now (see onBeginDispatchMulti)
            std::cout << "[DEBUG] dispatch(CPU) - posting BeginDispatchMultiEvt for CPU " << p->toString() << std::endl;
            _beginEvt[p]->post(SIMUL.getTime());
        }
    }

    void MRTKernel::dispatch() {
        DBGENTER(_KERNEL_DBG_LEV);

        // ⭐ V30修复：记录dispatch()调用开始时间，确保同一tick的所有任务记录相同的时间
        _dispatch_start_time = SIMUL.getTime();

        std::cout << "[DEBUG] MRTKernel::dispatch() - CALLED! _dispatch_start_time=" << _dispatch_start_time << std::endl;
        size_t ncpu = _m_currExe.size();

        // Tells us how many of the first ncpu tasks in the ready queue are not
        // yet scheduled or dispatched for scheduling.
        int num_newtasks = 0;

        // Check whether the first ncpu tasks in the ready queue are already
        // dispatched or not.
        std::cout << "[DEBUG] MRTKernel::dispatch() - 计算num_newtasks, ncpu=" << ncpu << std::endl;
        for (size_t i = 0; i < ncpu; ++i) {
            AbsRTTask *t = _sched->getTaskN(i);
            if (t == nullptr) {
                std::cout << "[DEBUG]   getTaskN(" << i << ") = nullptr, 退出循环" << std::endl;
                break;
            }
            CPU *proc = getProcessor(t);
            CPU *disp = _m_dispatched[t];
            bool is_new = (proc == nullptr && disp == nullptr);
            std::cout << "[DEBUG]   getTaskN(" << i << ")=" << taskname(t)
                      << " getProcessor=" << (proc ? proc->toString() : "nullptr")
                      << " _m_dispatched=" << (disp ? disp->toString() : "nullptr")
                      << " is_new=" << is_new << std::endl;
            if (is_new)
                ++num_newtasks;
        }
        std::cout << "[DEBUG] MRTKernel::dispatch() - 循环结束, num_newtasks=" << num_newtasks << std::endl;

        // Technically, in the old impl i can be less than ncpu, but if we break
        // before reaching ncpu for sure there are NO tasks to evict and i will
        // never be used.
        int i = ncpu;

        DBGPRINT(_sched->toString());
        DBGPRINT("New tasks: ", num_newtasks);
        print();
        std::cout << "[DEBUG] MRTKernel::dispatch() - num_newtasks=" << num_newtasks << std::endl;

        if (num_newtasks < 1) {
            std::cout << "[DEBUG] MRTKernel::dispatch() - num_newtasks < 1，返回" << std::endl;
            return;
        }

        for (auto f = getNextFreeProc(_m_currExe.begin(), _m_currExe.end());
             num_newtasks > 0; f = getNextFreeProc(f, _m_currExe.end())) {
            if (f != _m_currExe.end()) {
                DBGPRINT("Dispatching on free processor ", f->first);
                dispatch(f->first);
                --num_newtasks;
                ++f;
            } else {
                // We have to "evict" a task from being scheduled/dispatched
                // because there are no more CPUs and a task that is "higher" in
                // the ready queue has to run on its CPU.

                // ORIGINAL NOTE FROM TOMMASO:
                // NON-SENSE: this is putting all new tasks on WHAT CPU ?!?

                // NOTE: this loop takes a long while to complete
                // ⭐ V28.13修复：允许只调度部分任务，不填满所有CPU
                // 如果找不到可以驱逐的任务，就停止调度
                for (int max_attempts = 100; max_attempts > 0; --max_attempts) {
                    AbsRTTask *t = _sched->getTaskN(i++);
                    if (t == nullptr) {
                        // 没有更多任务可以驱逐，停止调度
                        DBGPRINT("No more tasks to deschedule, stopping dispatch");
                        std::cout << "[DEBUG] MRTKernel::dispatch() - 没有更多任务可驱逐，停止调度 (num_newtasks=" << num_newtasks << ")" << std::endl;
                        num_newtasks = 0;  // 清零，退出外层循环
                        break;
                    }

                    // NOTE: does not check for running tasks, only dispatched
                    // ones!
                    CPU *c = _m_dispatched[t];
                    if (c != nullptr) {
                        DBGPRINT("Dispatching on processor ", c,
                                 " which is executing task ", taskname(t));
                        dispatch(c);
                        --num_newtasks;
                        break;
                    }
                }
            }
        }
    }

    void MRTKernel::onBeginDispatchMulti(BeginDispatchMultiEvt *e) {
        DBGENTER(_KERNEL_DBG_LEV);

        // if necessary, deschedule the task.
        CPU *p = e->getCPU();
        AbsRTTask *dt = _m_currExe[p];
        AbsRTTask *st = nullptr;

        if (dt != nullptr) {
            _m_oldExe[dt] = p;
            _m_currExe[p] = nullptr;
            _m_dispatched[dt] = nullptr;
            dt->deschedule();
        }

        // select the first non dispatched task in the queue
        int i = 0;
        std::cout << "[DEBUG] onBeginDispatchMulti - 开始调用getTaskN查找任务" << std::endl;
        while ((st = _sched->getTaskN(i)) != nullptr) {
            std::cout << "[DEBUG] onBeginDispatchMulti - getTaskN(" << i << ") = " << taskname(st) << " _m_dispatched=" << _m_dispatched[st] << std::endl;
            if (_m_dispatched[st] == nullptr)
                break;
            else
                i++;
        }

        if (st == nullptr) {
            DBGPRINT("Nothing to schedule, finishing");
            // ⭐ V30修复：没有任务可调度时，不设置事件，直接返回
            std::cout << "[DEBUG] onBeginDispatchMulti - st is nullptr, returning early at time " << SIMUL.getTime() << std::endl;
            return;
        }

        DBGPRINT("Scheduling task ", taskname(st), " on cpu ", p->toString());

        if (st) {
            // 🔒 V28.9修复：只预检查能量，不预消耗
            // 预消耗会导致_local_energy和_EnergyBridge不同步
            GPFPCASCADEScheduler *cascade_sched = dynamic_cast<GPFPCASCADEScheduler*>(_sched);
            EFPFPScheduler *efpp_sched = dynamic_cast<EFPFPScheduler*>(_sched);
            CBPPScheduler *cbpp_sched = dynamic_cast<CBPPScheduler*>(_sched);
            Task *task = dynamic_cast<Task*>(st);

            // ⭐ 支持CBPP调度器：使用其批量调度机制
            if (cbpp_sched && task) {
                // CBPP的批量调度逻辑在getTaskN()中实现
                // 已经进行了能量预扣减和批量决策
                std::cout << "[DEBUG] onBeginDispatchMulti - 使用CBPP调度器: " << task->getName() << std::endl;

                // 不需要额外能量检查，CBPP已经在getTaskN()中完成了能量判断
            } else if (cascade_sched && task) {
                // CASCADE的能量预检查
                double unit_energy = cascade_sched->getUnitTimeEnergy(st);
                double current_energy = cascade_sched->getCurrentEnergy();

                std::cout << "[DEBUG] onBeginDispatchMulti - CASCADE能量预检查: " << task->getName()
                          << " 需要: " << std::fixed << std::setprecision(10) << unit_energy << "J"
                          << " 当前: " << current_energy << "J" << std::endl;

                if (current_energy < unit_energy - 1e-6) {
                    // 能量不足，不调度这个任务
                    std::cout << "[DEBUG] onBeginDispatchMulti - CASCADE能量不足，跳过: " << task->getName() << std::endl;
                    DBGPRINT("Energy insufficient in onBeginDispatchMulti, skipping: ", taskname(st));
                    // 从队列中移除
                    _sched->extract(st);
                    // 不设置事件，直接返回
                    return;
                }
                // 能量足够，继续dispatch流程
            }
            // EPP/EFPP等其他调度器直接调度，不在这里检查能量
            // 因为它们的能量判断已经在getTaskN()中完成

            _m_dispatched[st] = p;
        }
        _endEvt[p]->setTask(st);
        _isContextSwitching[p] = true;

        // ⭐ V30关键修复：使用_dispatch_start_time作为调度时间，确保同一tick的所有任务使用相同时间
        // _dispatch_start_time在dispatch()开始时设置，在整个tick期间保持不变
        // ⭐ V31修复：防止时间倒流 - 如果当前时间已超过_dispatch_start_time，使用当前时间
        Tick overhead(_contextSwitchDelay);
        if (st != nullptr && _m_oldExe[st] != p && _m_oldExe[st] != nullptr)
            overhead += _migrationDelay;

        Tick current_time = SIMUL.getTime();
        Tick base_time = (current_time > _dispatch_start_time) ? current_time : _dispatch_start_time;
        Tick post_time = base_time + overhead;
        _endEvt[p]->post(post_time);
        std::cout << "[DEBUG] onBeginDispatchMulti - 设置事件: task=" << taskname(st)
                  << " CPU=" << p->toString()
                  << " post_time=" << post_time << " (_dispatch_start_time=" << _dispatch_start_time << ")" << std::endl;
    }

    void MRTKernel::onEndDispatchMulti(EndDispatchMultiEvt *e) {
        // performs the "real" context switch
        DBGENTER(_KERNEL_DBG_LEV);

        AbsRTTask *st = e->getTask();
        CPU *p = e->getCPU();

        _m_currExe[p] = st;

        DBGPRINT("CPU: ", p->toString());
        DBGPRINT("Task: ", taskname(st));
        printState();

        // st could be null (because of an idling processor)
        if (st) {
            // 添加调试输出
            Task* task = dynamic_cast<Task*>(st);
            if (task) {
                std::cout << "[DEBUG] MRTKernel::onEndDispatchMulti() - 任务状态: " << task->getName()
                          << " 状态: " << task->getState() << std::endl;
            }

            // 🔒 V28.9修复：在schedule()之前预检查能量，避免记录虚假的scheduled事件
            // ⭐ V28.10修复：扩展到GPFPASAPScheduler
            // ⭐ V28.11修复：EPP/EFPP/CBPP调度器已在getTaskN中预扣能量，kernel不再重复检查
            GPFPCASCADEScheduler *cascade_sched = dynamic_cast<GPFPCASCADEScheduler*>(_sched);
            GPFPASAPScheduler *asap_sched = dynamic_cast<GPFPASAPScheduler*>(_sched);
            EPPScheduler *epp_sched = dynamic_cast<EPPScheduler*>(_sched);
            EFPFPScheduler *efpp_sched = dynamic_cast<EFPFPScheduler*>(_sched);
            CBPPScheduler *cbpp_sched = dynamic_cast<CBPPScheduler*>(_sched);
            std::cout << "[DEBUG] _sched类型: " << typeid(*_sched).name() << std::endl;
            std::cout << "[DEBUG] cascade_sched指针: " << cascade_sched << " asap_sched指针: " << asap_sched
                      << " epp_sched指针: " << epp_sched << " efpp_sched指针: " << efpp_sched
                      << " cbpp_sched指针: " << cbpp_sched << std::endl;

            double unit_energy = 0.0;
            double current_energy = 0.0;
            bool check_energy = false;

            if (cascade_sched && task) {
                unit_energy = cascade_sched->getUnitTimeEnergy(st);
                current_energy = cascade_sched->getCurrentEnergy();
                check_energy = true;
            } else if (asap_sched && task) {
                unit_energy = asap_sched->getUnitTimeEnergy(st);
                current_energy = asap_sched->getCurrentEnergy();
                check_energy = true;

                // ⭐ V28.10新增：ASAP调度器在能量紧张时的idle任务检查
                double initial_energy = asap_sched->getInitialEnergy();
                double energy_ratio = (initial_energy > 1e-9) ? (current_energy / initial_energy) : 1.0;
                double energy_critical_threshold = 0.2;  // 能量紧张阈值：低于20%
                bool is_energy_critical = (energy_ratio < energy_critical_threshold);

                if (is_energy_critical) {
                    std::cout << "[DEBUG] ASAP能量紧张检查: " << task->getName()
                              << " 能量比例: " << (energy_ratio * 100) << "%" << std::endl;

                    // 获取工作负载类型
                    auto workload_it = asap_sched->getTaskWorkloads().find(st);
                    std::string workload = (workload_it != asap_sched->getTaskWorkloads().end())
                                              ? workload_it->second
                                              : "control";
                    bool is_idle_task = (workload.find("idle") != std::string::npos);

                    std::cout << "[DEBUG] ASAP工作负载检查: " << task->getName()
                              << " 工作负载: " << workload
                              << " 是否idle: " << (is_idle_task ? "是" : "否") << std::endl;

                    // ⭐ 阻止idle任务在能量紧张时调度
                    if (is_idle_task) {
                        std::cout << "[DEBUG] ASAP能量紧张，阻止idle任务调度: " << task->getName()
                                  << " 工作负载: " << workload
                                  << " 能量比例: " << (energy_ratio * 100) << "%" << std::endl;
                        DBGPRINT("Energy critical, blocking idle task: ", taskname(st));
                        _sched->extract(st);
                        _m_currExe[p] = nullptr;
                        _isContextSwitching[p] = false;
                        return;
                    }
                }
            }

            if (check_energy) {
                std::cout << "[DEBUG] MRTKernel::onEndDispatchMulti() - 能量预检查: " << task->getName()
                          << " 需要: " << std::fixed << std::setprecision(10) << unit_energy << "J"
                          << " 当前: " << std::fixed << std::setprecision(10) << current_energy << "J" << std::endl;

                if (current_energy < unit_energy - 1e-9) {  // 🔒 V28.9修复：使用epsilon避免浮点数精度问题
                    // 能量不足，不调用schedule()，避免记录scheduled事件
                    std::cout << "[DEBUG] MRTKernel::onEndDispatchMulti() - 能量不足判定!"
                              << " current_energy(" << current_energy << ") < unit_energy(" << unit_energy << ")" << std::endl;
                    std::cout << "[DEBUG] MRTKernel::onEndDispatchMulti() - 能量不足，跳过schedule(): " << task->getName() << std::endl;
                    DBGPRINT("Energy insufficient, skipping schedule(): ", taskname(st));
                    // 从队列中移除任务
                    _sched->extract(st);
                    // 将_m_currExe[p]设置为null，避免后续问题
                    _m_currExe[p] = nullptr;
                    _isContextSwitching[p] = false;
                    return;
                } else {
                    std::cout << "[DEBUG] MRTKernel::onEndDispatchMulti() - 能量充足，允许调度: " << task->getName() << std::endl;
                }
            } else if ((epp_sched || efpp_sched || cbpp_sched) && task) {
                // ⭐ V28.11修复：EPP/EFPP/CBPP调度器已在getTaskN中预扣能量，跳过kernel的重复检查
                std::cout << "[DEBUG] EPP/EFPP/CBPP调度器检测到，跳过kernel能量检查（已在调度器中预扣）: " << task->getName() << std::endl;
                // 不设置check_energy，直接继续到schedule()
            } else {
                std::cout << "[DEBUG] cascade_sched和asap_sched都为nullptr或task为nullptr，跳过能量预检查" << std::endl;
            }

            // 重要修复：检查任务是否真的应该被调度
            // 如果任务不在就绪或执行状态，不调用schedule()，避免记录错误的调度事件
            // 这样可以解决能量不足时仍然记录调度事件的问题
            if (task) {
                std::cout << "[DEBUG] 任务状态检查: " << task->getName()
                          << " 状态: " << task->getState()
                          << " (TSK_READY=" << TSK_READY << " TSK_EXEC=" << TSK_EXEC << ")" << std::endl;
            }

            if (task && (task->getState() == TSK_READY || task->getState() == TSK_EXEC)) {
                // 任务在就绪或执行状态，可以调度
                std::cout << "[DEBUG] 调用schedule(): " << task->getName() << std::endl;
                st->schedule();
                DBGPRINT("Task scheduled: ", taskname(st));

                // ⭐ V28.15新增：为TIE/BTIE/TGF调度器启动运行时能量检查
                // ⭐ V40重构：TIE/TGF已移除能量检查事件，能量在performTickScheduling中处理
                TIEScheduler *tie_sched = dynamic_cast<TIEScheduler*>(_sched);
                BTIEScheduler *btie_sched = dynamic_cast<BTIEScheduler*>(_sched);
                TGFScheduler *tgf_sched = dynamic_cast<TGFScheduler*>(_sched);

                if (tie_sched) {
                    // ❌ V40重构：TIE能量检查事件已移除，能量由performTickScheduling处理
                    // tie_sched->startEnergyCheckForTask(st, p);
                } else if (btie_sched) {
                    btie_sched->startEnergyCheckForTask(st, p);
                } else if (tgf_sched) {
                    // ❌ V40重构：TGF能量检查事件已移除，能量由performTickScheduling处理
                    // tgf_sched->startEnergyCheckForTask(st, p);
                }
            } else {
                // 任务不在就绪或执行状态，可能能量不足或已完成
                // 不调用schedule()，避免记录错误的调度事件
                std::cout << "[DEBUG] 跳过schedule()，任务状态不对: " << (task ? task->getName() : "nullptr") << std::endl;
                DBGPRINT("Task not in READY or EXEC state, skipping schedule(): ", taskname(st));
                // 将_m_currExe[p]设置为null，避免后续问题
                _m_currExe[p] = nullptr;
                // ⭐ 修复：清理_m_dispatched，避免后续周期调度失败
                if (st) {
                    _m_dispatched[st] = nullptr;
                    std::cout << "[DEBUG] 清理_m_dispatched: " << taskname(st) << std::endl;
                }
            }
        }

        _isContextSwitching[p] = false;
        _sched->notify(st);
    }

    CPU *MRTKernel::getProcessor(const AbsRTTask *t) const {
        DBGENTER(_KERNEL_DBG_LEV);
        CPU *ret = nullptr;

        for (auto i = _m_currExe.cbegin(); i != _m_currExe.cend(); i++)
            if (i->second == t)
                ret = i->first;
        return ret;
    }

    CPU *MRTKernel::getOldProcessor(const AbsRTTask *t) const {
        CPU *ret = nullptr;

        DBGENTER(_KERNEL_DBG_LEV);

        auto it = _m_oldExe.find(t);
        if (it != _m_oldExe.cend())
            ret = it->second;

        return ret;
    }

    // std::vector<CPU *> MRTKernel::getProcessors() const {
    //     std::vector<CPU *> s(_m_currExe.size());
    //     int j = 0;
    //     for (auto i = _m_currExe.cbegin(); i != _m_currExe.cend(); i++, j++)
    //         s[j] = i->first;
    //     return s;
    // }

    void MRTKernel::newRun() {
        for (auto i = _m_currExe.begin(); i != _m_currExe.end(); i++) {
            if (i->second != nullptr)
                _sched->extract(i->second);
            i->second = nullptr;
        }

        for (auto j = _m_dispatched.begin(); j != _m_dispatched.end(); ++j) {
            j->second = nullptr;
        }

        for (auto j = _m_oldExe.begin(); j != _m_oldExe.end(); ++j) {
            j->second = nullptr;
        }
    }

    void MRTKernel::endRun() {
        for (auto i = _m_currExe.begin(); i != _m_currExe.end(); i++) {
            if (i->second != nullptr)
                _sched->extract(i->second);
            i->second = nullptr;
        }
    }

    void MRTKernel::print() const {
        DBGPRINT("Executing");
        for (auto i = _m_currExe.cbegin(); i != _m_currExe.cend(); ++i)
            DBGPRINT("  [", i->first, "] --> ", taskname(i->second));

        DBGPRINT("Dispatched");
        for (auto j = _m_dispatched.cbegin(); j != _m_dispatched.cend(); ++j)
            DBGPRINT("  [", taskname(j->first), "] --> ", j->second);
    }

    void MRTKernel::printState() const {
        Entity *task;
        std::cout << "MRTKernel::printstate(), time " << SIMUL.getTime() << " ";
        for (auto i = _m_currExe.cbegin(); i != _m_currExe.cend(); i++) {
            task = dynamic_cast<Entity *>(i->second);
            if (task != nullptr)
                std::cout << i->first->getName() << " : " << task->getName()
                          << "   ";
            else
                std::cout << i->first->getName() << " :   0   ";
        }
        std::cout << std::endl;
    }

    AbsRTTask *MRTKernel::getTask(const CPU *c) {
        // Not the cleanest solution, but the comparison operator doesn't care
        // about the pointd element anyway
        return _m_currExe[const_cast<CPU *>(c)];
    }

    std::vector<std::string> MRTKernel::getRunningTasks() {
        std::vector<std::string> tmp_ts;
        for (auto i = _m_currExe.cbegin(); i != _m_currExe.cend(); i++) {
            std::string tmp_name = taskname((*i).second);
            if (tmp_name != "(nil)")
                tmp_ts.push_back(tmp_name);
        }
        return tmp_ts;
    }

} // namespace RTSim
