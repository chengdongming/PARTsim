// RTSim
#include <rtsim/cbserver.hpp>
#include <rtsim/energyMRTKernel.hpp>
#include <rtsim/scheduler/multisched.hpp>

// Schedulers
#include <rtsim/scheduler/edfsched.hpp>
#include <rtsim/scheduler/rrsched.hpp>

namespace RTSim {
    MultiCoresScheds::MultiCoresScheds(MRTKernel *kernel,
                                       std::vector<CPU *> &cpus,
                                       std::vector<Scheduler *> &scheds,
                                       const std::string &name) :
        Entity(name),
        _kernel(kernel) {
        assert(cpus.size() == scheds.size());

        for (size_t i = 0; i < cpus.size(); ++i) {
            Scheduler *s = scheds[i];
            s->setKernel(_kernel);
            _queues[cpus[i]] = s;
        }
    }

    void MultiCoresScheds::postBeginEvt(CPU *cpu, AbsRTTask *task, Tick when) {
        BeginDispatchMultiEvt *ee = new BeginDispatchMultiEvt(*_kernel, *cpu);
        ee->post(when);
        ee->setTask(task);
        _beginEvts[cpu] = ee;
    }

    void MultiCoresScheds::postEndEvt(CPU *cpu, AbsRTTask *task, Tick when) {
        EndDispatchMultiEvt *ee = new EndDispatchMultiEvt(*_kernel, *cpu);
        ee->post(when);
        ee->setTask(task);
        _endEvts[cpu] = ee;
    }

    void MultiCoresScheds::dropEvt(CPU *cpu, AbsRTTask *task) {
        assert(cpu != nullptr);
        assert(task != nullptr);

        // I don'task like the fact that we are erasing and re-creating
        // events all the time
        auto begin_evt = _beginEvts.find(cpu);
        if (begin_evt != _beginEvts.end() &&
            begin_evt->second->getTask() == task) {
            begin_evt->second->drop();
            _beginEvts.erase(cpu);
        }

        auto end_evt = _endEvts.find(cpu);
        if (end_evt != _endEvts.end() && end_evt->second->getTask() == task) {
            end_evt->second->drop();
            _endEvts.erase(cpu);
        }
    }

    void MultiCoresScheds::makeRunning(AbsRTTask *task, CPU *cpu) {
        assert(cpu != nullptr);
        assert(task != nullptr);

        // The task may have to wait the end time of the context switch
        // before it can be run
        Tick when = SIMUL.getTime();
        if (isContextSwitching(cpu))
            when = _endEvts[cpu]->getTime();
        dropEvt(cpu, task);
        postBeginEvt(cpu, task, when);
        // std::cout << "task = " << SIMUL.getTime() << ", ctx switch set at"
        //      << double(when) << " for " << taskname(task) << std::endl;
    }

    void MultiCoresScheds::makeReady(CPU *cpu) {
        AbsRTTask *oldTask = getRunningTask(cpu);
        dropEvt(cpu, oldTask);

        // TODO: I prefer the original implementation of the MRTKernel which
        // puts a nullptr in the map
        _running_tasks.erase(cpu);
        oldTask->deschedule();
    }

    void MultiCoresScheds::addTask(AbsRTTask *task, CPU *cpu,
                                   const std::string &params) {
        _queues[cpu]->addTask(task, params);
        if (dynamic_cast<RRScheduler *>(_queues[cpu]))
            dynamic_cast<RRScheduler *>(_queues[cpu])->notify(task);
    }

    size_t MultiCoresScheds::countTasks(CPU *cpu) {
        // TODO: const
        return _queues[cpu]->getSize();
        // int i = 0;
        // while (_queues[cpu]->getTaskN(i) != nullptr)
        //     i++;
        // return i;
    }

    void MultiCoresScheds::empty(CPU *cpu) {
        for (CBServer *s : _kernel->getServers())
            removeFromQueue(cpu, s);

        while (!isEmpty(cpu)) {
            removeFirstFromQueue(cpu);
        }
    }

    bool MultiCoresScheds::isEmpty(CPU *cpu) {
        return countTasks(cpu) == 0;
    }

    Scheduler *MultiCoresScheds::getScheduler(CPU *cpu) {
        return _queues[cpu];
    }

    AbsRTTask *MultiCoresScheds::getFirst(CPU *cpu) {
        // Assumes that cpu is a valid key in _queues

        AbsRTTask *task = _queues[cpu]->getFirst();

        CBServer *cbs = dynamic_cast<CBServer *>(task);
        if (cbs != nullptr && cbs->isYielding())
            task = getFirstReady(cpu);

        return task;
    }

