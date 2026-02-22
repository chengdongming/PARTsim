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
#include <cstdlib>
#include <cstring>

#include <metasim/factory.hpp>
#include <metasim/regvar.hpp>
#include <metasim/simul.hpp>
#include <metasim/strtoken.hpp>

#include <rtsim/abskernel.hpp>
#include <rtsim/instr.hpp>
#include <rtsim/task.hpp>
#include <rtsim/suspend_instr.hpp>

#include <rtsim/utils.hpp>
#include <rtsim/scheduler/gpfp_cascade_scheduler.hpp>

namespace RTSim {
    using namespace MetaSim;
    using namespace parse_util;

    using std::unique_ptr;
    using std::vector;

    Task::~Task() {
        DBGENTER(_TASK_DBG_LEV);
        DBGPRINT("Destructor of class Task");
        // discardInstrs(true);
    }

    Task::Task(unique_ptr<RandomVar> iat, Tick rdl, Tick ph,
               const std::string &name, long qs, Tick maxC) :
        Entity(name),
        int_time(std::move(iat)),
        lastArrival(0),
        phase(ph),
        arrival(0),
        execdTime(0),
        _maxC(maxC),
        arrQueue(),
        arrQueueSize(qs),
        state(TSK_IDLE),
        instrQueue(),
        actInstr(),
        _kernel(nullptr),
        _lastSched(0),
        _dl(0),
        _rdl(rdl),
        feedback(nullptr),
        arrEvt(this),
        endEvt(this),
        schedEvt(this),
        deschedEvt(this),
        fakeArrEvt(this),
        killEvt(this),
        deadEvt(this, false, true) {}

    string Task::getStateString() {
        string s = std::to_string(double(SIMUL.getTime())) + " ";
        switch (getState()) {
        case TSK_IDLE:
            s += "idle";
            break;
        case TSK_READY:
            s += "ready";
            break;
        case TSK_EXEC:
            s += "executing";
            break;
        case TSK_BLOCKED:
            s += "blocked";
            break;
        default:
            assert(false);
            break;
        }
        return s;
    }

    void Task::newRun(void) {
        if (!instrQueue.empty()) {
            actInstr = instrQueue.begin();
        } else
            throw EmptyTask();

        state = TSK_IDLE;
        while (chkBuffArrival())
            unbuffArrival();

        lastArrival = arrival = phase;
        if (int_time != nullptr)
            arrEvt.post(arrival);
        _dl = 0;
    }

    void Task::endRun(void) {
        while (!arrQueue.empty()) {
            arrQueue.pop_front();
        }
        arrEvt.drop();
        endEvt.drop();
        schedEvt.drop();
        deschedEvt.drop();
        fakeArrEvt.drop();
        deadEvt.drop();
        killEvt.drop();
    }

    /* Methods from the interface... */
    bool Task::isActive(void) const {
        return state != TSK_IDLE;
    }

    bool Task::isExecuting(void) const {
        return state == TSK_EXEC;
    };

    void Task::schedule(void) {
        DBGENTER(_TASK_DBG_LEV);
        DBGPRINT("Scheduling ", getName());

        _lastSched = SIMUL.getTime();

        // 重要修复：当内核调用schedule()时，任务应该已经被放入就绪队列
        // 因此，无论任务状态如何，都应该记录调度事件
        // 真正的能量检查应该在调度器中完成，当能量不足时，不将任务放入就绪队列
        
        // 记录调度事件
        DBGPRINT("触发调度事件: ", getName(), " 状态: ", getState());
        // 添加调试输出到标准输出
        std::cout << "[DEBUG] Task::schedule() - 触发调度事件: " << getName() 
                  << " 状态: " << getState() 
                  << " 当前时间: " << SIMUL.getTime() << "ms" << std::endl;
        
        // 添加更详细的调试输出
        std::cout << "[DEBUG] Task::schedule() - 调用schedEvt.process()" << std::endl;
        schedEvt.process();
        std::cout << "[DEBUG] Task::schedule() - schedEvt.process()调用完成" << std::endl;
    }

    void Task::deschedule() {
        DBGENTER(_TASK_DBG_LEV);
        DBGPRINT("Descheduling ", getName());

        schedEvt.drop();
        deschedEvt.process();
    }

