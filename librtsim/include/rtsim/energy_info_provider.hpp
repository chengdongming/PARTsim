#ifndef __ENERGY_INFO_PROVIDER_HPP__
#define __ENERGY_INFO_PROVIDER_HPP__

#include <string>

namespace RTSim {
    class AbsRTTask;

    // ⭐ 能量信息接口 - 用于从调度器获取能量数据
    class EnergyInfoProvider {
    public:
        virtual ~EnergyInfoProvider() = default;
        virtual double getCurrentEnergy() const = 0;
        virtual double getTotalEnergyConsumed() const = 0;
        virtual double getTotalEnergyHarvested() const = 0;
        virtual double getTaskUnitEnergy(AbsRTTask *task) const = 0;
        virtual double getTaskTotalEnergy(AbsRTTask *task) const = 0;

        // ⭐ V115：获取任务被挂起的真正原因（消灭幽灵抢占）
        virtual std::string getSuspendReason(AbsRTTask *task) const {
            (void)task;  // 默认实现
            return "unknown";
        }
        virtual void clearSuspendReason(AbsRTTask *task) {
            (void)task;  // 默认实现：什么都不做
        }
    };
} // namespace RTSim

#endif // __ENERGY_INFO_PROVIDER_HPP__
