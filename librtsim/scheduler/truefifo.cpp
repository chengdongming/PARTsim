#include <metasim/factory.hpp>

#include <rtsim/scheduler/truefifo.hpp>

namespace RTSim {
    void TrueFIFOScheduler::addTask(AbsRTTask *task, const std::string &p) {
        // Ignoring Parameters
        enqueueModel(new TrueFIFOModel(task));
    }

    std::unique_ptr<TrueFIFOScheduler>
        TrueFIFOScheduler::createInstance(const std::vector<std::string> &par) {
        // todo: check the parameters (i.e. to set the default
        // time quantum)
        return std::make_unique<TrueFIFOScheduler>();
    }

} // namespace RTSim