    void Task::setKernel(AbsKernel *k) { // throw(KernAlreadySet) {
        DBGENTER(_TASK_DBG_LEV);

        if (_kernel != nullptr)
            throw KernAlreadySet();

        _kernel = k;
    }

    void Task::reactivate() {
        Tick v;

        if (int_time != nullptr) {
            v = (Tick) int_time->get();
            if (v > 0)
                arrEvt.post(SIMUL.getTime() + v);
        }
    }

    void Task::handleArrival(Tick arr) {
        DBGENTER(_TASK_DBG_LEV);
        std::cout << "[DEBUG] Task::handleArrival() - 开始: " << getName() 
                  << " 到达时间: " << arr << "ms" << std::endl;

        if (isActive()) {
            DBGPRINT("Task::handleArrival() Task already active!");
            std::cout << "[DEBUG] Task::handleArrival() - 任务已激活，抛出异常: " << getName() << std::endl;
            throw TaskAlreadyActive();
        }

        arrival = arr;
        lastArrival = arr;  // ⭐ 关键修复：同时更新lastArrival，确保getLastArrival()返回当前实例的到达时间
        execdTime = 0;
        actInstr = instrQueue.begin();

        DBGPRINT("Task::handleArrival() instrQueue.begin() accessed ");

        // reset all instructions
        auto p = instrQueue.begin();
        while (p != instrQueue.end()) {
            DBGPRINT("Resetting");
            if (*p == nullptr)
                DBGPRINT("SERIOUS PROBLEM!!");
            (*p)->reset();
            DBGPRINT("Reset");
            p++;
        }

        DBGPRINT("Task::handleArrival() after reset ");

        state = TSK_READY;
        _dl = getArrival() + _rdl;
        std::cout << "[DEBUG] Task::handleArrival() - 设置截止时间: " << getName() 
                  << " 到达时间: " << getArrival() << "ms 相对截止时间: " << _rdl 
                  << "ms 绝对截止时间: " << _dl << "ms" << std::endl;
        if (_dl >= SIMUL.getTime()) {
            DBGPRINT("安排截止时间事件: ", getName(), " 截止时间: ", _dl, " 当前时间: ", SIMUL.getTime());
            std::cout << "[DEBUG] Task::handleArrival() - 安排截止时间事件: " << getName() 
                      << " 截止时间: " << _dl << "ms 当前时间: " << SIMUL.getTime() << "ms" << std::endl;
            deadEvt.post(_dl);
        } else {
            DBGPRINT("截止时间已过，不安排截止时间事件: ", getName(), " 截止时间: ", _dl, " 当前时间: ", SIMUL.getTime());
            std::cout << "[DEBUG] Task::handleArrival() - 截止时间已过，不安排截止时间事件: " << getName() 
                      << " 截止时间: " << _dl << "ms 当前时间: " << SIMUL.getTime() << "ms" << std::endl;
        }
        std::cout << "[DEBUG] Task::handleArrival() - 完成: " << getName() << std::endl;
    }

    void Task::block() {
        // check that the task is not idle and is not already blocked
        if (state == TSK_IDLE || state == TSK_BLOCKED)
            throw string("Task cannot be blocked, because it is ") +
                (state == TSK_IDLE ? "idle" : "blocked");
        _kernel->suspend(this);
        state = TSK_BLOCKED;
        _kernel->dispatch();
    }

    void Task::unblock() {
        state = TSK_READY;
        _kernel->onArrival(this);
    }

    Tick Task::getArrival() const {
        return arrival;
    }

    Tick Task::getLastArrival() const {
        return lastArrival;
    }

    Tick Task::getExecTime() const {
        if (isActive()) {
            return execdTime + (*actInstr)->getExecTime();
        } else {
            return execdTime;
        }
    }

    double Task::getExecCycles() const {
        if (isActive()) {
            return double(execdCycles) + (*actInstr)->getActCycles();
        } else {
            return execdCycles;
        }
    }

    Tick Task::getBuffArrival() {
        Tick time = arrQueue.front();

        arrQueue.pop_front();

        return time;
    }

    bool Task::chkBuffArrival() const {
        return !arrQueue.empty();
    }

