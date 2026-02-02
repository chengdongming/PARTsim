#ifndef __ENERGY_INFO_PROVIDER_HPP__
#define __ENERGY_INFO_PROVIDER_HPP__

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
    };
} // namespace RTSim

#endif // __ENERGY_INFO_PROVIDER_HPP__
