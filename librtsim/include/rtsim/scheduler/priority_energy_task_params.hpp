#ifndef RTSIM_PRIORITY_ENERGY_TASK_PARAMS_HPP
#define RTSIM_PRIORITY_ENERGY_TASK_PARAMS_HPP

#include <string>
#include <cstdint>
#include <optional>

namespace RTSim {

double parsePriorityEnergyTaskFactor(const std::string &params);

std::optional<std::int64_t>
parseFixedPriorityRank(const std::string &params);

}  // namespace RTSim

#endif
