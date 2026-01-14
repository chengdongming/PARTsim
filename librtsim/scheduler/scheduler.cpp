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

#include <climits>

#include <iostream>

#include <metasim/simul.hpp>

#include <rtsim/scheduler/scheduler.hpp>
#include <rtsim/task.hpp>

namespace RTSim {
    using namespace MetaSim;

    using std::map;

    TaskModel::TaskModel(AbsRTTask *t) :
        _rtTask(t),
        active(false),
        _insertTime(0)
    // , _threshold(INT_MAX)
    {}

    TaskModel::~TaskModel() {}

    bool TaskModel::TaskModelCmp::operator()(TaskModel *a, TaskModel *b) const {
        // Order by priority, then inserion time, and finally task number using
        // tuple's partial ordering instead of writing our own (less error
        // prone)
        // For RM scheduling: priority values are negative (e.g., -500, -600, -1000, -1200)
        // where shorter period = higher priority = less negative (e.g., -500 > -1200)
        // We use std::greater so highest priority (least negative/largest number) is at begin()
        auto a_tuple = std::tuple(a->getPriority(), a->getInsertTime(),
                                  a->getTaskNumber());
        auto b_tuple = std::tuple(b->getPriority(), b->getInsertTime(),
                                  b->getTaskNumber());

        return a_tuple > b_tuple;
    }

    void TaskModel::setActive() {
        active = true;
    }

    void TaskModel::setInactive() {
        active = false;
    }

    bool TaskModel::isActive() {
        return active;
    }

    // NOTE: deprecated
    // void TaskModel::raiseThreshold() {
    //     _savedPriority = getPriority();
    //     // std::cout << "New priority: " << getThreshold() << " / old
    //     priority:
    //     // " << _savedPriority << std::endl;
    //     changePriority(getThreshold());
    // }

    // NOTE: deprecated
    // void TaskModel::restorePriority() {
    //     DBGTAG(_SCHED_DBG_LEVEL, "Restoring Priority");
    //     // std::cout << "Restoring priority from: " << getPriority() << " to:
    //     "
    //     // << _savedPriority << std::endl;
    //     changePriority(_savedPriority);
    // }

    /*-----------------------------------------------------------------*/

    Scheduler::Scheduler() :
        Entity(""),
        _kernel(0),
        _queue(),
        _tasks(),
        _currExe(0) {}

    Scheduler::~Scheduler() {}

    void Scheduler::enqueueModel(TaskModel *model) {
        AbsRTTask *task = model->getTask();
        if (find(task) != nullptr)
            throw RTSchedExc("Element already present");
        _tasks[task] = model;
    }

    TaskModel *Scheduler::find(AbsRTTask *task) const {
        auto mi = _tasks.find(task);
        if (mi == _tasks.end())
            return nullptr;
        else
            return (*mi).second;

        // // NOTE: this code should not be necessary anymore. If you need it
        // // remove the if-else above and uncomment this block.
        // while (mi == _tasks.end()) {
        //     auto kernel = task->getKernel();
        //     task = dynamic_cast<AbsRTTask *>(kernel);
        //     if (task == nullptr)
        //         return nullptr;
        //     mi = _tasks.find(task);
        // }
        // return (*mi).second;
    }

    void Scheduler::setKernel(AbsKernel *k) {
        _kernel = k;
    }

    void Scheduler::insert(AbsRTTask *task) { // throw(RTSchedExc, BaseExc) {
        DBGENTER(_SCHED_DBG_LEVEL);

        std::cout << "[DEBUG] Scheduler::insert() - 开始: " << taskname(task)
                  << " 当前时间: " << SIMUL.getTime() << "ms" << std::endl;

        TaskModel *model = find(task);
        if (model == nullptr) {
            std::cerr << "Scheduler::insert Task model not found" << std::endl;
            std::cerr << "For task " << taskname(task) << std::endl;
            std::cerr << "Scheduler " << getName() << std::endl;
            throw RTSchedExc("AbsRTTaskNotFound");
        }

        std::cout << "[DEBUG] Scheduler::insert() - 找到模型: " << taskname(task) << std::endl;

        model->setInsertTime(SIMUL.getTime());
        model->setActive();

        _queue.insert(model);
        std::cout << "[DEBUG] Scheduler::insert() - 任务已添加到队列: " << taskname(task)
                  << " 队列大小: " << _queue.size() << " 优先级: " << model->getPriority() << std::endl;

        // 调试：显示队列前4个任务的优先级
        std::cout << "[DEBUG] 队列前4个任务: ";
        int count = 0;
        for (auto it = _queue.begin(); it != _queue.end() && count < 4; ++it, ++count) {
            std::cout << taskname((*it)->getTask()) << "(prio=" << (*it)->getPriority() << ") ";
        }
        std::cout << std::endl;
    }

    void Scheduler::extract(AbsRTTask *task) { // throw(RTSchedExc, BaseExc) {
        DBGENTER(_SCHED_DBG_LEVEL);

        std::cout << "[DEBUG] Scheduler::extract() - 开始: " << taskname(task)
                  << " 当前时间: " << SIMUL.getTime() << "ms"
                  << " 队列大小: " << _queue.size() << std::endl;

        TaskModel *model = find(task);
        if (model == nullptr) // raise an exception
            throw RTSchedExc("AbsRTTask not found");

        _queue.erase(model);
        model->setInactive();

        std::cout << "[DEBUG] Scheduler::extract() - 完成: " << taskname(task)
                  << " 队列大小: " << _queue.size() << std::endl;
    }

