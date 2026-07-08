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

        // ⭐ V58新增：强制记录dline_miss事件（用于Early Abort场景）
        // 当调度器因能量耗尽等原因主动kill任务时，
        // DeadEvt可能被drop掉不会触发，需要主动注入dline_miss记录
        virtual void logDlineMiss(AbsRTTask *task, const std::string &reason = "early_abort") {
            (void)task;  // 默认实现：什么都不做
            (void)reason;
        }

        // Early Abort专用：若任务先suspend，需要在descheduled之后补记dline_miss
        virtual void logDlineMissAfterDesched(AbsRTTask *task, const std::string &reason = "early_abort") {
            (void)task;
            (void)reason;
        }

        // ⭐ V58新增：设置JSONTrace指针，用于Early Abort时注入dline_miss记录
        // 子类重写此方法，将JSONTrace指针注册到调度器
        virtual void setTraceLogger(void *trace) {
            (void)trace;  // 默认实现：什么都不做
        }

        // 可选语义调度决策 trace；默认关闭，且默认实现不影响调度器。
        virtual void setSemanticTraceEnabled(bool enabled) {
            (void)enabled;
        }
    };
} // namespace RTSim

#endif // __ENERGY_INFO_PROVIDER_HPP__
