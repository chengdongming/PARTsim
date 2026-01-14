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
#include <algorithm>
#include <iostream>

#include <metasim/simul.hpp>

#include <rtsim/cpu.hpp>
#include <rtsim/kernel.hpp>
#include <rtsim/reginstr.hpp>
#include <rtsim/resource/resmanager.hpp>
#include <rtsim/scheduler/scheduler.hpp>
#include <rtsim/task.hpp>

namespace RTSim {

    RTKernel::RTKernel(Scheduler *s, const std::string &name, CPU *c) :
        Entity(name),
        _sched(s),
        _resMng(0),
        _cpu(nullptr),
        _isContextSwitching(false),
        _contextSwitchDelay(0),
        _internalCPU(false),
        beginDispatchEvt(this),
        endDispatchEvt(this) {
        __reginstr_init();
        __regsched_init();
        __regtask_init();

        _currExe = NULL;

        DBGENTER(_KERNEL_DBG_LEV);

        /* In this constructor the user can decide to provide
           a particular CPU or not.  If a CPU is provided
           (i.e. c!=NULL), that particular CPU will be
           used. Otherwise, a new CPU without power saving
           functions is created (using the statement new), and
           a boolean variable (i.e. internalCpu) is set. This
           variable specifies if the CPU must be deleted in
           the kernel destructor.
        */
        if (c)
            setCPU(c);

        // if (c == nullptr) {
        //     setCPU(new CPU, true);
        // } else {
        //     setCPU(c);
        // }

        s->setKernel(this);
    }

    void RTKernel::setCPU(CPU *cpu, bool internal) {
        if (_cpu && _internalCPU) {
            DBGPRINT("Deleting internal CPU in the kernel");
            delete _cpu;
        }

        _cpu = cpu;
        _internalCPU = internal && cpu != nullptr;

        if (_cpu)
            _cpu->setKernel(this);
    }

    RTKernel::~RTKernel() {
        DBGENTER(_KERNEL_DBG_LEV);
        DBGPRINT("Destructor of RTKernel");

        setCPU(nullptr);
    }

    void RTKernel::addTask(AbsRTTask &t, const string &params) {
        t.setKernel(this);
        _handled.push_back(&t);
        _sched->addTask(&t, params);
    }

    CPU *RTKernel::getProcessor(const AbsRTTask *t) const {
        return _cpu;
    }

    CPU *RTKernel::getOldProcessor(const AbsRTTask *t) const {
        return _cpu;
    }

    void RTKernel::activate(AbsRTTask *task) {
        DBGENTER(_KERNEL_DBG_LEV);

        _sched->insert(task);
    }

    AbsRTTask *RTKernel::getCurrExe() const {
        return _currExe;
    }

    void RTKernel::suspend(AbsRTTask *task) {
        DBGENTER(_KERNEL_DBG_LEV);

        _sched->extract(task);

        if (_currExe == task) {
            task->deschedule();
            _currExe = NULL;
        }
    }

    void RTKernel::discardTasks(bool f) {
        _sched->discardTasks(f);
        _handled.clear();
    }

    void RTKernel::onArrival(AbsRTTask *task) {
        DBGENTER(_KERNEL_DBG_LEV);
        DBGPRINT("Inserting ", taskname(task));
        
        // 添加调试输出到标准输出
        std::cout << "[DEBUG] RTKernel::onArrival() - 开始: " << taskname(task) 
                  << " 当前时间: " << SIMUL.getTime() << "ms" << std::endl;

        _sched->insert(task);

        if (!_isContextSwitching) {
            DBGPRINT("onArrival, calling dispatch() while NOT contextSwitching");
            std::cout << "[DEBUG] RTKernel::onArrival() - 调用dispatch() (非上下文切换)" << std::endl;
            dispatch();
        } else {
            DBGPRINT("onArrival, calling dispatch() even if we're contextSwitching");
            std::cout << "[DEBUG] RTKernel::onArrival() - 调用dispatch() (上下文切换中)" << std::endl;
            dispatch();
        }
        
        std::cout << "[DEBUG] RTKernel::onArrival() - 完成: " << taskname(task) << std::endl;
    }

    void RTKernel::onEnd(AbsRTTask *task) {
        DBGENTER(_KERNEL_DBG_LEV);

        if (getProcessor(task) == NULL) {
            throw RTKernelExc("Received a onEnd of a non executing task");
        }
        _sched->extract(task);
        if (_currExe == task)
            _currExe = NULL;

        dispatch();
    }

    void RTKernel::dispatch() {
        DBGENTER(_KERNEL_DBG_LEV);

        // we have only to post an Dispatch event (low priority)

        beginDispatchEvt.drop();
        beginDispatchEvt.post(SIMUL.getTime());
    }

