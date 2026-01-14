// TODO: organize includes

// #pragma once
#ifndef LIBRTSIM_SYSTEM_HPP
#define LIBRTSIM_SYSTEM_HPP

// STL
#include <metasim/memory.hpp>
#include <vector>

// Static system information
#include <rtsim/cpu.hpp>
#include <rtsim/kernel.hpp>
#include <rtsim/mrtkernel.hpp>
#include <rtsim/powermodel.hpp>
#include <rtsim/scheduler/edfsched.hpp>
#include <rtsim/tracepower.hpp>

// Tasks system information
#include <rtsim/instr.hpp>
#include <rtsim/rttask.hpp>

// Tracing
#include <rtsim/json_trace.hpp>
#include <rtsim/jtrace.hpp>
#include <rtsim/texttrace.hpp>
#include <rtsim/tracepower.hpp>

// File formats
#include <rtsim/csv.hpp>
#include <rtsim/yaml.hpp>

namespace RTSim {
    template <class T>
    using sptr = std::shared_ptr<T>;

    template <class T>
    using uptr = std::unique_ptr<T>;

    // TODO: all private, immutable and yadda yadda
    // TODO: the system has no notion of islands, should add that!
    class System {
        // TODO: hide?
    public:
        std::vector<sptr<CPUModel>> cpu_models;
        std::vector<sptr<CPUIsland>> islands;
        std::vector<sptr<CPU>> cpus;
        std::vector<sptr<TracePowerConsumption>> ptraces;
        std::vector<sptr<Scheduler>> schedulers;
        std::vector<sptr<RTKernel>> kernels;

    public:
        System(const std::string &fname);
    };

    // TODO:
    class TaskSet {
    public:
        using Task_ptr = std::shared_ptr<Task>;

        std::vector<Task_ptr> tasks;
    };

} // namespace RTSim

#endif // LIBRTSIM_SYSTEM_HPP