    AbsRTTask *MultiCoresScheds::getFirstReady(CPU *cpu) {
        assert(cpu != nullptr);
        // Assumes there is only 1 CBServer per core and that the first task
        // is a CBServer that is yielding the CPU

        AbsRTTask *task = _queues[cpu]->getTaskN(1);
        return task;
    }

    CPU *MultiCoresScheds::getProcessorRunning(const AbsRTTask *task) const {
        for (const auto &elem : _running_tasks)
            if (elem.second == task)
                return elem.first;
        return nullptr;
    }

    CPU *MultiCoresScheds::getProcessorReady(const AbsRTTask *task) const {
        for (auto &elem : _queues) {
            std::vector<AbsRTTask *> tasks = getAllTasksInQueue(elem.first);
            for (AbsRTTask *it : tasks)
                if (it == task) {
                    CPU *cpu = elem.first;
                    return cpu;
                }
        }
        return nullptr;
    }

    CPU *MultiCoresScheds::getProcessor(const AbsRTTask *task) const {
        CPU *cpu = getProcessorRunning(task);
        if (cpu == nullptr)
            cpu = getProcessorReady(task);
        return cpu;
    }

    AbsRTTask *MultiCoresScheds::getRunningTask(CPU *cpu) {
        // TODO: const
        assert(cpu != nullptr);
        return _running_tasks[cpu];
    }

    std::vector<AbsRTTask *>
        MultiCoresScheds::getAllTasksInQueue(CPU *cpu) const {
        // TODO: const properly

        std::vector<AbsRTTask *> tasks;

        auto res = _queues.find(cpu);
        if (res == _queues.cend())
            throw std::runtime_error("CPU not in set!");
        Scheduler *s = res->second;
        AbsRTTask *task;
        for (size_t i = 0; (task = s->getTaskN(i)) != nullptr; ++i) {
            if (dynamic_cast<CBServer *>(task)->isYielding()) {
                ++i;
                continue;
            }
            tasks.push_back(task);
        }

        return tasks;
    }

    std::vector<AbsRTTask *> MultiCoresScheds::getReadyTasks(CPU *cpu) {
        assert(cpu != nullptr);
        std::vector<AbsRTTask *> tasks = getAllTasksInQueue(cpu);
        AbsRTTask *r = getRunningTask(cpu);
        if (r == nullptr)
            return tasks;
        for (size_t i = 0; i < tasks.size(); ++i)
            if (tasks.at(i) == r) {
                tasks.erase(tasks.begin() + i);
                break;
            }
        return tasks;
    }

    void MultiCoresScheds::insertTask(AbsRTTask *task, CPU *cpu) {
        try {
            _queues[cpu]->insert(task);
        } catch (RTSchedExc &e) {
            // core schedulers/queues do not know tasks until this point
            std::cout << "Receiving this error once per task is ok"
                      << std::endl;
            addTask(task, cpu, "");
            insertTask(task, cpu);
        }
    }

    CPU *MultiCoresScheds::isInAnyQueue(const AbsRTTask *task) {
        for (const auto &q : _queues) {
            std::vector<AbsRTTask *> tasks = getAllTasksInQueue(q.first);
            for (AbsRTTask *tt : tasks)
                if (tt == task)
                    return q.first;
        }

        return nullptr;
    }

    bool MultiCoresScheds::isContextSwitching(CPU *cpu) const {
        return _endEvts.find(cpu) != _endEvts.cend();
    }

    void MultiCoresScheds::onBeginDispatchMultiFinished(CPU *cpu,
                                                        AbsRTTask *newTask,
                                                        Tick overhead) {
        assert(cpu != nullptr);
        assert(newTask != nullptr);
        assert(double(overhead) >= 0.0);

        // Drop all events on the cpu for that task and post the end context
        // switch event
        dropEvt(cpu, newTask);
        postEndEvt(cpu, newTask, SIMUL.getTime() + overhead);
    }

    void MultiCoresScheds::onEndDispatchMultiFinished(CPU *cpu,
                                                      AbsRTTask *task) {
        assert(cpu != nullptr);
        assert(task != nullptr);
        _running_tasks[cpu] = task;
        dropEvt(cpu, task);
    }

    void MultiCoresScheds::onEnd(AbsRTTask *t, CPU *cpu) {
        assert(cpu != nullptr);
        assert(t != nullptr);

        std::cout << "\t" << cpu->getName()
                  << " has now wl: " << cpu->getWorkload()
                  << ", speed: " << cpu->getSpeed() << std::endl
                  << std::endl;

        // check if there is consistency still
        AbsRTTask *tt = getRunningTask(cpu);
        assert(tt != nullptr && t == tt);

        removeFromQueue(cpu, t);
        makeReady(cpu);
    }

