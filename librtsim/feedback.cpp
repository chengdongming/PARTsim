#include <rtsim/feedback.hpp>
#include <rtsim/task.hpp>

namespace RTSim {

    AbstractFeedbackModule::AbstractFeedbackModule(const std::string &name) :
        Entity(name),
        task(0) {}

    AbstractFeedbackModule::~AbstractFeedbackModule() {}

    void AbstractFeedbackModule::setTask(Task *t) {
        task = t;
    }

} // namespace RTSim