    void Task::buffArrival() {
        if ((int) arrQueue.size() <= arrQueueSize) {
            arrQueue.push_back(SIMUL.getTime());
        }
    }

    void Task::unbuffArrival() {
        if (!arrQueue.empty()) {
            arrQueue.pop_back();
        }
    }

    unique_ptr<RandomVar> Task::changeIAT(unique_ptr<RandomVar> iat) {
        unique_ptr<RandomVar> ret = std::move(int_time);

        int_time = std::move(iat);
        return ret;
    }

    void Task::addInstr(unique_ptr<Instr> instr) {
        instrQueue.push_back(std::move(instr));
        DBGTAG(_TASK_DBG_LEV, "Task::addInstr() : Instruction added");
    }

    void Task::discardInstrs(bool selfDestruct) {
        instrQueue.clear();
    }

    /* And finally, the event handlers!!! */

    void Task::onArrival(Event *e) {
        DBGENTER(_TASK_DBG_LEV);
        std::cout << "[DEBUG] Task::onArrival() - 开始: " << getName()
                  << " 当前时间: " << SIMUL.getTime() << "ms"
                  << " isActive: " << isActive() << std::endl;

        if (!isActive()) {
            std::cout << "[DEBUG] Task::onArrival() - 任务未激活: " << getName() << std::endl;
            
            // 检查内核是否设置
            if (!_kernel) {
                std::cout << "[DEBUG] Task::onArrival() - 内核未设置: " << getName() << std::endl;
                // 内核未设置，正常激活任务
                handleArrival(SIMUL.getTime());
                // 注意：这里不能调用_kernel->onArrival(this)，因为_kernel是nullptr
                // 但是，如果内核未设置，任务不应该被调度
                // 我们仍然调用handleArrival()来设置截止时间
                reactivate();
                return;
            }
            
            // 获取调度器
            Scheduler* sched = _kernel->getScheduler();
            if (!sched) {
                std::cout << "[DEBUG] Task::onArrival() - 调度器未设置: " << getName() << std::endl;
                // 调度器未设置，正常激活任务
                handleArrival(SIMUL.getTime());
                _kernel->onArrival(this);
                reactivate();
                return;
            }
            
            // 重要修复：能量检查已经移到了调度器的insert()方法中
            // 当任务被添加到就绪队列时，调度器会检查能量并决定是否添加
            // 这里只需要正常激活任务，让调度器来处理能量约束
            handleArrival(SIMUL.getTime());
            _kernel->onArrival(this);
        } else {
            DBGPRINT("[Buffered]");
            std::cout << "[DEBUG] Task::onArrival() - Buffered模式: " << getName() << std::endl;
            // Buffered Task Arrival: enqueue the request
            // and generate a buffArrEvt for the father;
            // the event will be automatically deleted(),
            // since we put the disposable flag in post to
            // true
            // from old Task ...

            deadEvt.process();

            buffArrival();

            // 重要修复：在Buffered模式下也要调用内核的onArrival
            // 这样可以确保能量检查被执行
            if (_kernel) {
                std::cout << "[DEBUG] Task::onArrival() - Buffered模式调用内核onArrival: " << getName() << std::endl;
                _kernel->onArrival(this);
            }
        }
        reactivate();
    }

    void Task::onEndInstance(Event *) {
        DBGENTER(_TASK_DBG_LEV);

        // from old Task ...
        deadEvt.drop();
        // normal code

        if (!isActive()) {
            throw TaskNotActive("OnEnd() on a non-active task");
        }

        // 修复：suspend结束后任务可能不在执行状态
        // 注释掉这个检查，允许任务不在执行状态时结束实例
        // if (!isExecuting()) {
        //     std::cout << toString() << std::endl;
        //     throw TaskNotExecuting("OnEnd() on a non-executing task");
        // }

        actInstr = instrQueue.begin();
        lastArrival = arrival;

        // 获取CPU索引，但如果任务不在执行状态，可能没有CPU
        CPU *cpu = getCPU();
        int cpu_index = -1;
        if (cpu) {
            cpu_index = cpu->getIndex();
        }

        DBGPRINT("Task ", getName(), " finished on CPU ", cpu_index);

        if (cpu_index >= 0) {
            endEvt.setCPU(cpu_index);
        }

        // 只有当任务在内核中被处理时才调用onEnd
        if (_kernel && isExecuting()) {
            _kernel->onEnd(this);
        }
        state = TSK_IDLE;

        if (feedback) {
            DBGPRINT("Calling the feedback module");
            feedback->notify(getExecTime());
        }

        DBGPRINT("chkBuffArrival for task ",
                 dynamic_cast<Entity *>(this)->getName(), " = ",
                 chkBuffArrival());

        if (chkBuffArrival()) {
            fakeArrEvt.process();

            DBGPRINT("[Fake Arrival generated]");
        }
    }