    int Scheduler::getPriority(AbsRTTask *task) const { // throw(RTSchedExc) {
        TaskModel *model = find(task);
        if (model == nullptr)
            throw RTSchedExc("AbsRTTask not found");

        return model->getPriority();
    }

    // NOTE: deprecated
    // int Scheduler::getThreshold(AbsRTTask *task) { // throw(RTSchedExc) {
    //     TaskModel *model = find(task);
    //     if (model == nullptr)
    //         throw RTSchedExc("AbsRTTask not found");
    //     return model->getThreshold();
    // }

    // NOTE: deprecated
    // void Scheduler::setThreshold(AbsRTTask *task,
    //                              int th) { // throw(RTSchedExc) {
    //     TaskModel *model = find(task);
    //     if (model == nullptr)
    //         throw RTSchedExc("AbsRTTask not found");
    //     model->setThreshold(th);
    // }

    // NOTE: deprecated
    // void Scheduler::enableThreshold(AbsRTTask *task) { // throw(RTSchedExc) {
    //     DBGENTER(_SCHED_DBG_LEVEL);
    //     TaskModel *model = find(task);
    //     if (model == nullptr)
    //         throw RTSchedExc("AbsRTTask not found");
    //     // the check for the executing task is in the kernel
    //     model->raiseThreshold();
    // }

    // NOTE: deprecated
    // void Scheduler::disableThreshold(AbsRTTask *task) { // throw(RTSchedExc)
    // {
    //     DBGENTER(_SCHED_DBG_LEVEL);
    //     TaskModel *model = find(task);
    //     if (model == nullptr)
    //         throw RTSchedExc("AbsRTTask not found");
    //     // std::cout << "disableThreshold called" << std::endl;
    //     if (model->isActive()) {
    //         extract(task);
    //         model->restorePriority();
    //         insert(task);
    //         _kernel->dispatch();
    //     } else
    //         model->restorePriority();
    // }

    void Scheduler::discardTasks(bool f) {
        DBGENTER(_SCHED_DBG_LEVEL);

        _queue.clear();

        if (f) {
            // Free all task models
            for (auto [task, model] : _tasks) {
                delete model;
            }
        }

        // XXX: Why do we always clear the set of tasks, but we may not free the
        // models? Are we leaking memory? Is anyone actually calling this
        // function?
        _tasks.clear();
    }

    AbsRTTask *Scheduler::getTaskN(unsigned int n) {
        DBGENTER(_SCHED_DBG_LEVEL);

        std::cout << "[DEBUG] Scheduler::getTaskN(" << n << ") - 队列大小: " << _queue.size()
                  << " 当前时间: " << SIMUL.getTime() << "ms" << std::endl;

        if (_queue.size() <= n) {
            std::cout << "[DEBUG] Scheduler::getTaskN(" << n << ") - 返回nullptr (队列太小)" << std::endl;
            return nullptr;
        }

        // Jump to nth element in queue
        auto it = _queue.begin();
        std::advance(it, n);
        AbsRTTask *task = (*it)->getTask();
        std::cout << "[DEBUG] Scheduler::getTaskN(" << n << ") - 返回任务: " << taskname(task) << std::endl;
        return task;
    }

    bool Scheduler::isFound(AbsRTTask *t) {
        TaskModel *model = find(t);
        return model != nullptr;
    }

    bool Scheduler::isInQueue(AbsRTTask *t) {
        auto task_is_t = [t](TaskModel *model) {
            return model->getTask() == t;
        };

        // If not found, returns the end of the queue
        auto found = std::find_if(_queue.begin(), _queue.end(), task_is_t);
        return found != _queue.end();
    }

    void Scheduler::notify(AbsRTTask *task) {
        DBGENTER(_SCHED_DBG_LEVEL);
        _currExe = task;
    }

    void Scheduler::newRun() {
        std::cout << "[DEBUG] Scheduler::newRun() - 清空队列，队列大小: " << _queue.size()
                  << " 当前时间: " << SIMUL.getTime() << "ms" << std::endl;

        _queue.clear();

        for (auto [task, model] : _tasks) {
            model->setInactive();
        }

        std::cout << "[DEBUG] Scheduler::newRun() - 完成，队列大小: " << _queue.size() << std::endl;
    }

    void Scheduler::endRun() {}

    template <typename Iter, typename EndIter>
    bool is_last(Iter iter, const EndIter &endIter) {
        return (iter != endIter) && (std::next(iter) == endIter);
    }

    std::string Scheduler::toString() const {
        std::ostringstream oss;

        oss << "Ready queue: ";
        for (auto it = _queue.begin(); it != _queue.end(); ++it) {
            oss << taskname((*it)->getTask());
            if (!is_last(it, _queue.end())) {
                oss << " -> ";
            }
        }

        return oss.str();
    }

    AbsRTTask *Scheduler::getFirst() {
        return getTaskN(0);
    }
} // namespace RTSim