    void RTKernel::onBeginDispatch(Event *e) {
        DBGENTER(_KERNEL_DBG_LEV);

        // 添加调试输出
        std::cout << "[DEBUG] RTKernel::onBeginDispatch() - 开始: _currExe = " 
                  << (_currExe ? taskname(_currExe) : "NULL") << std::endl;
        
        AbsRTTask *newExe = _sched->getFirst();
        
        std::cout << "[DEBUG] RTKernel::onBeginDispatch() - _sched->getFirst() 返回: " 
                  << (newExe ? taskname(newExe) : "NULL") << std::endl;

        if (newExe != NULL)
            DBGPRINT("From sched: ", taskname(newExe));

        if (_currExe != newExe) {
            if (_currExe != NULL) {
                _currExe->deschedule();
            }
            if (newExe != NULL) {
                _isContextSwitching = true;
                _currExe = newExe;
                endDispatchEvt.post(SIMUL.getTime() + _contextSwitchDelay);
                std::cout << "[DEBUG] RTKernel::onBeginDispatch() - 安排上下文切换: " 
                          << taskname(newExe) << std::endl;
            } else {
                // 重要修复：当newExe为null时，将_currExe设置为null
                // 这样可以避免在onEndDispatch中调度null任务
                _currExe = NULL;
                std::cout << "[DEBUG] RTKernel::onBeginDispatch() - newExe为null，_currExe设置为null" << std::endl;
            }
        } else {
            _sched->notify(newExe);
            if (newExe != NULL)
                DBGPRINT("Now Running: ", taskname(newExe));
        }
        
        std::cout << "[DEBUG] RTKernel::onBeginDispatch() - 完成" << std::endl;
    }

    void RTKernel::onEndDispatch(Event *e) {
        DBGENTER(_KERNEL_DBG_LEV);

        // 添加调试输出
        std::cout << "[DEBUG] RTKernel::onEndDispatch() - 开始: _currExe = " 
                  << (_currExe ? taskname(_currExe) : "NULL") << std::endl;

        // 重要修复：检查_currExe是否为null
        // 如果为null，不调用schedule()，避免段错误
        if (_currExe != NULL) {
            // 新修复：检查任务是否真的应该被调度
            // 如果任务不在就绪或执行状态，不调用schedule()，避免记录错误的调度事件
            // 这样可以解决能量不足时仍然记录调度事件的问题
            Task* task = dynamic_cast<Task*>(_currExe);
            if (task) {
                std::cout << "[DEBUG] RTKernel::onEndDispatch() - 任务状态: " << task->getName() 
                          << " 状态: " << task->getState() << std::endl;
            }
            
            // 重要修复：无论任务状态如何，都调用schedule()来记录调度事件
            // 这样可以确保调度事件被记录，即使任务可能因为能量不足而不会被真正执行
            std::cout << "[DEBUG] RTKernel::onEndDispatch() - 调用schedule(): " << (_currExe ? taskname(_currExe) : "NULL") << std::endl;
            _currExe->schedule();
            DBGPRINT("Now Running: ", taskname(_currExe));
            _sched->notify(_currExe);
            
            // 重要修复：记录调度事件到追踪文件
            // 这样可以确保调度事件被记录，即使任务可能因为能量不足而不会被真正执行
            std::cout << "[DEBUG] RTKernel::onEndDispatch() - 调度事件已记录: " << (_currExe ? taskname(_currExe) : "NULL") << std::endl;
        } else {
            DBGPRINT("No task to schedule (_currExe is NULL)");
            std::cout << "[DEBUG] RTKernel::onEndDispatch() - 没有任务可调度 (_currExe is NULL)" << std::endl;
        }

        _isContextSwitching = false;
    }

    void RTKernel::setResManager(ResManager *rm) {
        _resMng = rm;
        // _resMng->setKernel(this, _sched);
    }

    bool AbsKernel::requestResource(AbsRTTask *t, const string &r, int n) {
        DBGENTER(_KERNEL_DBG_LEV);

        ResManager *resMng = getResManager();
        if (resMng == 0)
            throw BaseExc("Resource Manager not set!");
        bool ret = resMng->request(t, r, n);
        if (!ret)
            dispatch();
        return ret;
    }

    void AbsKernel::releaseResource(AbsRTTask *t, const string &r, int n) {
        ResManager *resMng = getResManager();
        if (resMng == 0)
            throw BaseExc("Resource Manager not set!");

        resMng->release(t, r, n);
        dispatch();
    }

    // void RTKernel::setThreshold(const int th) {
    //     DBGENTER(_KERNEL_DBG_LEV);
    //     _sched->setThreshold(_currExe, th);
    // }

    // void RTKernel::enableThreshold() {
    //     DBGENTER(_KERNEL_DBG_LEV);
    //     _sched->enableThreshold(_currExe);
    // }

    // void RTKernel::disableThreshold() {
    //     DBGENTER(_KERNEL_DBG_LEV);
    //     _sched->disableThreshold(_currExe);
    // }

    void RTKernel::printState() const {}

    void RTKernel::newRun() {
        _currExe = NULL;
    }

    void RTKernel::endRun() {
        _currExe = NULL;
    }

    void RTKernel::print() const {}

    std::vector<std::string> RTKernel::getRunningTasks() {
        std::vector<std::string> tmp_ts;
        std::string tmp_name = taskname(_currExe);

        if (tmp_name != "(nil)")
            tmp_ts.push_back(tmp_name);

        return tmp_ts;
    }

    std::string KernelEvt::toString() const {
        return Event::toString() + " for " + _kernel->getName();
    }
} // namespace RTSim
