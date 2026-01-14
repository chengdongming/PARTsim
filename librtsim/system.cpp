#include <rtsim/system.hpp>
#include <rtsim/system_descriptor.hpp>

#include <rtsim/mrtkernel.hpp>

#include <rtsim/scheduler/edfsched.hpp>
#include <rtsim/scheduler/fifosched.hpp>
#include <rtsim/scheduler/rmsched.hpp>
#include <rtsim/scheduler/rrsched.hpp>

#include <metasim/factory.hpp>

namespace RTSim {
    uptr<Scheduler> make_scheduler(const std::string &name) {
        // TODO: support scheduler parameters
        auto params = std::vector<std::string>{};
        auto scheduler_ptr =
            genericFactory<Scheduler>::instance().create(name, params);
        if (!scheduler_ptr) {
            throw BaseExc("Unsupported scheduler class: " + name);
        }
        return scheduler_ptr;
    }

    uptr<CPUModel>
        make_model(const std::map<std::string, CPUMDescriptor> &models,
                   const std::vector<OPP> &opps, const std::string &name_power,
                   const std::string &name_speed) {
        auto model_power = models.find(name_power);
        if (model_power == models.cend())
            throw BaseExc("Could not find model: " + name_power);

        auto model_speed = models.find(name_speed);
        if (model_speed == models.cend())
            throw BaseExc("Could not find model: " + name_speed);

        // Last OPP is expected to be the maximum frequency
        return CPUModel::create(model_power->second, model_speed->second,
                                opps.back(), opps.back().frequency);
    }

    uptr<CPUIsland> make_island(const std::string &name,
                                const std::vector<OPP> &opps, CPUModel *model) {
        return std::make_unique<CPUIsland>(0, CPUIsland::Type::GENERIC, name,
                                           opps, model);
    }

    uptr<CPU> make_cpu(const std::string &name) {
        return std::make_unique<CPU>(name, nullptr);
    }

    uptr<RTKernel> make_kernel_global(Scheduler *scheduler,
                                      const std::string &name) {
        return std::make_unique<MRTKernel>(scheduler, std::set<CPU *>{}, name);
    }

    uptr<RTKernel> make_kernel_partitioned(Scheduler *scheduler,
                                           const std::string &name, CPU *cpu) {
        return std::make_unique<RTKernel>(scheduler, name, cpu);
    }

    // TODO: Errors are not presented in a "nice" way to the user, especially
    // when the user simply forgot to set a required attribute!
    System::System(const string &fname) {
        const SystemDescriptor sys_des{fname};

        int cnt_cpus = 0;
        int cnt_islands = 0;

        // Fill up the islands
        for (const auto &island_des : sys_des.islands) {
            sptr<CPUModel> model;
            sptr<CPUIsland> island;
            sptr<CPU> cpu;
            sptr<TracePowerConsumption> ptrace;
            sptr<Scheduler> scheduler;
            sptr<RTKernel> kernel;

            auto opps = OPP::fromVectors(island_des.volts, island_des.freqs);
            if (opps.size() < 1) {
                throw BaseExc("The specified island '" + island_des.name +
                              "' has no OPPs!");
            }

            // Each Island has its own CPU Model. Models could probably be
            // instanciated before the islands and then the islands could simply
            // look them up (for example if multiple islands reference the same
            // model, for homogeneous multiple-islands systems), since the
            // Models are now completely stateless. But oh well.

            model = make_model(sys_des.power_models, opps,
                               island_des.power_model, island_des.speed_model);
            island = make_island(island_des.name, opps, model.get());

            cpu_models.emplace_back(model);
            islands.emplace_back(island);

            const std::string basename_kernel = island_des.name + "-kernel";
            const std::string basename_cpu = island_des.name + "-";

            // For global scheduling: one single MRTKernel with one
            // Scheduler
            if (island_des.kernel.placement == "global") {
                scheduler = make_scheduler(island_des.kernel.scheduler);
                kernel = make_kernel_global(scheduler.get(), basename_kernel);
                this->schedulers.emplace_back(scheduler);
                this->kernels.emplace_back(kernel);
            }

            for (int i = 0; i < island_des.numcpus; ++i, ++cnt_cpus) {
                const std::string cpuname =
                    basename_cpu + std::to_string(cnt_cpus);

                cpu = make_cpu(cpuname);
                cpu->setIsland(island.get());
                cpu->setWorkload("idle");

                if (island_des.kernel.placement == "partitioned") {
                    // One RTKernel for each CPU, with its own scheduler
                    scheduler = make_scheduler(island_des.kernel.scheduler);
                    kernel = make_kernel_partitioned(
                        scheduler.get(),
                        basename_kernel + std::to_string(cnt_cpus), cpu.get());
                    this->schedulers.emplace_back(scheduler);
                    this->kernels.emplace_back(kernel);
                } else {
                    std::dynamic_pointer_cast<MRTKernel>(kernel)->addCPU(
                        cpu.get());
                }

                // // TODO: Power tracing output configuration/change
                // // implementation to reduce the number of events?
                // ptrace = std::make_unique<TracePowerConsumption>(
                //     cpu.get(), 1, "power_" + cpuname + ".txt");

                this->cpus.emplace_back(cpu);
                this->ptraces.emplace_back(ptrace);
            }

            size_t base_opp_idx = std::numeric_limits<size_t>::max();
            // Select the frequency from given the index
            if (island_des.base_freq > 0) {
                base_opp_idx =
                    island->getOPPIndexByFrequency(island_des.base_freq);
            }

            // If nothing is specified, the maximum frequency is used
            if (base_opp_idx >= opps.size())
                base_opp_idx = opps.size() - 1;

            island->setOPP(base_opp_idx);

            ++cnt_islands;
        }
    }

} // namespace RTSim