    void Task::killInstance() { // throw(TaskNotActive, TaskNotExecuting) {
        DBGENTER(_TASK_DBG_LEV);

        if (chkBuffArrival()) {
            fakeArrEvt.post(SIMUL.getTime());
            DBGPRINT("[Fake Arrival generated]");
        }

        if (isExecuting())
            deschedule();

        // killEvt.process();
        killEvt.post(SIMUL.getTime());
    }

    void Task::onKill(Event *e) {
        DBGENTER(_TASK_DBG_LEV);

        // todo right? otherwise at next task arrival, another deadEvt is posted
        // => exception
        deadEvt.drop();

        //(*actInstr)->deschedule();

        // from old Task ...
        killEvt.drop();
        // 防止deschedEvt在kill后触发（killInstance调用deschedule会post deschedEvt）
        deschedEvt.drop();
        // 防止endEvt在kill后触发
        endEvt.drop();
        // normal code

        lastArrival = arrival;

        // 安全处理：任务可能不在CPU上执行（例如在就绪队列中等待）
        CPU *cpu = getCPU();
        int cpu_index = -1;
        if (cpu) {
            cpu_index = cpu->getIndex();
        }

        DBGPRINT("Task ", getName(), " killed on CPU ", cpu_index);

        if (cpu_index >= 0) {
            endEvt.setCPU(cpu_index);
        }

        // ⭐ V48关键修复：即使任务不在执行状态，也需要通知内核清理_m_currExe
        //
        // 原有问题：
        // - 旧逻辑只有当isExecuting()返回true时才调用_kernel->onEnd()
        // - 但在deadline miss场景下，handleDeadlineMiss先调用deschedule()
        //   这会设置state=TSK_READY，导致isExecuting()返回false
        // - 结果是onEnd()不会被调用，_m_currExe不会被清理
        // - 这导致CPU被认为仍然被该任务占用，阻塞后续任务调度
        //
        // 修复策略：
        // 1. 检查任务是否在CPU上（通过getCPU()）
        // 2. 如果在CPU上，即使状态不是EXEC，也调用onEnd()清理内核状态

        // 先检查任务是否在某个CPU上
        bool on_cpu = (cpu != nullptr);

        if (_kernel) {
            if (isExecuting()) {
                // 任务在执行状态，正常调用onEnd
                _kernel->onEnd(this);
            } else if (on_cpu) {
                // 任务不在执行状态但仍在CPU映射中
                // 这可能是因为deschedule()已更新状态但内核未清理
                // 直接调用onEnd清理内核状态
                DBGPRINT("Task ", getName(), " killed while not in EXEC state but on CPU, forcing cleanup");
                _kernel->onEnd(this);
            }
        }
        state = TSK_IDLE;

        if (feedback) {
            DBGPRINT("Calling the feedback module");
            feedback->notify(getExecTime());
        }

        DBGPRINT("chkBuffArrival for task ",
                 dynamic_cast<Entity *>(this)->getName(), " = ",
                 chkBuffArrival());

        if (chkBuffArrival()) {
            fakeArrEvt.process();

            DBGPRINT("[Fake Arrival generated]");
        }

        // 注意：不再调用endRun()，因为它会清除周期性任务的到达事件
        // NonPeriodicTask::onKill()会自行调用endRun()
    }