    void MultiCoresScheds::onMigrationFinished(AbsRTTask *task, CPU *original,
                                               CPU *final) {
        assert(task != nullptr);
        assert(original != nullptr);
        assert(final != nullptr);
        std::cout << "\t\tMCS::" << __func__ << "() migrate "
                  << task->toString() << " from " << original->toString()
                  << " to " << final->toString() << std::endl;

        try {
            removeFromQueue(original, task);
            insertTask(task, final);
            makeRunning(task, final);
        } catch (RTSchedExc &e) {
            // ?!? what?? why??
            insertTask(task, final);
            onMigrationFinished(task, original, final);
        }
    }

    void MultiCoresScheds::onExecutingRecharging(CBServer *cbs) {
        // std::cout << "t=" << SIMUL.getTime() << " MCS::" << __func__
        //           << "() for " << cbs->getName() << std::endl;

        forgetU_active(cbs);
    }

    void MultiCoresScheds::onExecutingReleasing(CPU *cpu, CBServer *cbs) {
        assert(cbs != nullptr);
        assert(cpu != nullptr);

        // std::cout << "t=" << SIMUL.getTime() << " MCS::" << __func__
        //           << "() for " << cbs->getName() << std::endl;

        saveU_active(cpu, cbs);

        // std::cout << "\tCBS server has now #tasks="
        //           << cbs->getTasks().size()
        //           << (cbs->getTasks().size() > 0
        //                   ? " - first one: " +
        //                         cbs->getTasks().at(0)->toString()
        //                   : "")
        //           << std::endl;
    }

    CPU *MultiCoresScheds::onReleasingIdle(CBServer *cbs) {
        // std::cout << "t=" << SIMUL.getTime() << " MCS::" << __func__
        //           << "() for " << cbs->getName() << std::endl;

        CPU *cpu = forgetU_active(cbs);
        assert(cpu != nullptr);
        return cpu;
    }

    void MultiCoresScheds::onReplenishment(CBServer *cbs) {
        // std::cout << "t=" << SIMUL.getTime() << " MCS::" << __func__
        //           << "() for " << cbs->getName() << std::endl;

        // if (SIMUL.getTime() == cbs->getPeriod())
        forgetU_active(cbs);

        CPU *cpu = getProcessorRunning(cbs);
        if (cpu != nullptr) // server might be with no task => already ready
            makeReady(cpu);
    }

    void MultiCoresScheds::onRound(AbsRTTask *finishingTask, CPU *cpu) {
        if (cpu == nullptr) // task has just finished its WCET
            return;
        schedule(cpu);
    }

    void MultiCoresScheds::onTaskInServerEnd(AbsRTTask *task, CPU *cpu,
                                             CBServer *cbs) {
        // std::cout << "MCS::" << __func__ << "() for " << cbs->getName()
        //           << std::endl;
        assert(task != nullptr);
        assert(cbs != nullptr);
        assert(cpu != nullptr);

        // saveU_active(cpu, cbs);
    }

    void MultiCoresScheds::removeFromQueue(CPU *cpu, AbsRTTask *task) {
        assert(cpu != nullptr);
        assert(task != nullptr);
        if (_queues[cpu]->isFound(task))
            _queues[cpu]->extract(task);
        dropEvt(cpu, task);
    }

    void MultiCoresScheds::removeFirstFromQueue(CPU *cpu) {
        AbsRTTask *task = getFirst(cpu);
        removeFromQueue(cpu, task);
    }

    void MultiCoresScheds::schedule(CPU *cpu) {
        assert(cpu != nullptr);
        AbsRTTask *task = getFirst(cpu);
        // std::cout << __func__ << "() "
        //           << (task == nullptr ? "" : task->toString() + " on ")
        //           << cpu->getName() << std::endl;

        if (shouldDeschedule(cpu, task))
            makeReady(cpu);

        // Request to schedule on core with no assigned tasks
        if (shouldSchedule(cpu, task))
            makeRunning(task, cpu);
    }

    bool MultiCoresScheds::shouldDeschedule(CPU *cpu, AbsRTTask *task) {
        if (dynamic_cast<RRScheduler *>(_queues[cpu])) {
            return getRunningTask(cpu) != nullptr;
        } else if (dynamic_cast<EDFScheduler *>(_queues[cpu])) {
            if (dynamic_cast<CBServer *>(task)) {
                if (getRunningTask(cpu) == task)
                    return false;
                else if (dynamic_cast<CBServer *>(task)->isYielding())
                    return true;
            }
            bool isNotSameTask =
                getRunningTask(cpu) != nullptr && getRunningTask(cpu) != task;
            return isNotSameTask;
        }
        assert(false); // add your choice/branch
        return false;
    }

