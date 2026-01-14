#ifndef RTSIM_TRUEFIFO_HPP
#define RTSIM_TRUEFIFO_HPP

#include <iostream>
#include <memory>

#include <rtsim/scheduler/scheduler.hpp>

namespace RTSim {
    class TrueFIFOScheduler : public Scheduler {
        class TrueFIFOModel : public TaskModel {
        public:
            TrueFIFOModel(AbsRTTask *t) : TaskModel(t) {}
            Tick getPriority() const override {
                // All tasks have the same priority and are only ordered by
                // insertion time
                return 0;
            }

            void changePriority(MetaSim::Tick) override {
                std::cerr << "Warning! changePriority called on a TrueFIFOModel"
                          << std::endl;
            }
        };

    public:
        // void addTask(AbsRTTask *t); // throw(RTSchedExc);

        /**
           Create an FIFOModel, passing the task. It throws a
           RTSchedExc exception if the task is already present
           in this scheduler.
        */
        void addTask(AbsRTTask *t, const std::string &p) override;

        void removeTask(AbsRTTask *t) override {}

        static std::unique_ptr<TrueFIFOScheduler>
            createInstance(const std::vector<std::string> &par);
    };
} // namespace RTSim

#endif // RTSIM_TRUEFIFO_HPP