    void Task::onSched(Event *e) {
        DBGENTER(_TASK_DBG_LEV);

        // 安全处理：任务可能已被killOnMiss终止
        if (!isActive()) {
            DBGPRINT("Task ", getName(), " is not active, skipping sched");
            return;
        }

        int cpu_index = getCPU()->getIndex();

        DBGPRINT("schedEvt for task ", getName(), " on CPU ", cpu_index);

        if (isExecuting()) {
            throw TaskAlreadyExecuting();
        }

        schedEvt.setCPU(cpu_index);
        deschedEvt.drop();

        state = TSK_EXEC;

        // Setting the workload here is innocent enough. Not
        // necessary to ensure correct behavior of the
        // simulator, but the TextTraces expect the speed of
        // the CPU to be updated after this event is
        // handled.
        auto cpu = getCPU();
        if (cpu) {
            cpu->setWorkload(Utils::getTaskWorkload(this));
        }

        (*actInstr)->schedule();

        // from Task ...
        deadEvt.setCPU(cpu_index);
    }

    void Task::onDesched(Event *e) {
        DBGENTER(_TASK_DBG_LEV);

        // 安全处理：任务可能已被killOnMiss终止，此时不再是活动/执行状态
        if (!isActive() || !isExecuting()) {
            DBGPRINT("Task ", getName(), " is not active/executing, skipping desched");
            return;
        }

        // 安全处理：getOldCPU可能为空（任务首次执行时没有旧CPU）
        CPU *oldCpu = getOldCPU();
        int cpu_index = oldCpu ? oldCpu->getIndex() : -1;
        if (cpu_index < 0) {
            // 尝试使用当前CPU
            CPU *curCpu = getCPU();
            cpu_index = curCpu ? curCpu->getIndex() : 0;
        }

        DBGPRINT("CPU: ", getCPU());
        deschedEvt.setCPU(cpu_index);
        endEvt.drop();

        // BUG: execdTime accumulates too much when a task is descheduled and
        // then re-scheduled and an instruction ends

        (*actInstr)->deschedule();
        execdTime += (*actInstr)->getExecTime();

        state = TSK_READY;
    }

    void Task::onInstrEnd() {
        DBGENTER(_TASK_DBG_LEV);
        DBGPRINT("task : ", getName());

        if (!isActive()) {
            DBGPRINT("not active...");
            throw TaskNotActive("onInstrEnd() on a non-active task");
        }

        // this exception conflicts with the implementation of suspendInstr.
        // I am removing it for the moment
        // if (not isExecuting()) {
        //     DBGPRINT("not executing...");
        //     throw TaskNotExecuting("OnInstrEnd() on a non executing task");
        // }

        // 修复：如果当前指令是SuspendInstr，则不检查CPU
        // 因为suspend结束后任务会被deschedule，此时没有CPU是正常的
        bool is_suspend_instr = (dynamic_cast<SuspendInstr *>((*actInstr).get()) != nullptr);

        CPU *p = getCPU();

        if (!is_suspend_instr) {
            // 非suspend指令需要CPU检查
            if (!dynamic_cast<CPU *>(p))
                throw InstrExc("No CPU!", "Task::onInstrEnd()");
            p->setWorkload("idle");
        }

        execdTime += (*actInstr)->getExecTime();
        actInstr++;
        if (actInstr == instrQueue.end()) {
            DBGPRINT("End of instruction list");
            endEvt.post(SIMUL.getTime());
        } else if (isExecuting()) {
            (*actInstr)->schedule();
            DBGPRINT("Next instr scheduled");
        } else if (is_suspend_instr) {
            // 特殊处理：如果刚结束的指令是suspend，即使任务不在执行状态
            // 也需要重新调度任务，让内核dispatch到CPU
            // suspend结束后，actInstr已经指向下一条指令
            // 使用onArrival()而不是直接insert()，这样会触发完整的调度流程
            std::cout << "[DEBUG] Task::onInstrEnd() - suspend ended for "
                      << getName() << " at time " << SIMUL.getTime() << "ms, calling kernel->onArrival()" << std::endl;

            if (_kernel) {
                _kernel->onArrival(this);
            }
        }
    }

    void Task::onFakeArrival(Event *e) {
        DBGENTER(_TASK_DBG_LEV);
        DBGPRINT("fakeArrEvt for task", getName());

        handleArrival(getBuffArrival());

        _kernel->onArrival(this);
    }

    void Task::activate() {
        activate(SIMUL.getTime());
    }

    void Task::activate(Tick t) {
        arrEvt.drop();
        arrEvt.post(t);
    }