    bool MultiCoresScheds::shouldSchedule(CPU *cpu, AbsRTTask *task) {
        CBServer *cbs = dynamic_cast<CBServer *>(task);
        if (cbs != nullptr) {
            if (getRunningTask(cpu) == task || cbs->isYielding())
                return false;
        }
        return task != nullptr;
    }

    void MultiCoresScheds::yield(CPU *cpu) {
        assert(cpu != nullptr); // running task might have already ended

        std::cout << "\tCore status: " << _queues[cpu]->toString() << std::endl;
        AbsRTTask *nextReady = getFirstReady(cpu);
        if (nextReady != nullptr) {
            // todo remove
            std::cout << "\tYielding in favour of " << nextReady->toString()
                      << std::endl;
            AbsRTTask *runningTask = getRunningTask(cpu);
            if (runningTask != nullptr) {
                makeReady(cpu);
                runningTask->deschedule();
            }
            makeRunning(nextReady, cpu);
        }
    }

    // =====================================================
    // Operations that manage active utilizations
    // =====================================================

    CPU *MultiCoresScheds::forgetU_active(AbsRTTask *task) {
        // std::cout << "\tMSC::" << __func__ << "() for " <<
        // task->toString()
        //           << std::endl;
        CPU *cpu;

        if (EnergyMRTKernel::EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED)
            task = dynamic_cast<EnergyMRTKernel *>(_kernel)->getEnveloper(task);

        for (auto &elem : _active_utilizations) {
            // std::cout << "forgetU_active: " << elem.first->toString() <<
            // std::endl;
            if (elem.first == task) {
                // std::cout << "\treleasing_idle for " <<
                // elem.first->toString()
                //      << " on " << elem.second.cpu->getName()
                //      << ". Its U_act was " << elem.second.uact << std::endl;
                cpu = elem.second.cpu;
                _active_utilizations.erase(elem.first);
                break;
            }
        }
        return cpu;
    }

    double MultiCoresScheds::getUtilization_active(CPU *cpu) const {
        assert(cpu != NULL);
        double u_active = 0.0;

        for (const auto &elem : _active_utilizations)
            if (elem.second.cpu == cpu)
                u_active += elem.second.uact;

        assert(u_active >= 0.0);
        return u_active;
    }

    double MultiCoresScheds::getUtilization_active(AbsRTTask *t) {
        assert(t != NULL);

        return _active_utilizations[t].uact;
    }

    void MultiCoresScheds::saveU_active(CPU *cpu, CBServer *cbs) {
        // std::cout << "MCS::" << __func__ << "() " << std::endl;
        assert(cbs != NULL);
        assert(cpu != NULL);

        // todo: e se il task e' migrato? allora sommo le utilizzazioni
        // parziali, che e' facile double u_active =
        // double(cbs->getWCET(cpu->getSpeed())) / (double)
        // cbs->getDeadline(); printf("\tu_active = %f/%f (wl: %s, speed:
        // %f)\n", double(cbs->getWCET(cpu->getSpeed())), (double)
        // cbs->getDeadline(), cpu->getWorkload().c_str(), cpu->getSpeed());
        // if (cbs->isKilled()) {
        double u_active = double(cbs->getBudget()) / double(cbs->getDeadline());
        // todo right? or it applies only in
        // case of CBS_ENVELOPING_PER_TASK?

        // printf("\tu_active = %f/%f (wl: %s, speed: %f). Amendment,
        // \'cause task\'s killed\n", double(cbs->getBudget()), (double)
        // cbs->getDeadline(), cpu->getWorkload().c_str(), cpu->getSpeed());
        // printf(
        //     "\tu_active = %f/%f (wl: %s, speed: %f, based on CBS
        //     budget)\n", double(cbs->getBudget()),
        //     (double)cbs->getDeadline(), cpu->getWorkload().c_str(),
        //     cpu->getSpeed());
        // }

        // a better map is by cpu, but then cpus can collide
        Tick vt = Tick(cbs->getVirtualTime());
        if (double(vt) < double(SIMUL.getTime())) {
            // std::cout << "\tvt = " << vt
            //      << " <= simul time = " << SIMUL.getTime() << " => skip"
            //      << std::endl;
            return;
        }

        CPU_Utilizations u;
        u.cpu = cpu;
        u.virtual_time = vt;
        u.uact = u_active;

        _active_utilizations[cbs] = u;
        // std::cout << "\tadded active utilization for " << cbs->getName()
        //      << " cpu " << cpu->toString() << " U_act " << u_active
        //      << ", cancel at t=" <<
        //      _active_utilizations[cbs].virtual_time
        //      << std::endl;
    }

    std::string MultiCoresScheds::toString() const {
        return "MultiCoresScheds toString().";
    }
} // namespace RTSim
