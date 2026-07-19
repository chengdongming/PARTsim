#ifndef RTSIM_B3_TIMING_TRACE_HPP
#define RTSIM_B3_TIMING_TRACE_HPP

#include <map>
#include <string>
#include <vector>

#include <rtsim/json_trace.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/task.hpp>

namespace RTSim {

    // Trace-only adapter shared by the three ALAP implementations.  It reads
    // the same public task/model facts already used by their native gates.
    template <typename Model>
    SchedulerTraceJob makeB3TraceJob(
        AbsRTTask *task,
        const std::map<AbsRTTask *, Model *> &models,
        int ready_order) {
        SchedulerTraceJob job{};
        Task *concrete_task = dynamic_cast<Task *>(task);
        job.task_name = concrete_task
            ? concrete_task->getName()
            : std::string("task_") +
                std::to_string(task ? task->getTaskNumber() : -1);
        job.arrival_time = concrete_task
            ? static_cast<double>(concrete_task->getLastArrival())
            : (task ? static_cast<double>(task->getArrival()) : 0.0);
        job.priority = 0.0;
        job.ready_order = ready_order;
        job.task_unit_energy_mJ = 0.0;
        job.remaining_time_ms = task ? task->getRemainingWCET() : 0.0;
        job.absolute_deadline = task
            ? static_cast<double>(task->getDeadline()) : 0.0;

        const auto model_it = models.find(task);
        if (model_it != models.end() && model_it->second) {
            job.priority = static_cast<double>(
                model_it->second->getRMPriority());
            job.task_unit_energy_mJ =
                model_it->second->getUnitEnergy() * 1000.0;
        }
        return job;
    }

    template <typename Model>
    std::vector<SchedulerTraceJob> makeB3TraceJobs(
        const std::vector<AbsRTTask *> &tasks,
        const std::map<AbsRTTask *, Model *> &models) {
        std::vector<SchedulerTraceJob> jobs;
        jobs.reserve(tasks.size());
        for (std::size_t i = 0; i < tasks.size(); ++i) {
            jobs.push_back(makeB3TraceJob(
                tasks[i], models, static_cast<int>(i)));
        }
        return jobs;
    }

} // namespace RTSim

#endif