    Tick Task::getWCET() const {
        Tick tt = 0;
        if (_maxC == 0) {
            auto i = instrQueue.begin();
            while (i != instrQueue.end()) {
                tt += (*i)->getWCET();
                i++;
            }
        } else
            tt = _maxC;
        return tt;
    }

    void Task::insertCode(const string &code) { // throw(ParseExc)
        DBGENTER(_TASK_DBG_LEV);

        vector<string> instr = split_instr(code);

        for (unsigned int i = 0; i < instr.size(); ++i) {
            vector<string>::iterator j;

            string token = get_token(instr[i]);
            string param = get_param(instr[i]);
            vector<string> par_list = split_param(param);

            par_list.push_back(string(getName()));

            for (j = par_list.begin(); j != par_list.end(); ++j)
                DBGPRINT(" - ", *j);
            DBGPRINT("");

            unique_ptr<Instr> curr =
                genericFactory<Instr>::instance().create(token, par_list);

            if (!curr)
                throw ParseExc("insertCode", token);

            DBGPRINT("Instr ", curr->getName(), "  created.");
            // todo
            std::cout << "Task::insertCode. instr created: " << curr->getName()
                      << std::endl;

            addInstr(std::move(curr));

            printInstrList();
        }
    }

    void Task::printInstrList() const {
        unsigned int i;

        std::cout << "Task " << getName() << ": instruction list" << std::endl;
        DBGPRINT("Task ", getName(), ": instruction list");
        for (i = 0; i < instrQueue.size(); ++i) {
            std::cout << i << ") " << instrQueue[i]->toString() << std::endl;
            DBGPRINT(i, ") ", instrQueue[i]->toString());
        }
    }

    CPU *Task::getCPU() const {
        DBGTAG(_TASK_DBG_LEV, "Task::getCPU()");

        return _kernel->getProcessor(this);
    }

    CPU *Task::getOldCPU() const {
        DBGTAG(_TASK_DBG_LEV, "Task::getOldCPU()");

        return _kernel->getOldProcessor(this);
    }

    void Task::refreshExec(double oldSpeed, double newSpeed) {
        DBGENTER(_TASK_DBG_LEV);
        (*actInstr)->refreshExec(oldSpeed, newSpeed);
    }

    std::string taskname(const AbsRTTask *t) {
        const Entity *e = dynamic_cast<const Entity *>(t);
        if (e)
            return string(e->getName());
        else
            return "(nil)";
    }

    unique_ptr<Task> Task::createInstance(const vector<string> &par) {
        unique_ptr<RandomVar> i;
        if (par[0] != "0") //(strcmp(par[0].c_str(), "0"))
            i = RandomVar::parsevar(par[0]);
        Tick d = Tick(par[1]);
        Tick p = Tick(par[2]);
        string n = "";
        // const char* n = "";
        if (par.size() > 2)
            n = par[3];
        long q = 1000;
        if (par.size() > 4)
            q = 1000; // atoi(par[4].c_str()); // TODO: WHY?
        bool a = true;
        if (par.size() > 5 && par[5] != "false")
            a = false;
        unique_ptr<Task> t(new Task(std::move(i), d, p, n, q, a));
        return t;
    }

    void Task::setFeedbackModule(AbstractFeedbackModule *afm) {
        feedback = afm;
    }

    void Task::resetInstrQueue() {
        actInstr = instrQueue.begin();
    }

    void Task::killOnMiss(bool kill) {
        deadEvt.setKill(kill);
    }

    double Task::getRemainingWCET(double capacity) const {
        // todo keep track of task migrations and do as in
        // ExecInstr::refreshExec(
        Tick alreadyExecdCycles = Tick(double(getExecTime()) * capacity);
        double n = double(getWCET() - alreadyExecdCycles) / capacity;
        // printf("%s (%f-%f)/%f=%f\n", __func__, double(getWCET()),
        // double(alreadyExecdCycles), capacity, n);
        return n;
    }

    string Task::toString() const {
        std::stringstream ss;
        ss << getName() << " arr " << getArrival() << " DL " << getDeadline()
           << " WCET " << getWCET();
        return ss.str();
    }

    /// to string operator
    std::ostream &operator<<(std::ostream &strm, Task &a) {
        strm << a.toString();
        return strm;
    }

} // namespace RTSim
