#ifndef RTSIM_TASK_MODEL_VALIDATION_HPP
#define RTSIM_TASK_MODEL_VALIDATION_HPP

#include <sstream>
#include <stdexcept>
#include <string>

#include <metasim/basetype.hpp>

namespace RTSim {

class InvalidTaskModel : public std::invalid_argument {
public:
    explicit InvalidTaskModel(const std::string &message)
        : std::invalid_argument(message) {}
};

inline long parseStrictTaskInteger(const std::string &task_id,
                                   const std::string &field,
                                   const std::string &raw_value) {
    if (raw_value.empty()) {
        throw InvalidTaskModel(
            "invalid_task_model: invalid_numeric_task_field task=" +
            task_id + " field=" + field + " raw=<empty>");
    }

    std::size_t consumed = 0;
    long value = 0;
    try {
        value = std::stol(raw_value, &consumed, 10);
    } catch (const std::exception &) {
        throw InvalidTaskModel(
            "invalid_task_model: invalid_numeric_task_field task=" +
            task_id + " field=" + field + " raw=" + raw_value);
    }
    if (consumed != raw_value.size()) {
        throw InvalidTaskModel(
            "invalid_task_model: invalid_numeric_task_field task=" +
            task_id + " field=" + field + " raw=" + raw_value);
    }
    return value;
}

inline void validateConstrainedDeadlineTask(
    const std::string &task_id,
    MetaSim::Tick execution,
    MetaSim::Tick deadline,
    MetaSim::Tick period) {
    if (execution > MetaSim::Tick(0) &&
        deadline > MetaSim::Tick(0) &&
        period > MetaSim::Tick(0) &&
        execution <= deadline && deadline <= period) {
        return;
    }

    std::ostringstream message;
    message << "invalid_task_model: invalid_constrained_deadline_task"
            << " task=" << task_id
            << " C=" << execution
            << " D=" << deadline
            << " T=" << period
            << " required=0<C<=D<=T";
    throw InvalidTaskModel(message.str());
}

} // namespace RTSim

#endif // RTSIM_TASK_MODEL_VALIDATION_HPP
